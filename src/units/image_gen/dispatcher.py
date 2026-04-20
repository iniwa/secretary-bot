"""Dispatcher — 画像生成ジョブの状態機械を駆動する 4 worker。

ステート: queued → dispatching → (warming_cache →) running → done
リトライ: 非終端 → queued（retry_count++, next_attempt_at=now+backoff）
キャンセル: 任意非終端 → cancelled

Workers:
  - job_dispatcher:    event 駆動 + 2s ポーリングで queued→dispatching→...
  - cache_sync_monitor: warming_cache 中の Agent SSE を購読
  - running_monitor:    running 中の Agent SSE を購読
  - stuck_reaper:       30s 周期で timeout_at 超過のジョブを検知
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone

from src.database import jst_now
from src.errors import (
    AgentCommunicationError,
    ImageGenError,
    OOMError,
    ValidationError,
    is_retryable,
)
from src.logger import get_logger, new_trace_id
from src.units.image_gen.agent_client import AgentClient
from src.units.image_gen.models import (
    STATUS_DISPATCHING,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_WARMING_CACHE,
    TransitionEvent,
)

log = get_logger(__name__)

JST = timezone(timedelta(hours=9))

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def _resolve_placeholder(value: str, params: dict) -> str:
    """`{{KEY}}` を params[KEY] で置換。未解決はそのまま残す。"""
    if not isinstance(value, str):
        return value

    def _sub(m: re.Match) -> str:
        v = params.get(m.group(1))
        return str(v) if v is not None else m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, value)

# Poll 間隔・タイムアウト設定
_POLL_INTERVAL_SEC = 2.0
_STUCK_REAPER_INTERVAL_SEC = 30.0
_PROGRESS_DEBOUNCE_SEC = 2.0

# Backoff 設定
_BACKOFF_BASE_SEC = 30.0
_BACKOFF_MAX_SEC = 300.0


class Dispatcher:
    """ジョブ状態機械のドライバ。"""

    def __init__(self, bot, unit):
        self.bot = bot
        self.unit = unit
        self._wake_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._monitoring: set[str] = set()   # 監視中 job_id
        self._running = False
        # 進捗デバウンス: job_id → last_written_ts
        self._progress_last: dict[str, float] = {}
        # Agent クライアントキャッシュ: agent_id -> AgentClient
        self._agent_clients: dict[str, AgentClient] = {}

    # --- lifecycle ---

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks.append(asyncio.create_task(
            self._job_dispatcher_worker(), name="img_job_dispatcher"))
        self._tasks.append(asyncio.create_task(
            self._stuck_reaper_worker(), name="img_stuck_reaper"))
        # cache_sync_monitor / running_monitor は job 単位で動的起動される。
        # ここでは起動時に既存の warming_cache / running を復帰監視する。
        self._tasks.append(asyncio.create_task(
            self._resume_monitors(), name="img_resume_monitors"))
        # 起動時ウォームアップ: 定常ベースモデルを NAS→ローカルに事前キャッシュ
        self._tasks.append(asyncio.create_task(
            self._warmup_agents(), name="img_warmup"))
        log.info("Dispatcher started (workers=%d)", len(self._tasks))

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        for ac in self._agent_clients.values():
            try:
                await ac.close()
            except Exception:
                pass
        self._agent_clients.clear()
        log.info("Dispatcher stopped")

    def wake(self) -> None:
        """新規ジョブ投入時などに呼び出して dispatcher を即起床させる。"""
        self._wake_event.set()

    # --- Worker 1: job_dispatcher ---

    async def _job_dispatcher_worker(self) -> None:
        while self._running:
            try:
                claimed = await self.bot.database.generation_job_claim_queued()
                if claimed:
                    asyncio.create_task(self._handle_dispatching(claimed))
                    continue  # すぐ次を拾いに行く
            except Exception as e:
                log.error("job_dispatcher claim failed: %s", e)

            # 空き待ち: event or poll
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=_POLL_INTERVAL_SEC,
                )
            except TimeoutError:
                pass
            self._wake_event.clear()

    async def _handle_dispatching(self, job: dict) -> None:
        """dispatching 状態のジョブを処理: Agent 選定 → cache 判定 → running or warming_cache。"""
        job_id = job["id"]
        new_trace_id()
        try:
            # Agent 選定
            agent = await self._select_agent_for_job(job)
            if agent is None:
                await self._transition_retry(
                    job, reason="no_agent_available",
                    from_status=STATUS_DISPATCHING,
                )
                return
            agent_id = agent.get("id", "")

            # キャッシュ照合（Phase1 は簡易: 不足検知のみ、実際の sync は Agent 側に委譲）
            missing = await self._check_cache_missing(agent, job)
            if missing:
                sync_id = await self._start_cache_sync(agent, missing, job_id)
                ok = await self.bot.database.generation_job_update_status(
                    job_id, STATUS_WARMING_CACHE,
                    expected_from=STATUS_DISPATCHING,
                    assigned_agent=agent_id,
                    cache_sync_id=sync_id,
                    timeout_at=_future(600),
                )
                if ok:
                    await self._broadcast(TransitionEvent(
                        job_id=job_id, from_status=STATUS_DISPATCHING,
                        to_status=STATUS_WARMING_CACHE, agent_id=agent_id,
                        detail={"cache_sync_id": sync_id},
                    ))
                    asyncio.create_task(self._monitor_cache_sync(job_id, agent, sync_id))
                return

            # キャッシュ揃い済み → 即 running
            await self._start_generate(job, agent)

        except Exception as e:
            log.exception("dispatching handler failed for %s: %s", job_id, e)
            await self._transition_failed(job, str(e), from_status=STATUS_DISPATCHING)

    async def _select_agent_for_job(self, job: dict) -> dict | None:
        """Agent 選定: main_pc_only なら MainPC 固定、それ以外は AgentPool.select_agent。"""
        workflow_id = job.get("workflow_id")
        main_pc_only = False
        if workflow_id:
            wf = await self.bot.database.workflow_get(workflow_id)
            if wf:
                main_pc_only = bool(wf.get("main_pc_only", 0))
        preferred = None
        if main_pc_only:
            # AgentPool 内で role=main を優先に振る簡易実装
            for a in getattr(self.bot.unit_manager.agent_pool, "_agents", []):
                if a.get("role") == "main":
                    preferred = a.get("id")
                    break
        return await self.bot.unit_manager.agent_pool.select_agent(preferred=preferred)

    async def _check_cache_missing(self, agent: dict, job: dict) -> list[dict]:
        """Phase1: model_cache_manifest を見て欠けているものを返す（最小実装）。

        required_models / required_loras は workflows テーブル由来。
        NAS パス解決は Phase2 で厳密化するため、ここでは agent の実キャッシュと
        filename のみ照合（同 filename が cache に無ければ欠損扱い）。
        """
        workflow_id = job.get("workflow_id")
        if not workflow_id:
            return []
        wf = await self.bot.database.workflow_get(workflow_id)
        if not wf:
            return []
        required: list[dict] = []
        try:
            req_m = json.loads(wf.get("required_models") or "[]")
            req_l = json.loads(wf.get("required_loras") or "[]")
            required = list(req_m) + list(req_l)
        except Exception:
            required = []
        if not required:
            return []
        # preset の required_* は `{{CKPT}}` などの placeholder を含む。job.params_json で解決する。
        try:
            params = json.loads(job.get("params_json") or "{}")
        except Exception:
            params = {}
        resolved: list[dict] = []
        for m in required:
            fn = _resolve_placeholder(m.get("filename", ""), params)
            if not fn or "{{" in fn:
                raise ValidationError(
                    f"unresolved placeholder in required model: {m.get('filename')}"
                )
            resolved.append({**m, "filename": fn})
        agent_id = agent.get("id", "")
        rows = await self.bot.database.fetchall(
            "SELECT file_type, filename FROM model_cache_manifest WHERE agent_id = ?",
            (agent_id,),
        )
        have = {(r["file_type"], r["filename"]) for r in rows}
        missing: list[dict] = []
        for m in resolved:
            key = (m.get("type", ""), m.get("filename", ""))
            if key not in have:
                missing.append(m)
        return missing

    async def _start_cache_sync(
        self, agent: dict, files: list[dict], job_id: str,
    ) -> str:
        """POST /cache/sync を呼び、sync_id を返す。"""
        ac = self._get_agent_client(agent)
        # NAS パスが欠けている場合は Agent 側のデフォルトに委ねる（Phase2 で厳密化）
        payload_files = []
        for f in files:
            payload_files.append({
                "type": f.get("type", ""),
                "filename": f.get("filename", ""),
                "nas_path": f.get("nas_path", ""),
                "sha256": f.get("sha256", ""),
            })
        resp = await ac.cache_sync(payload_files, reason=f"job_id={job_id}")
        return resp.get("sync_id", "")

    async def _start_generate(self, job: dict, agent: dict) -> None:
        """POST /generation/submit を呼び、running へ遷移する。"""
        job_id = job["id"]
        agent_id = agent.get("id", "")
        workflow_json, params, timeout_sec = await self._build_workflow_payload(job)
        modality = job.get("modality") or "image"

        ac = self._get_agent_client(agent)
        try:
            resp = await ac.generation_submit(
                job_id=job_id, workflow_json=workflow_json,
                inputs=params, timeout_sec=timeout_sec,
                modality=modality,
            )
        except OOMError as e:
            # OOM は別 Agent へ retry
            log.warning("OOM on %s for %s: %s", agent_id, job_id, e)
            await self._transition_retry(
                job, reason=f"OOM_on_{agent_id}",
                from_status=STATUS_DISPATCHING,
                last_error=str(e),
            )
            return
        except ValidationError as e:
            await self._transition_failed(job, str(e), from_status=STATUS_DISPATCHING)
            return
        except ImageGenError as e:
            if is_retryable(e):
                await self._transition_retry(
                    job, reason="transient", from_status=STATUS_DISPATCHING,
                    last_error=str(e),
                )
            else:
                await self._transition_failed(
                    job, str(e), from_status=STATUS_DISPATCHING,
                )
            return
        except Exception as e:
            await self._transition_retry(
                job, reason="agent_comm_fail", from_status=STATUS_DISPATCHING,
                last_error=str(e),
            )
            return

        # running へ遷移
        ok = await self.bot.database.generation_job_update_status(
            job_id, STATUS_RUNNING,
            expected_from=STATUS_DISPATCHING,
            assigned_agent=agent_id,
            started_at=jst_now(),
            timeout_at=_future(timeout_sec or 300),
        )
        if ok:
            await self._broadcast(TransitionEvent(
                job_id=job_id, from_status=STATUS_DISPATCHING,
                to_status=STATUS_RUNNING, agent_id=agent_id,
                detail={"comfyui_prompt_id": resp.get("comfyui_prompt_id")},
            ))
            asyncio.create_task(self._monitor_running(job_id, agent))

    async def _build_workflow_payload(
        self, job: dict,
    ) -> tuple[dict, dict, int]:
        """ジョブの params_json と workflow_id から実行 JSON を組み立てる。"""
        params = json.loads(job.get("params_json") or "{}")
        # positive / negative は params にも注入して Agent 側の inputs に渡す
        if job.get("positive") is not None:
            params.setdefault("POSITIVE", job["positive"])
        if job.get("negative") is not None:
            params.setdefault("NEGATIVE", job["negative"])
        # seed が -1 ならここで決定
        if params.get("SEED", -1) == -1:
            params["SEED"] = random.randint(0, 2**31 - 1)
        # filename_prefix を組み立て
        if "FILENAME_PREFIX" not in params:
            now = datetime.now(JST).strftime("%Y-%m-%d_%H%M%S")
            params["FILENAME_PREFIX"] = f"{now}_{job['id']}_{params['SEED']}"

        workflow_id = job.get("workflow_id")
        wf_row = await self.bot.database.workflow_get(workflow_id) if workflow_id else None
        if not wf_row:
            raise ValidationError(f"Workflow id={workflow_id} not found")

        workflow_json = await self.unit.workflow_mgr.resolve(
            wf_row["name"], params,
        )
        timeout_sec = int(wf_row.get("default_timeout_sec") or 300)
        # inputs はログ用
        inputs = {k.lower(): v for k, v in params.items()}
        return workflow_json, inputs, timeout_sec

    # --- Worker 2: cache_sync_monitor (per-job task) ---

    async def _monitor_cache_sync(
        self, job_id: str, agent: dict, sync_id: str,
    ) -> None:
        if job_id in self._monitoring:
            return
        self._monitoring.add(job_id)
        ac = self._get_agent_client(agent)
        try:
            async for ev in ac.cache_sync_stream(sync_id):
                name = ev.get("event", "")
                data = ev.get("data", {}) or {}
                if name == "progress":
                    # キャッシュ転送進捗は progress フィールドではなく
                    # 付加情報として broadcast（DB は更新しない）
                    await self._broadcast(TransitionEvent(
                        job_id=job_id, from_status=STATUS_WARMING_CACHE,
                        to_status=STATUS_WARMING_CACHE,
                        progress=int(data.get("percent", 0)),
                        event="progress", agent_id=agent.get("id"),
                        detail=data,
                    ))
                elif name == "status":
                    st = data.get("status")
                    if st == "done":
                        await self._after_cache_sync_done(job_id, agent)
                        break
                    elif st in ("failed", "cancelled"):
                        job = await self.bot.database.generation_job_get(job_id)
                        if job:
                            await self._transition_retry(
                                job, reason="cache_sync_fail",
                                from_status=STATUS_WARMING_CACHE,
                                last_error=data.get("message", f"cache_sync {st}"),
                            )
                        break
                elif name == "error":
                    job = await self.bot.database.generation_job_get(job_id)
                    if job:
                        if data.get("retryable"):
                            await self._transition_retry(
                                job, reason="cache_sync_err",
                                from_status=STATUS_WARMING_CACHE,
                                last_error=data.get("message", ""),
                            )
                        else:
                            await self._transition_failed(
                                job, data.get("message", "cache_sync error"),
                                from_status=STATUS_WARMING_CACHE,
                            )
                    break
                elif name == "done":
                    break
        except AgentCommunicationError as e:
            log.warning("cache_sync SSE lost for %s: %s", job_id, e)
            job = await self.bot.database.generation_job_get(job_id)
            if job and job["status"] == STATUS_WARMING_CACHE:
                await self._transition_retry(
                    job, reason="cache_sync_sse_lost",
                    from_status=STATUS_WARMING_CACHE, last_error=str(e),
                )
        except Exception as e:
            log.exception("cache_sync monitor error for %s: %s", job_id, e)
        finally:
            self._monitoring.discard(job_id)

    async def _after_cache_sync_done(self, job_id: str, agent: dict) -> None:
        """cache_sync 完了後に running へ遷移させる。"""
        job = await self.bot.database.generation_job_get(job_id)
        if not job or job["status"] != STATUS_WARMING_CACHE:
            return
        # dispatching へ戻さずその場で generate を投げる（warming_cache → running を直接）
        # _start_generate は expected_from=dispatching を期待するので一旦 dispatching へ戻す
        # (簡易実装: DB 上の from を warming_cache → running へ直遷移)
        workflow_json, params, timeout_sec = await self._build_workflow_payload(job)
        modality = job.get("modality") or "image"
        ac = self._get_agent_client(agent)
        try:
            resp = await ac.generation_submit(
                job_id=job_id, workflow_json=workflow_json,
                inputs=params, timeout_sec=timeout_sec,
                modality=modality,
            )
        except ImageGenError as e:
            if is_retryable(e):
                await self._transition_retry(
                    job, reason="post_cache_generate_fail",
                    from_status=STATUS_WARMING_CACHE, last_error=str(e),
                )
            else:
                await self._transition_failed(
                    job, str(e), from_status=STATUS_WARMING_CACHE,
                )
            return
        except Exception as e:
            await self._transition_retry(
                job, reason="post_cache_comm_fail",
                from_status=STATUS_WARMING_CACHE, last_error=str(e),
            )
            return

        ok = await self.bot.database.generation_job_update_status(
            job_id, STATUS_RUNNING,
            expected_from=STATUS_WARMING_CACHE,
            assigned_agent=agent.get("id", ""),
            started_at=jst_now(),
            timeout_at=_future(timeout_sec or 300),
        )
        if ok:
            await self._broadcast(TransitionEvent(
                job_id=job_id, from_status=STATUS_WARMING_CACHE,
                to_status=STATUS_RUNNING, agent_id=agent.get("id"),
                detail={"comfyui_prompt_id": resp.get("comfyui_prompt_id")},
            ))
            asyncio.create_task(self._monitor_running(job_id, agent))

    # --- Worker 3: running_monitor (per-job task) ---

    async def _monitor_running(self, job_id: str, agent: dict) -> None:
        if job_id in self._monitoring:
            return
        self._monitoring.add(job_id)
        ac = self._get_agent_client(agent)
        try:
            async for ev in ac.generation_job_stream(job_id):
                name = ev.get("event", "")
                data = ev.get("data", {}) or {}
                if name == "progress":
                    await self._on_progress(
                        job_id, int(data.get("percent", 0)),
                        agent.get("id"), data,
                    )
                elif name == "status":
                    st = data.get("status")
                    if st in ("done", "failed", "cancelled"):
                        # result は別途 event で来る想定だが念のため
                        pass
                elif name == "result":
                    paths = data.get("result_paths", []) or []
                    kinds = data.get("result_kinds", []) or []
                    await self._on_job_done(job_id, paths, kinds, agent.get("id"))
                    break
                elif name == "error":
                    job = await self.bot.database.generation_job_get(job_id)
                    if not job:
                        break
                    if data.get("retryable"):
                        # OOM は別 Agent へ
                        if (data.get("error_class") or "").endswith("OOMError"):
                            await self._transition_retry(
                                job, reason="OOM", from_status=STATUS_RUNNING,
                                last_error=data.get("message", "OOM"),
                                avoid_agent=agent.get("id"),
                            )
                        else:
                            await self._transition_retry(
                                job, reason="running_transient",
                                from_status=STATUS_RUNNING,
                                last_error=data.get("message", ""),
                            )
                    else:
                        await self._transition_failed(
                            job, data.get("message", "running error"),
                            from_status=STATUS_RUNNING,
                        )
                    break
                elif name == "done":
                    break
        except AgentCommunicationError as e:
            log.warning("running SSE lost for %s: %s", job_id, e)
            job = await self.bot.database.generation_job_get(job_id)
            if job and job["status"] == STATUS_RUNNING:
                await self._transition_retry(
                    job, reason="running_sse_lost",
                    from_status=STATUS_RUNNING, last_error=str(e),
                )
        except Exception as e:
            log.exception("running monitor error for %s: %s", job_id, e)
        finally:
            self._monitoring.discard(job_id)

    async def _on_progress(
        self, job_id: str, percent: int, agent_id: str | None, detail: dict,
    ) -> None:
        # DB 書き込みは 2s デバウンス
        now = time.monotonic()
        last = self._progress_last.get(job_id, 0.0)
        if now - last >= _PROGRESS_DEBOUNCE_SEC:
            await self.bot.database.generation_job_update_progress(job_id, percent)
            self._progress_last[job_id] = now
        await self._broadcast(TransitionEvent(
            job_id=job_id, from_status=STATUS_RUNNING, to_status=STATUS_RUNNING,
            progress=percent, event="progress", agent_id=agent_id,
            detail=detail,
        ))

    def _normalize_nas_path(self, p: str) -> str:
        """Agent が返す Windows ドライブレター形式（例: `N:/ai-image/outputs/...`）
        を Pi 側マウント形式（例: `/mnt/ai-image/outputs/...`）へ変換。"""
        if not isinstance(p, str) or not p:
            return p
        nas_cfg = self.bot.config.get("units", {}).get("image_gen", {}).get("nas", {}) or {}
        base = (nas_cfg.get("mount_point") or nas_cfg.get("base_path") or "/mnt/ai-image").rstrip("/\\")
        outputs_sub = (nas_cfg.get("outputs_subdir") or "outputs").strip("/\\")
        # 既に Pi マウント配下なら変更なし
        if p.startswith(base + "/") or p.startswith(base + "\\"):
            return p.replace("\\", "/")
        # outputs/... 部分を抜き出して base と結合
        uni = p.replace("\\", "/")
        marker = f"/{outputs_sub}/"
        idx = uni.find(marker)
        if idx >= 0:
            return f"{base}{uni[idx:]}"
        return uni

    async def _on_job_done(
        self, job_id: str, result_paths: list[str],
        result_kinds: list[str], agent_id: str | None,
    ) -> None:
        # kinds が空/不足なら paths 長さに合わせて image fallback
        if not result_kinds or len(result_kinds) != len(result_paths):
            result_kinds = ["image"] * len(result_paths)
        # Agent 側の Windows ドライブ形式を Pi マウント形式に正規化
        result_paths = [self._normalize_nas_path(p) for p in result_paths]
        await self.bot.database.generation_job_set_result(
            job_id,
            json.dumps(result_paths, ensure_ascii=False),
            json.dumps(result_kinds, ensure_ascii=False),
        )
        ok = await self.bot.database.generation_job_update_status(
            job_id, STATUS_DONE,
            expected_from=STATUS_RUNNING,
            finished_at=jst_now(),
            progress=100,
        )
        if ok:
            await self._broadcast(TransitionEvent(
                job_id=job_id, from_status=STATUS_RUNNING,
                to_status=STATUS_DONE, progress=100, event="result",
                agent_id=agent_id,
                detail={
                    "result_paths": result_paths,
                    "result_kinds": result_kinds,
                },
            ))

    # --- Worker 4: stuck_reaper ---

    async def _stuck_reaper_worker(self) -> None:
        while self._running:
            try:
                rows = await self.bot.database.generation_job_find_timed_out()
                for row in rows:
                    await self._handle_timeout(row)
            except Exception as e:
                log.error("stuck_reaper error: %s", e)
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=_STUCK_REAPER_INTERVAL_SEC,
                )
            except TimeoutError:
                pass

    async def _handle_timeout(self, job: dict) -> None:
        status = job["status"]
        job_id = job["id"]
        log.warning("Job %s timed out in status=%s", job_id, status)
        if status == STATUS_DISPATCHING:
            # Dispatcher が死んだ or 選定が詰まった → retry
            await self._transition_retry(
                job, reason="dispatching_timeout",
                from_status=STATUS_DISPATCHING,
                last_error="dispatching timeout",
            )
        elif status == STATUS_WARMING_CACHE:
            await self._transition_retry(
                job, reason="warming_cache_timeout",
                from_status=STATUS_WARMING_CACHE,
                last_error="cache sync timeout",
            )
        elif status == STATUS_RUNNING:
            await self._transition_retry(
                job, reason="running_timeout",
                from_status=STATUS_RUNNING,
                last_error="running timeout",
            )
        elif status == STATUS_QUEUED:
            # 24h 以上 queued は失敗確定
            await self._transition_failed(
                job, "queued timeout (24h)", from_status=STATUS_QUEUED,
            )

    # --- resume (起動時) ---

    async def _resume_monitors(self) -> None:
        """起動直後に warming_cache / running のジョブを拾って監視再開する。"""
        await asyncio.sleep(0.5)
        try:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM generation_jobs WHERE status IN "
                "('warming_cache', 'running') ORDER BY created_at ASC"
            )
            for row in rows:
                agent_id = row.get("assigned_agent")
                if not agent_id:
                    continue
                agent = self._find_agent(agent_id)
                if not agent:
                    continue
                if row["status"] == STATUS_WARMING_CACHE and row.get("cache_sync_id"):
                    asyncio.create_task(self._monitor_cache_sync(
                        row["id"], agent, row["cache_sync_id"],
                    ))
                elif row["status"] == STATUS_RUNNING:
                    asyncio.create_task(self._monitor_running(row["id"], agent))
        except Exception as e:
            log.warning("resume_monitors failed: %s", e)

    # --- warmup (起動時) ---

    async def _warmup_agents(self) -> None:
        """起動時に全 Agent のキャッシュ状態を同期し、定常モデルを事前取得する。

        best-effort。失敗はログに残すだけでジョブ処理へは影響させない。
        定期実行は model_sync ユニットが担当する。
        """
        await asyncio.sleep(2.0)
        from src.units.image_gen.warmup import warmup_all_agents
        await warmup_all_agents(self.bot, trigger_sync=True)

    # --- 遷移ヘルパー ---

    async def _transition_retry(
        self, job: dict, *, reason: str, from_status: str,
        last_error: str | None = None, avoid_agent: str | None = None,
    ) -> None:
        """non-terminal → queued（retry）。retry_count 超過で failed。"""
        job_id = job["id"]
        retry_count = int(job.get("retry_count", 0))
        max_retries = int(job.get("max_retries", 2))
        if retry_count >= max_retries:
            await self._transition_failed(
                job, f"max retries exceeded: {reason}", from_status=from_status,
                last_error=last_error,
            )
            return
        backoff = _compute_backoff(retry_count)
        await self.bot.database.generation_job_update_status(
            job_id, STATUS_QUEUED,
            expected_from=from_status,
            retry_count=retry_count + 1,
            last_error=last_error or reason,
            next_attempt_at=_future(backoff),
            timeout_at=None,
            dispatcher_lock_at=None,
            assigned_agent=None if avoid_agent else job.get("assigned_agent"),
        )
        await self._broadcast(TransitionEvent(
            job_id=job_id, from_status=from_status, to_status=STATUS_QUEUED,
            event="status",
            detail={"retry_count": retry_count + 1, "reason": reason,
                    "next_attempt_in_sec": int(backoff)},
        ))

    async def _transition_failed(
        self, job: dict, message: str, *, from_status: str,
        last_error: str | None = None,
    ) -> None:
        job_id = job["id"]
        await self.bot.database.generation_job_update_status(
            job_id, STATUS_FAILED,
            expected_from=from_status,
            error_message=message,
            last_error=last_error or message,
            finished_at=jst_now(),
        )
        await self._broadcast(TransitionEvent(
            job_id=job_id, from_status=from_status, to_status=STATUS_FAILED,
            event="error", detail={"message": message},
        ))

    # --- broadcast / helpers ---

    async def _broadcast(self, ev: TransitionEvent) -> None:
        await self.unit.broadcast_event(ev)

    def _get_agent_client(self, agent: dict) -> AgentClient:
        aid = agent.get("id", "")
        ac = self._agent_clients.get(aid)
        if ac is None:
            ac = AgentClient(agent)
            self._agent_clients[aid] = ac
        return ac

    def _find_agent(self, agent_id: str) -> dict | None:
        for a in getattr(self.bot.unit_manager.agent_pool, "_agents", []):
            if a.get("id") == agent_id:
                return a
        return None


def _compute_backoff(retry_count: int) -> float:
    """base*2^n + ±10% jitter, capped by max。"""
    raw = _BACKOFF_BASE_SEC * (2 ** retry_count)
    raw = min(raw, _BACKOFF_MAX_SEC)
    jitter = raw * 0.1 * (random.random() * 2 - 1)
    return max(5.0, raw + jitter)


def _future(seconds: float) -> str:
    """now + seconds を JST ISO 文字列で返す。"""
    dt = datetime.now(JST) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
