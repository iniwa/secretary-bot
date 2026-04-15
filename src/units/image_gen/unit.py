"""ImageGenUnit — ジョブ受付・状態参照・キャンセル・イベント pub/sub。

Phase 1 の Walking Skeleton。Discord 連携（execute）は Phase 3。
WebGUI からは enqueue / get_job / list_jobs / list_gallery / cancel_job /
subscribe_events / unsubscribe_events を直接呼ぶ。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.errors import ValidationError
from src.logger import get_logger
from src.units.base_unit import BaseUnit
from src.units.image_gen.dispatcher import Dispatcher
from src.units.image_gen.models import (
    JobStatus, TransitionEvent, STATUS_DONE, DEFAULT_PARAMS,
)
from src.units.image_gen.workflow_mgr import WorkflowManager

log = get_logger(__name__)


class ImageGenUnit(BaseUnit):
    UNIT_NAME = "image_gen"
    UNIT_DESCRIPTION = "ComfyUI による画像生成。プリセット + プロンプトでジョブ投入。"
    DELEGATE_TO = None            # Pi 内完結（Agent 呼び出しは Dispatcher 経由）
    AUTONOMY_TIER = 4             # Phase5 で調整
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        self.workflow_mgr = WorkflowManager(bot)
        self.dispatcher = Dispatcher(bot, self)
        self._event_subscribers: set[asyncio.Queue] = set()
        self._started = False

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
        await self.dispatcher.start()

    async def on_heartbeat(self) -> None:
        if not self._started:
            await self.on_ready_hook()

    async def cog_unload(self) -> None:   # discord.py hook
        await self.dispatcher.stop()

    # --- Discord 連携（Phase3 で実装） ---

    async def execute(self, ctx, parsed: dict) -> str | None:
        raise NotImplementedError(
            "image_gen.execute is Phase3 (Discord slash commands)"
        )

    # === WebGUI から呼ばれる外部公開インターフェース ===

    async def enqueue(
        self, user_id: str, platform: str, workflow_name: str,
        positive: str | None, negative: str | None,
        params: dict[str, Any] | None = None,
    ) -> str:
        """ジョブを登録し、job_id (UUID hex) を返す。

        - workflow_name: workflows.name
        - positive / negative: ユーザー入力プロンプト
        - params: ワークフローのパラメータ（WIDTH/HEIGHT/STEPS/CFG/SEED/...）
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

        job_id = await self.bot.database.image_job_insert(
            user_id=user_id, platform=platform,
            workflow_id=int(wf["id"]),
            positive=positive, negative=negative,
            params_json=json.dumps(merged, ensure_ascii=False),
            priority=int(merged.get("PRIORITY", 0)) if "PRIORITY" in merged else 0,
        )
        log.info("image_job enqueued: id=%s user=%s workflow=%s",
                 job_id, user_id, workflow_name)
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
        row = await self.bot.database.image_job_get(job_id)
        if not row:
            return None
        return await self._row_to_dict(row)

    async def list_jobs(
        self, user_id: str | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        rows = await self.bot.database.image_job_list(
            user_id=user_id, status=status, limit=limit, offset=offset,
        )
        return [await self._row_to_dict(r) for r in rows]

    async def list_gallery(
        self, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """完了ジョブの result_paths を日付降順で列挙する。

        Phase1 は DB の image_jobs.result_paths を参照する簡易実装。
        Phase2 で NAS outputs/ を直接走査する実装に差し替える。
        """
        rows = await self.bot.database.image_job_list(
            status=STATUS_DONE, limit=limit, offset=offset,
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
            out.append({
                "job_id": r["id"],
                "user_id": r["user_id"],
                "finished_at": r.get("finished_at"),
                "result_paths": paths,
                "positive": r.get("positive"),
                "negative": r.get("negative"),
            })
        return out

    async def cancel_job(self, job_id: str) -> bool:
        """非終端ジョブを cancelled へ。Agent 側にも interrupt を試みる。"""
        row = await self.bot.database.image_job_get(job_id)
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
                            await ac.image_job_cancel(job_id)
                    finally:
                        await ac.close()
                except Exception as e:
                    log.warning("agent cancel best-effort failed: %s", e)
        ok = await self.bot.database.image_job_cancel(job_id)
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
            last_error=row.get("last_error"),
            retry_count=int(row.get("retry_count") or 0),
            max_retries=int(row.get("max_retries") or 0),
            created_at=row.get("created_at"),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
        )
        return js.to_dict()

    def _find_agent(self, agent_id: str) -> dict | None:
        for a in getattr(self.bot.unit_manager.agent_pool, "_agents", []):
            if a.get("id") == agent_id:
                return a
        return None
