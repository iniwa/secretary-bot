"""ClipPipelineUnit — 切り抜きジョブの受付・状態参照・キャンセル・イベント pub/sub。

WebGUI からは `enqueue` / `get_job` / `list_jobs` / `cancel_job` /
`subscribe_events` / `unsubscribe_events` を直接呼ぶ。
Discord 連携は ``execute()`` + Discord 向け完了通知タスク。

image_gen のパターンに合わせて ``DELEGATE_TO=None`` で Pi 内完結。Agent 呼び出しは
Dispatcher 経由でのみ行う（cancel_job の best-effort キャンセルは直接 AgentClient を
1 回使うが、ユニット間境界は Dispatcher に閉じている）。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from src.errors import ValidationError
from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit
from src.units.clip_pipeline.agent_client import AgentClient
from src.units.clip_pipeline.dispatcher import Dispatcher
from src.units.clip_pipeline.models import (
    DEFAULT_PARAMS,
    MODE_NORMAL,
    MODE_TEST,
    PLATFORM_DISCORD,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    TransitionEvent,
)

log = get_logger(__name__)


_EXTRACT_PROMPT = """\
あなたは配信アーカイブの自動切り抜きユニットの意図抽出アシスタント。
以下のユーザー入力を分析し JSON で返してください。

## アクション一覧
- enqueue: 新規ジョブを投入（video_path が必要）
- status:  ジョブの状態を確認（job_id が必要。省略時は最新の自分のジョブ）
- cancel:  ジョブをキャンセル（job_id が必要）
- list:    自分の最近のジョブ一覧

## 補助パラメータ（enqueue のときだけ）
- video_path: 動画ファイルの絶対パス（NAS UNC / ローカル SSD の絶対パス）
- mode: "test" (先頭 3 分のみ) / "normal" (全尺)。省略時は normal
- whisper_model: whisper モデル名（例: "large-v3"）
- ollama_model:  Ollama モデル名（例: "qwen3:14b"）
- top_n / min_clip_sec / max_clip_sec: 整数
- do_export_clips: bool
- use_demucs: bool
- mic_track: 0 or 1

## 出力形式（厳守）
{{"action": "...", "video_path": "...", "mode": "...", "whisper_model": "...", "ollama_model": "...", "top_n": 0, "min_clip_sec": 0, "max_clip_sec": 0, "do_export_clips": false, "use_demucs": false, "mic_track": 1, "job_id": "..."}}

- 不要なフィールドは省略。
- JSON 1 個だけ。他テキストは禁止。

## ユーザー入力
{user_input}
"""


class ClipPipelineUnit(BaseUnit):
    UNIT_NAME = "clip_pipeline"
    UNIT_DESCRIPTION = (
        "配信アーカイブから自動で切り抜き候補を作るユニット。"
        "「切り抜き D:\\videos\\stream_01.mp4」などで呼び出し、Whisper + Ollama が"
        "ハイライトを抽出して EDL / MP4 を出力する。"
    )
    DELEGATE_TO = None
    AUTONOMY_TIER = 4
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        self.dispatcher = Dispatcher(bot, self)
        self._event_subscribers: set[asyncio.Queue] = set()
        self._started = False
        self._discord_jobs: dict[str, dict] = {}
        self._discord_notifier_task: asyncio.Task | None = None

    # --- lifecycle ---

    async def on_ready_hook(self) -> None:
        if self._started:
            return
        self._started = True
        await self.dispatcher.start()

    async def on_heartbeat(self) -> None:
        if not self._started:
            await self.on_ready_hook()

    async def cog_load(self) -> None:
        asyncio.create_task(self.on_ready_hook())
        if self._discord_notifier_task is None or self._discord_notifier_task.done():
            self._discord_notifier_task = asyncio.create_task(
                self._discord_notifier_loop(),
                name="clip_discord_notifier",
            )

    async def cog_unload(self) -> None:
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
            action = (extracted.get("action") or "enqueue").lower()
            if action == "status":
                result = await self._discord_status(extracted, user_id)
            elif action == "cancel":
                result = await self._discord_cancel(extracted, user_id)
            elif action == "list":
                result = await self._discord_list(user_id)
            else:
                result = await self._discord_enqueue(ctx, extracted, user_id)
            self.session_done = True
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": action}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    async def _discord_enqueue(self, ctx, extracted: dict, user_id: str) -> str:
        video_path = str(extracted.get("video_path") or "").strip()
        if not video_path:
            return "切り抜きを実行する動画のパスを指定してください。例: 「切り抜き D:\\videos\\stream_01.mp4」"

        cfg = self._clip_cfg()
        mode = str(extracted.get("mode") or cfg.get("default_mode") or MODE_NORMAL).lower()
        if mode not in (MODE_NORMAL, MODE_TEST):
            mode = MODE_NORMAL
        whisper_model = str(
            extracted.get("whisper_model")
            or cfg.get("default_whisper_model")
            or "large-v3"
        )
        ollama_model = str(
            extracted.get("ollama_model")
            or cfg.get("default_ollama_model")
            or "qwen3:14b"
        )

        params = dict(DEFAULT_PARAMS)
        for k in ("top_n", "min_clip_sec", "max_clip_sec",
                  "do_export_clips", "use_demucs", "mic_track",
                  "sleep_sec"):
            if k in extracted and extracted[k] is not None:
                params[k] = extracted[k]
        # config defaults
        for k, v in (cfg.get("defaults") or {}).items():
            params.setdefault(k, v)

        try:
            job_id = await self.enqueue(
                user_id=user_id, platform=PLATFORM_DISCORD,
                video_path=video_path,
                mode=mode,
                whisper_model=whisper_model,
                ollama_model=ollama_model,
                params=params,
            )
        except ValidationError as e:
            return f"❌ 受付失敗: {e}"

        ch_id = getattr(getattr(ctx, "channel", None), "id", 0)
        msg_id = getattr(getattr(ctx, "message", None), "id", 0)
        self._discord_jobs[job_id] = {
            "channel_id": int(ch_id) if ch_id else 0,
            "user_id": user_id,
            "message_id": int(msg_id) if msg_id else 0,
        }
        return (
            f"✂️ 受付ました（job_id={job_id[:8]}, mode={mode}, whisper={whisper_model}）。"
            "完了したらここに結果を返します。"
        )

    async def _discord_status(self, extracted: dict, user_id: str) -> str:
        job_id = str(extracted.get("job_id") or "").strip()
        if not job_id and user_id:
            rows = await self.bot.database.clip_pipeline_job_list(
                user_id=user_id, limit=1,
            )
            if rows:
                job_id = rows[0]["id"]
        if not job_id:
            return "確認対象のジョブが見つかりません。"
        job = await self.get_job(job_id)
        if not job:
            return f"job_id={job_id[:8]} が見つかりません。"
        step = job.get("step") or "-"
        msg = f"job_id={job_id[:8]}: {job['status']} / step={step} ({job.get('progress', 0)}%)"
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
        lines = ["✂️ 最近の切り抜きジョブ"]
        for r in rows:
            short = r["job_id"][:8]
            step = r.get("step") or "-"
            lines.append(
                f"- {short}  {r['status']}  step={step}  ({r.get('progress', 0)}%)"
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
                    log.warning("clip discord notify failed for %s: %s", job_id, e)
                finally:
                    self._discord_jobs.pop(job_id, None)
        except asyncio.CancelledError:
            raise
        finally:
            self.unsubscribe_events(q)

    async def _post_discord_result(
        self, job_id: str, status: str, meta: dict, ev: dict,
    ) -> None:
        cfg = self._clip_cfg()
        output_ch = int(cfg.get("discord_output_channel_id") or 0)
        ch_id = output_ch or meta.get("channel_id") or 0
        channel = self.bot.get_channel(int(ch_id)) if ch_id else None
        if channel is None:
            channel = self.bot.get_channel(self._admin_channel_id)
        if channel is None:
            log.info("clip discord notify: no destination channel for %s", job_id)
            return

        uid = meta.get("user_id") or ""
        prefix = f"<@{uid}> " if uid and uid != "webgui" else ""
        short = job_id[:8]

        if status == STATUS_DONE:
            job = await self.get_job(job_id)
            result = (job or {}).get("result") or {}
            edl = result.get("edl_path", "")
            hi = result.get("highlights_count", "?")
            clips = result.get("clip_paths") or []
            text = f"{prefix}✅ 切り抜き完了 (job_id={short}) ハイライト {hi} 件"
            if edl:
                text += f"\nEDL: {edl}"
            if clips:
                text += f"\nMP4 {len(clips)} 本"
            await channel.send(text)
        elif status == STATUS_FAILED:
            detail = (ev.get("detail") or {}).get("message", "")
            await channel.send(f"{prefix}❌ 切り抜き失敗 (job_id={short}) {detail}")
        elif status == STATUS_CANCELLED:
            await channel.send(f"{prefix}🛑 切り抜きキャンセル (job_id={short})")

    # === WebGUI から呼ばれる外部公開インターフェース ===

    async def enqueue(
        self, *, user_id: str, platform: str,
        video_path: str,
        mode: str = MODE_NORMAL,
        whisper_model: str = "",
        ollama_model: str = "",
        params: dict[str, Any] | None = None,
        output_dir: str | None = None,
        max_retries: int | None = None,
    ) -> str:
        """ジョブを登録し、job_id (UUID hex) を返す。

        - video_path: Agent から見える絶対パス（NAS UNC / Windows ローカル）
        - output_dir: 指定なしなら config の NAS base + 動画ベース名で自動算出
        """
        if not video_path:
            raise ValidationError("video_path is required")
        cfg = self._clip_cfg()
        if not whisper_model:
            whisper_model = cfg.get("default_whisper_model") or "large-v3"
        if not ollama_model:
            ollama_model = cfg.get("default_ollama_model") or "qwen3:14b"
        if mode not in (MODE_NORMAL, MODE_TEST):
            mode = MODE_NORMAL

        # output_dir: config.units.clip_pipeline.nas.base_path + outputs_subdir + 動画ベース名
        if not output_dir:
            output_dir = self._default_output_dir_for(video_path)

        merged = dict(DEFAULT_PARAMS)
        if params:
            for k, v in params.items():
                if v is not None:
                    merged[k] = v

        if max_retries is None:
            max_retries = int((cfg.get("retry") or {}).get("max_retries", 2))

        job_id = await self.bot.database.clip_pipeline_job_insert(
            user_id=user_id, platform=platform,
            video_path=video_path, output_dir=output_dir,
            mode=mode, whisper_model=whisper_model, ollama_model=ollama_model,
            params_json=json.dumps(merged, ensure_ascii=False),
            max_retries=max_retries,
        )
        log.info(
            "clip job enqueued: id=%s user=%s video=%s mode=%s whisper=%s",
            job_id, user_id, video_path, mode, whisper_model,
        )
        self.dispatcher.wake()
        await self.broadcast_event(TransitionEvent(
            job_id=job_id, from_status=None, to_status="queued",
            event="status",
            detail={"video_path": video_path, "mode": mode,
                    "whisper_model": whisper_model},
        ))
        return job_id

    async def get_job(self, job_id: str) -> dict | None:
        row = await self.bot.database.clip_pipeline_job_get(job_id)
        if not row:
            return None
        return self._row_to_dict(row)

    async def list_jobs(
        self, user_id: str | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        rows = await self.bot.database.clip_pipeline_job_list(
            user_id=user_id, status=status, limit=limit, offset=offset,
        )
        return [self._row_to_dict(r) for r in rows]

    async def cancel_job(self, job_id: str) -> bool:
        """非終端ジョブを cancelled へ。Agent 側にも cancel を試みる（best-effort）。"""
        row = await self.bot.database.clip_pipeline_job_get(job_id)
        if not row:
            return False
        agent_id = row.get("assigned_agent")
        if agent_id:
            agent = self._find_agent(agent_id)
            if agent:
                try:
                    ac = AgentClient(agent)
                    try:
                        await ac.job_cancel(job_id)
                    finally:
                        await ac.close()
                except Exception as e:
                    log.warning(
                        "clip agent cancel best-effort failed for %s: %s",
                        job_id, e,
                    )
        ok = await self.bot.database.clip_pipeline_job_cancel(job_id)
        if ok:
            await self.broadcast_event(TransitionEvent(
                job_id=job_id, from_status=row["status"],
                to_status=STATUS_CANCELLED, event="status",
            ))
        return ok

    # --- イベント pub/sub ---

    def subscribe_events(self, queue: asyncio.Queue) -> None:
        self._event_subscribers.add(queue)

    def unsubscribe_events(self, queue: asyncio.Queue) -> None:
        self._event_subscribers.discard(queue)

    async def broadcast_event(self, ev: TransitionEvent) -> None:
        payload = {
            "job_id": ev.job_id,
            "status": ev.to_status,
            "from_status": ev.from_status,
            "progress": ev.progress,
            "step": ev.step,
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

    def _clip_cfg(self) -> dict:
        return (self.bot.config.get("units") or {}).get("clip_pipeline") or {}

    def _default_output_dir_for(self, video_path: str) -> str:
        """NAS outputs/<video_basename>/ を組み立てる。

        Pi 上で動く Dispatcher 目線での絶対パス（例: `/mnt/secretary-bot/auto-kirinuki/outputs/stream_01/`）。
        Windows パスが来た場合はベース名だけ抜き出す。
        """
        cfg = self._clip_cfg().get("nas") or {}
        base = cfg.get("base_path", "/mnt/secretary-bot/auto-kirinuki")
        outputs_sub = cfg.get("outputs_subdir", "outputs")

        # video_path が Windows 形式か Posix 形式か判定
        if "\\" in video_path or (len(video_path) > 1 and video_path[1] == ":"):
            stem = PureWindowsPath(video_path).stem
        else:
            stem = PurePosixPath(video_path).stem
        if not stem:
            stem = "unknown"

        joined = base.rstrip("/\\") + "/" + outputs_sub.strip("/\\") + "/" + stem
        return joined

    def _row_to_dict(self, row: dict) -> dict:
        params: dict[str, Any] = {}
        try:
            params = json.loads(row.get("params_json") or "{}")
        except Exception:
            params = {}
        result: dict[str, Any] | None = None
        if row.get("result_json"):
            try:
                result = json.loads(row["result_json"])
            except Exception:
                result = None
        return {
            "job_id": row["id"],
            "user_id": row["user_id"],
            "platform": row["platform"],
            "status": row["status"],
            "step": row.get("step"),
            "progress": int(row.get("progress") or 0),
            "assigned_agent": row.get("assigned_agent"),
            "video_path": row["video_path"],
            "output_dir": row["output_dir"],
            "mode": row["mode"],
            "whisper_model": row["whisper_model"],
            "ollama_model": row["ollama_model"],
            "params": params,
            "result": result,
            "last_error": row.get("last_error"),
            "retry_count": int(row.get("retry_count") or 0),
            "max_retries": int(row.get("max_retries") or 2),
            "cache_sync_id": row.get("cache_sync_id"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }

    def _find_agent(self, agent_id: str) -> dict | None:
        for a in getattr(self.bot.unit_manager.agent_pool, "_agents", []):
            if a.get("id") == agent_id:
                return a
        return None
