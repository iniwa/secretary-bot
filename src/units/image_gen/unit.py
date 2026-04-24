"""ImageGenUnit — ジョブ受付・状態参照・キャンセル・イベント pub/sub。

WebGUI からは enqueue / get_job / list_jobs / list_gallery / cancel_job /
subscribe_events / unsubscribe_events を直接呼ぶ。
Discord 連携は execute() + Discord 向け完了通知タスク。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import discord

from src.errors import ValidationError
from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit
from src.units.image_gen.dispatcher import Dispatcher
from src.units.image_gen.models import (
    DEFAULT_PARAMS,
    PLATFORM_DISCORD,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    JobStatus,
    TransitionEvent,
)
from src.units.image_gen.section_mgr import SectionManager
from src.units.image_gen.workflow_mgr import WorkflowManager

log = get_logger(__name__)


_EXTRACT_PROMPT = """\
あなたは画像生成ユニットの意図抽出アシスタント。以下のユーザー入力を分析し JSON で返してください。

## アクション一覧
- generate: 新しく画像を生成（positive が必要、negative は任意）
- status:   ジョブの状態を確認（job_id が必要。省略時は最新の自分のジョブ）
- cancel:   ジョブをキャンセル（job_id が必要）
- list:     自分の最近のジョブ一覧

## 補助パラメータ（generate のときだけ任意）
- preset: プリセット名（workflows.name）。省略時は既定プリセット
- width / height / steps / cfg: 数値で上書き

## 出力形式（厳守）
{{"action": "...", "positive": "...", "negative": "...", "preset": "...", "job_id": "...", "width": 0, "height": 0, "steps": 0, "cfg": 0.0}}

- 不要なフィールドは省略。
- JSON 1 個だけ。他テキストは禁止。

## ユーザー入力
{user_input}
"""


class ImageGenUnit(BaseUnit):
    UNIT_NAME = "image_gen"
    UNIT_DESCRIPTION = "ComfyUI による画像生成。プリセット + プロンプトでジョブ投入。"
    DELEGATE_TO = None            # Pi 内完結（Agent 呼び出しは Dispatcher 経由）
    CHAT_ROUTABLE = False         # WebGUI 専用。チャット経由では呼ばない。
    AUTONOMY_TIER = 4             # Phase5 で調整
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        self.workflow_mgr = WorkflowManager(bot)
        self.section_mgr = SectionManager(bot)
        self.dispatcher = Dispatcher(bot, self)
        self._event_subscribers: set[asyncio.Queue] = set()
        self._started = False
        # Discord 通知用: Discord 経由の job_id → {"channel_id", "user_id", "message_id"}
        self._discord_jobs: dict[str, dict] = {}
        self._discord_notifier_task: asyncio.Task | None = None

    # --- lifecycle ---

    async def on_ready_hook(self) -> None:
        """bot.on_ready 相当のタイミングで呼ばれる想定。

        Phase1 では bot 側に明示 hook が無いので、UnitManager 起動直後に
        bot.py から呼び出すか、on_heartbeat の初回で起動する。
        """
        if self._started:
            return
        self._started = True
        try:
            await self.workflow_mgr.sync_presets_to_db()
        except Exception as e:
            log.warning("preset sync failed: %s", e)
        try:
            await self.section_mgr.sync_presets_to_db()
        except Exception as e:
            log.warning("section preset sync failed: %s", e)
        await self.dispatcher.start()

    async def on_heartbeat(self) -> None:
        if not self._started:
            await self.on_ready_hook()

    async def cog_load(self) -> None:   # discord.py hook: unit ロード直後
        # heartbeat (15分) を待たずに Dispatcher を起動する。
        asyncio.create_task(self.on_ready_hook())
        # Discord 通知用イベント購読タスクを起動
        if self._discord_notifier_task is None or self._discord_notifier_task.done():
            self._discord_notifier_task = asyncio.create_task(
                self._discord_notifier_loop(), name="img_discord_notifier",
            )

    async def cog_unload(self) -> None:   # discord.py hook
        await self.dispatcher.stop()
        if self._discord_notifier_task and not self._discord_notifier_task.done():
            self._discord_notifier_task.cancel()
            try:
                await self._discord_notifier_task
            except asyncio.CancelledError:
                pass

    # --- Discord 連携 ---

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)

        message = parsed.get("message", "")
        user_id = parsed.get("user_id", "")
        channel = parsed.get("channel", "")
        try:
            extracted = await self._extract_params(message, channel)
            action = (extracted.get("action") or "generate").lower()
            if action == "status":
                result = await self._discord_status(extracted, user_id)
            elif action == "cancel":
                result = await self._discord_cancel(extracted, user_id)
            elif action == "list":
                result = await self._discord_list(user_id)
            else:
                result = await self._discord_generate(ctx, parsed, extracted, user_id)
            self.session_done = True
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": action}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        return await self.llm.extract_json(prompt)

    async def _discord_generate(
        self, ctx, parsed: dict, extracted: dict, user_id: str,
    ) -> str:
        positive = (extracted.get("positive") or "").strip()
        negative = (extracted.get("negative") or "").strip() or None

        # positive が空なら prompt_crafter のアクティブセッションを流用
        if not positive:
            crafter = None
            um = getattr(self.bot, "unit_manager", None)
            if um is not None:
                crafter = um.get("prompt_crafter")
            if crafter is not None and hasattr(crafter, "get_active_prompt"):
                try:
                    sess = await crafter.get_active_prompt(user_id, PLATFORM_DISCORD)
                except Exception as e:
                    log.warning("prompt_crafter lookup failed: %s", e)
                    sess = None
                if sess and sess.get("positive"):
                    positive = sess["positive"]
                    if not negative and sess.get("negative"):
                        negative = sess["negative"]

        if not positive:
            # 最後の砦: ユーザー入力そのものをプロンプトに流用
            positive = (parsed.get("message") or "").strip()
        if not positive:
            return "画像生成するプロンプトを教えてください。"

        ig_cfg = (self.bot.config.get("units") or {}).get("image_gen") or {}
        preset = (extracted.get("preset") or "").strip() or ig_cfg.get("default_preset", "t2i_default")

        params: dict[str, Any] = {}
        for key_in, key_out in [("width", "WIDTH"), ("height", "HEIGHT"),
                                 ("steps", "STEPS"), ("cfg", "CFG")]:
            v = extracted.get(key_in)
            if v is None:
                continue
            try:
                params[key_out] = int(v) if key_out != "CFG" else float(v)
            except (TypeError, ValueError):
                continue

        job_id = await self.enqueue(
            user_id=user_id, platform=PLATFORM_DISCORD,
            workflow_name=preset, positive=positive, negative=negative,
            params=params or None,
        )

        # Discord 通知用にチャンネルを記録
        ch_id = getattr(getattr(ctx, "channel", None), "id", 0)
        msg_id = getattr(getattr(ctx, "message", None), "id", 0)
        self._discord_jobs[job_id] = {
            "channel_id": int(ch_id) if ch_id else 0,
            "user_id": user_id,
            "message_id": int(msg_id) if msg_id else 0,
        }
        return f"🎨 受付ました（job_id={job_id[:8]}, preset={preset}）。完了したらここに返します。"

    async def _discord_status(self, extracted: dict, user_id: str) -> str:
        job_id = str(extracted.get("job_id") or "").strip()
        if not job_id and user_id:
            rows = await self.bot.database.generation_job_list(
                user_id=user_id, modality="image", limit=1,
            )
            if rows:
                job_id = rows[0]["id"]
        if not job_id:
            return "確認対象のジョブが見つかりません。"
        job = await self.get_job(job_id)
        if not job:
            return f"job_id={job_id[:8]} が見つかりません。"
        msg = f"job_id={job_id[:8]}: {job['status']} ({job.get('progress', 0)}%)"
        if job.get("last_error"):
            msg += f"\nerror: {job['last_error']}"
        return msg

    async def _discord_cancel(self, extracted: dict, user_id: str) -> str:
        job_id = str(extracted.get("job_id") or "").strip()
        if not job_id:
            return "キャンセルする job_id を教えてください。"
        ok = await self.cancel_job(job_id)
        if ok:
            return f"🛑 job_id={job_id[:8]} をキャンセルしました。"
        return f"job_id={job_id[:8]} はキャンセルできませんでした（存在しないか既に終端）。"

    async def _discord_list(self, user_id: str) -> str:
        rows = await self.list_jobs(user_id=user_id or None, limit=5)
        if not rows:
            return "最近のジョブはありません。"
        lines = ["🖼️ 最近のジョブ"]
        for r in rows:
            short = r["job_id"][:8]
            lines.append(
                f"- {short}  {r['status']}  ({r.get('progress', 0)}%)"
                f"  {r.get('workflow_name') or ''}"
            )
        return "\n".join(lines)

    async def _discord_notifier_loop(self) -> None:
        """TransitionEvent を購読し、Discord 発のジョブが終端に達したら通知する。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subscribe_events(q)
        try:
            while True:
                ev = await q.get()
                job_id = ev.get("job_id", "")
                status = ev.get("status", "")
                meta = self._discord_jobs.get(job_id)
                if not meta:
                    continue
                if status not in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
                    continue
                try:
                    await self._post_discord_result(job_id, status, meta, ev)
                except Exception as e:
                    log.warning("discord notify failed for %s: %s", job_id, e)
                finally:
                    self._discord_jobs.pop(job_id, None)
        except asyncio.CancelledError:
            raise
        finally:
            self.unsubscribe_events(q)

    async def _post_discord_result(
        self, job_id: str, status: str, meta: dict, ev: dict,
    ) -> None:
        ig_cfg = (self.bot.config.get("units") or {}).get("image_gen") or {}
        output_ch = int(ig_cfg.get("discord_output_channel_id") or 0)
        ch_id = output_ch or meta.get("channel_id") or 0
        channel = self.bot.get_channel(int(ch_id)) if ch_id else None
        if channel is None:
            # フォールバック: 管理チャンネル
            channel = self.bot.get_channel(self._admin_channel_id)
        if channel is None:
            log.info("discord notify: no destination channel for %s", job_id)
            return

        uid = meta.get("user_id") or ""
        prefix = f"<@{uid}> " if uid and uid != "webgui" else ""
        short = job_id[:8]

        if status == STATUS_DONE:
            job = await self.get_job(job_id)
            result_paths: list[str] = (job or {}).get("result_paths") or []
            result_kinds: list[str] = (job or {}).get("result_kinds") or []
            if len(result_kinds) != len(result_paths):
                result_kinds = ["image"] * len(result_paths)
            # image のみ添付、video/audio はリンクテキストのみ
            files: list[discord.File] = []
            linked: list[str] = []
            for p, kind in list(zip(result_paths, result_kinds, strict=False))[:4]:
                if kind != "image":
                    linked.append(f"[{kind}] {p}")
                    continue
                try:
                    if os.path.exists(p):
                        files.append(discord.File(p))
                    else:
                        log.info("discord notify: file not readable %s", p)
                        linked.append(p)
                except Exception as e:
                    log.warning("discord File open failed %s: %s", p, e)
                    linked.append(p)
            text = f"{prefix}✅ 生成完了 (job_id={short})"
            if linked:
                text += "\n" + "\n".join(linked)
            elif not files:
                text += f"\n結果ファイル: {', '.join(result_paths) or '(なし)'}"
            await channel.send(text, files=files or None)
        elif status == STATUS_FAILED:
            detail = (ev.get("detail") or {}).get("message", "")
            await channel.send(f"{prefix}❌ 生成失敗 (job_id={short}) {detail}")
        elif status == STATUS_CANCELLED:
            await channel.send(f"{prefix}🛑 生成キャンセル (job_id={short})")

    # === WebGUI から呼ばれる外部公開インターフェース ===

    async def enqueue(
        self, user_id: str, platform: str, workflow_name: str,
        positive: str | None, negative: str | None,
        params: dict[str, Any] | None = None,
        *,
        section_ids: list[int] | None = None,
        user_position: str = "tail",
        modality: str | None = None,
        lora_overrides: list[dict] | None = None,
        is_nsfw: bool = False,
    ) -> str:
        """ジョブを登録し、job_id (UUID hex) を返す。

        - workflow_name: workflows.name
        - positive / negative: ユーザー入力プロンプト（セクション指定時は追加分）
        - params: ワークフローのパラメータ（WIDTH/HEIGHT/STEPS/CFG/SEED/...）
        - section_ids: 合成するセクション（prompt_sections.id）の列。None なら従来通り
        - user_position: 'head' | 'tail' | 'section:<category_key>'
        - modality: 'image' | 'video' | 'audio'。省略時は Workflow.category から推定
        """
        if not workflow_name:
            raise ValidationError("workflow_name is required")
        wf = await self.bot.database.workflow_get_by_name(workflow_name)
        if not wf:
            raise ValidationError(f"Workflow '{workflow_name}' not found")

        merged = dict(DEFAULT_PARAMS)
        if params:
            for k, v in params.items():
                if v is None:
                    continue
                merged[str(k).upper()] = v
        # LoRA オーバーライドは予約キーで params に埋めて DB 保存・再現性を担保。
        # 値が空の場合は埋めない（プレースホルダ未設定として扱う）。
        if lora_overrides:
            cleaned = [
                {
                    "node_id": str(o.get("node_id") or ""),
                    "enabled": bool(o.get("enabled", True)),
                    "strength": (
                        float(o["strength"]) if o.get("strength") is not None else None
                    ),
                }
                for o in lora_overrides
                if isinstance(o, dict) and o.get("node_id")
            ]
            if cleaned:
                merged["__LORA_OVERRIDES__"] = cleaned
        # preset 共通の placeholder はユーザー未指定なら config の既定で補完する。
        ig_cfg = (self.bot.config.get("units") or {}).get("image_gen") or {}
        if "CKPT" not in merged:
            default_ckpt = ig_cfg.get("default_base_model")
            if not default_ckpt:
                lt_cfg = (self.bot.config.get("units") or {}).get("lora_train") or {}
                default_ckpt = lt_cfg.get("default_base_model")
            if default_ckpt:
                merged["CKPT"] = default_ckpt

        # --- セクション合成（指定があれば positive/negative を確定） ---
        sections_json: str | None = None
        if section_ids:
            from src.units.image_gen.section_composer import compose_prompt
            section_rows = await self.bot.database.section_get_many(section_ids)
            composed = compose_prompt(
                section_rows,
                user_positive=positive,
                user_negative=negative,
                user_position=user_position,
            )
            positive = composed.positive
            negative = composed.negative
            sections_json = json.dumps({
                "section_ids": list(section_ids),
                "user_position": user_position,
                "warnings": composed.warnings,
                "dropped": composed.dropped,
            }, ensure_ascii=False)
            if composed.warnings:
                log.warning("section compose warnings: %s", composed.warnings)

        # modality は Workflow.category から推定（明示指定があれば優先）
        from src.units.image_gen.modality import category_to_modality, normalize_modality
        resolved_modality = (
            normalize_modality(modality) if modality
            else category_to_modality(wf.get("category"))
        )

        job_id = await self.bot.database.generation_job_insert(
            user_id=user_id, platform=platform,
            workflow_id=int(wf["id"]),
            positive=positive, negative=negative,
            params_json=json.dumps(merged, ensure_ascii=False),
            modality=resolved_modality,
            sections_json=sections_json,
            priority=int(merged.get("PRIORITY", 0)) if "PRIORITY" in merged else 0,
            is_nsfw=is_nsfw,
        )
        log.info("generation_job enqueued: id=%s user=%s workflow=%s modality=%s sections=%s",
                 job_id, user_id, workflow_name, resolved_modality,
                 section_ids or None)
        # 即 Dispatcher を起こす
        self.dispatcher.wake()
        # 購読者にも投入通知
        await self.broadcast_event(TransitionEvent(
            job_id=job_id, from_status=None, to_status="queued",
            event="status", detail={"workflow": workflow_name},
        ))
        return job_id

    async def get_job(self, job_id: str) -> dict | None:
        """ジョブの現在状態を dict で返す。"""
        row = await self.bot.database.generation_job_get(job_id)
        if not row:
            return None
        return await self._row_to_dict(row)

    async def list_jobs(
        self, user_id: str | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        # Jobs タブ・Discord の「最近のジョブ」用。created_at 降順にすることで
        # queued/running（finished_at=NULL）が完了ジョブに押し出されないようにする。
        rows = await self.bot.database.generation_job_list(
            user_id=user_id, status=status, modality="image",
            limit=limit, offset=offset, order="created_desc",
        )
        return [await self._row_to_dict(r) for r in rows]

    async def list_gallery(
        self, limit: int = 50, offset: int = 0,
        favorite_only: bool = False, tag: str | None = None,
        nsfw: bool | None = False,
        q: str | None = None,
        tags_all: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        workflow_name: str | None = None,
        collection_id: int | None = None,
        order: str = "new",
    ) -> list[dict]:
        """完了ジョブの result_paths を列挙する。

        favorite_only=True で ⭐ のみ、tag/tags_all で絞り込み、q は prompt 検索。
        nsfw=False (既定) で SFW のみ、True で NSFW のみ、None で両方。
        """
        # 後方互換: tag 単数 → tags_all へ合流
        if tag and not tags_all:
            tags_all = [tag]
        rows = await self.bot.database.generation_job_list(
            status=STATUS_DONE, modality="image", limit=limit, offset=offset,
            nsfw=nsfw, favorite_only=favorite_only,
            q=q, tags_all=tags_all,
            date_from=date_from, date_to=date_to,
            workflow_name=workflow_name,
            collection_id=collection_id,
            order=order,
        )
        out: list[dict] = []
        for r in rows:
            paths: list[str] = []
            try:
                paths = json.loads(r.get("result_paths") or "[]")
            except Exception:
                paths = []
            if not paths:
                continue
            kinds: list[str] = []
            try:
                kinds = json.loads(r.get("result_kinds") or "[]")
            except Exception:
                kinds = []
            if len(kinds) != len(paths):
                kinds = ["image"] * len(paths)
            tags: list[str] = []
            try:
                tags = json.loads(r.get("tags") or "[]") or []
            except Exception:
                tags = []
            favorite = bool(r.get("favorite"))
            out.append({
                "job_id": r["id"],
                "user_id": r["user_id"],
                "finished_at": r.get("finished_at"),
                "workflow_id": r.get("workflow_id"),
                "result_paths": paths,
                "result_kinds": kinds,
                "positive": r.get("positive"),
                "negative": r.get("negative"),
                "favorite": favorite,
                "tags": tags,
                "is_nsfw": bool(r.get("is_nsfw")),
            })
        return out

    async def cancel_job(self, job_id: str) -> bool:
        """非終端ジョブを cancelled へ。Agent 側にも interrupt を試みる。"""
        row = await self.bot.database.generation_job_get(job_id)
        if not row:
            return False
        # Agent 側キャンセルは best-effort
        agent_id = row.get("assigned_agent")
        if agent_id:
            agent = self._find_agent(agent_id)
            if agent:
                try:
                    from src.units.image_gen.agent_client import AgentClient
                    ac = AgentClient(agent)
                    try:
                        if row["status"] == "warming_cache" and row.get("cache_sync_id"):
                            await ac.cache_sync_cancel(row["cache_sync_id"])
                        elif row["status"] == "running":
                            await ac.generation_job_cancel(job_id)
                    finally:
                        await ac.close()
                except Exception as e:
                    log.warning("agent cancel best-effort failed: %s", e)
        ok = await self.bot.database.generation_job_cancel(job_id)
        if ok:
            await self.broadcast_event(TransitionEvent(
                job_id=job_id, from_status=row["status"],
                to_status="cancelled", event="status",
            ))
        return ok

    # --- event pub/sub ---

    def subscribe_events(self, queue: asyncio.Queue) -> None:
        """WebGUI SSE から購読キューを登録する。"""
        self._event_subscribers.add(queue)

    def unsubscribe_events(self, queue: asyncio.Queue) -> None:
        self._event_subscribers.discard(queue)

    async def broadcast_event(self, ev: TransitionEvent) -> None:
        """Dispatcher / enqueue 等から呼び出されるイベント配信。"""
        payload = {
            "job_id": ev.job_id,
            "status": ev.to_status,
            "from_status": ev.from_status,
            "progress": ev.progress,
            "event": ev.event,
            "agent_id": ev.agent_id,
            "detail": ev.detail,
        }
        dead: list[asyncio.Queue] = []
        for q in list(self._event_subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
            except Exception:
                dead.append(q)
        for q in dead:
            self._event_subscribers.discard(q)

    # --- helpers ---

    async def _row_to_dict(self, row: dict) -> dict:
        wf_name: str | None = None
        if row.get("workflow_id"):
            wf = await self.bot.database.workflow_get(int(row["workflow_id"]))
            if wf:
                wf_name = wf["name"]
        params: dict[str, Any] = {}
        try:
            params = json.loads(row.get("params_json") or "{}")
        except Exception:
            params = {}
        result_paths: list[str] = []
        try:
            result_paths = json.loads(row.get("result_paths") or "[]")
        except Exception:
            result_paths = []
        result_kinds: list[str] = []
        try:
            result_kinds = json.loads(row.get("result_kinds") or "[]")
        except Exception:
            result_kinds = []
        if len(result_kinds) != len(result_paths):
            result_kinds = ["image"] * len(result_paths)
        js = JobStatus(
            job_id=row["id"],
            user_id=row.get("user_id", ""),
            platform=row.get("platform", ""),
            workflow_id=row.get("workflow_id"),
            workflow_name=wf_name,
            status=row.get("status", ""),
            progress=int(row.get("progress") or 0),
            assigned_agent=row.get("assigned_agent"),
            positive=row.get("positive"),
            negative=row.get("negative"),
            params=params,
            result_paths=result_paths,
            result_kinds=result_kinds,
            modality=str(row.get("modality") or "image"),
            last_error=row.get("last_error"),
            retry_count=int(row.get("retry_count") or 0),
            max_retries=int(row.get("max_retries") or 0),
            created_at=row.get("created_at"),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
        )
        out = js.to_dict()
        out["favorite"] = bool(row.get("favorite"))
        out["is_nsfw"] = bool(row.get("is_nsfw"))
        try:
            out["tags"] = json.loads(row.get("tags") or "[]") or []
        except Exception:
            out["tags"] = []
        return out

    def _find_agent(self, agent_id: str) -> dict | None:
        for a in getattr(self.bot.unit_manager.agent_pool, "_agents", []):
            if a.get("id") == agent_id:
                return a
        return None
