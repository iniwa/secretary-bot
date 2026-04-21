"""Dispatcher — clip_pipeline ジョブの状態機械を駆動する。

ステート: queued → dispatching → (warming_cache →) running → done
リトライ: 非終端 → queued（retry_count++, next_attempt_at=now+backoff）
キャンセル: 任意非終端 → cancelled

Workers:
  - job_dispatcher:    event 駆動 + ポーリングで queued→dispatching→...
  - whisper_cache_monitor: warming_cache 中の Agent SSE を購読（per-job task）
  - running_monitor:    running 中の Agent SSE を購読（per-job task）
  - stuck_reaper:       周期的に timeout_at 超過のジョブを検知

image_gen/dispatcher.py と構造を揃えつつ、下記を差分として持つ:
  - Whisper モデル 1 種の自動同期（image_gen の複数モデル manifest より単純）
  - running イベントは step/progress の両方を保存
  - 結果は `result_json`（EDL/highlights/clip_paths を 1 つの JSON に）
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timedelta, timezone

from src.database import jst_now
from src.errors import (
    AgentCommunicationError,
    BotError,
    ValidationError,
    is_retryable,
)
from src.logger import get_logger, new_trace_id
from src.units.clip_pipeline.agent_client import AgentClient
from src.units.clip_pipeline.models import (
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


class Dispatcher:
    """clip_pipeline の状態機械ドライバ。"""

    def __init__(self, bot, unit):
        self.bot = bot
        self.unit = unit
        self._wake_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._monitoring: set[str] = set()
        self._running = False
        self._progress_last: dict[str, float] = {}
        self._last_step: dict[str, str] = {}
        self._agent_clients: dict[str, AgentClient] = {}
        # _handle_dispatching が同時並行で走るとき、DB の assigned_agent が
        # セットされるのは ac.job_start 完了後になるため、その間に別ジョブが
        # 同じ Agent を picks してしまう。job_start の呼び出し中だけ in-memory
        # で追跡して、先行ジョブが掴んでいる Agent を除外する。
        self._dispatching_to: dict[str, str] = {}  # agent_id → job_id
        # 選定 → 予約 をアトミックにするためのロック。`_select_agent_for_job`
        # が内部で await するので、ロックなしでは 2 本同時に同じ Agent を
        # 掴んでしまう（後続は Agent 側 423 Locked を食らう）。
        self._select_lock = asyncio.Lock()

        cfg = (
            bot.config.get("units", {})
            .get("clip_pipeline", {})
        )
        dispatcher_cfg = cfg.get("dispatcher", {}) or {}
        self._poll_interval_sec = float(dispatcher_cfg.get("poll_interval_seconds", 2.0))
        self._stuck_interval_sec = float(
            dispatcher_cfg.get("stuck_reaper_interval_seconds", 30.0)
        )
        self._progress_debounce_sec = float(
            dispatcher_cfg.get("progress_debounce_seconds", 2.0)
        )

        retry_cfg = cfg.get("retry", {}) or {}
        self._backoff_base_sec = float(retry_cfg.get("base_backoff_seconds", 30.0))
        self._backoff_max_sec = float(retry_cfg.get("max_backoff_seconds", 300.0))

        timeouts_cfg = cfg.get("timeouts", {}) or {}
        self._timeout_dispatching = int(timeouts_cfg.get("dispatching_seconds", 30))
        self._timeout_warming_cache = int(timeouts_cfg.get("warming_cache_seconds", 1800))
        self._timeout_running = int(timeouts_cfg.get("running_default_seconds", 7200))
        self._timeout_queued = int(timeouts_cfg.get("queued_seconds", 86400))

    # --- lifecycle ---

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks.append(asyncio.create_task(
            self._job_dispatcher_worker(), name="clip_job_dispatcher"))
        self._tasks.append(asyncio.create_task(
            self._stuck_reaper_worker(), name="clip_stuck_reaper"))
        self._tasks.append(asyncio.create_task(
            self._resume_monitors(), name="clip_resume_monitors"))
        log.info("ClipPipeline Dispatcher started (workers=%d)", len(self._tasks))

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
        log.info("ClipPipeline Dispatcher stopped")

    def wake(self) -> None:
        self._wake_event.set()

    # --- Worker 1: job_dispatcher ---

    async def _job_dispatcher_worker(self) -> None:
        while self._running:
            try:
                claimed = await self.bot.database.clip_pipeline_job_claim_queued()
                if claimed:
                    asyncio.create_task(self._handle_dispatching(claimed))
                    continue
            except Exception as e:
                log.error("clip_pipeline claim failed: %s", e)

            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=self._poll_interval_sec,
                )
            except TimeoutError:
                pass
            self._wake_event.clear()

    async def _handle_dispatching(self, job: dict) -> None:
        """dispatching 状態のジョブを処理: Agent 選定 → cache 判定 → running or warming_cache。"""
        job_id = job["id"]
        new_trace_id()
        agent: dict | None = None
        try:
            async with self._select_lock:
                agent = await self._select_agent_for_job(job)
                if agent is None:
                    # 全 Agent が busy or unavailable。budget を消費せず待機。
                    # （他 Agent が空く / agent_pool のメトリクスが回復する）
                    await self._transition_retry(
                        job, reason="no_agent_available",
                        from_status=STATUS_DISPATCHING,
                        consume_budget=False,
                    )
                    return
                # assigned_agent が DB に書かれるまで、他 _handle_dispatching が
                # 同じ Agent を拾ってしまわないよう in-memory で予約しておく。
                self._dispatching_to[agent["id"]] = job_id

            # Whisper モデルのキャッシュ判定
            whisper_model = job.get("whisper_model", "")
            cache_needed = await self._check_whisper_cache_missing(
                agent, whisper_model,
            )
            if cache_needed:
                sync_id = await self._start_whisper_cache_sync(
                    agent, whisper_model,
                )
                ok = await self.bot.database.clip_pipeline_job_update_status(
                    job_id, STATUS_WARMING_CACHE,
                    expected_from=STATUS_DISPATCHING,
                    assigned_agent=agent["id"],
                    cache_sync_id=sync_id,
                    timeout_at=_future(self._timeout_warming_cache),
                )
                if ok:
                    await self._broadcast(TransitionEvent(
                        job_id=job_id, from_status=STATUS_DISPATCHING,
                        to_status=STATUS_WARMING_CACHE,
                        agent_id=agent["id"],
                        detail={"cache_sync_id": sync_id,
                                "whisper_model": whisper_model},
                    ))
                    asyncio.create_task(
                        self._monitor_whisper_cache_sync(job_id, agent, sync_id)
                    )
                return

            # キャッシュ揃い済み → 即 running
            await self._start_job(job, agent)

        except Exception as e:
            log.exception("clip dispatching handler failed for %s: %s", job_id, e)
            await self._transition_failed(
                job, str(e), from_status=STATUS_DISPATCHING,
            )
        finally:
            # 成功経路でも失敗経路でも、この時点では DB が assigned_agent を
            # 持っている（running/warming_cache）か、リトライで queued に
            # 戻っている。どちらにせよ in-memory 予約は役目を終えた。
            if agent is not None:
                aid = agent.get("id")
                if aid and self._dispatching_to.get(aid) == job_id:
                    self._dispatching_to.pop(aid, None)

    async def _select_agent_for_job(self, job: dict) -> dict | None:
        """優先度順に Agent を見て、clip_pipeline の先行ジョブを抱えていない
        かつ agent_pool 的にも利用可能な最初の 1 台を返す。

        Sub PC (priority=1) → Main PC (priority=2) の順で評価。Sub PC が
        clip_pipeline ジョブ実行中なら Main PC へ自動フォールバックする。
        agent_pool 側の alive / idle / paused / mode=deny 判定は
        select_agent(preferred=aid) で流用する。
        """
        agent_pool = self.bot.unit_manager.agent_pool
        job_id = job["id"]
        agents_sorted = sorted(
            list(getattr(agent_pool, "_agents", [])),
            key=lambda a: a.get("priority", 99),
        )
        for agent in agents_sorted:
            aid = agent.get("id")
            if not aid:
                continue
            # 先行の _handle_dispatching が同じ Agent に dispatch 中（まだ
            # DB の assigned_agent が未セット）ならここでも除外する。
            pending_job = self._dispatching_to.get(aid)
            if pending_job and pending_job != job_id:
                continue
            # clip_pipeline は Agent 側で同時実行 1 本制限。先行ジョブを
            # 抱えているなら priority 順で次の Agent を試す。
            active = await self.bot.database \
                .clip_pipeline_job_count_active_on_agent(
                    aid, exclude_job_id=job_id,
                )
            if active > 0:
                continue
            # agent_pool の alive/idle/paused チェック。
            # preferred=aid 指定で aid が NG のとき他 Agent が返ってくるため、
            # 戻り値の id が一致したもののみ採用する。
            selected = await agent_pool.select_agent(preferred=aid)
            if selected and selected.get("id") == aid:
                return selected
        return None

    async def _check_whisper_cache_missing(
        self, agent: dict, whisper_model: str,
    ) -> bool:
        """Agent の capability を見て whisper_model がローカル SSD に無ければ True。

        失敗時は True を返して warming_cache パスへ回す（安全側）。
        """
        if not whisper_model:
            return False
        ac = self._get_agent_client(agent)
        try:
            cap = await ac.capability()
        except Exception as e:
            log.warning(
                "clip capability check failed for %s: %s (→ cache sync)",
                agent.get("id"), e,
            )
            return True
        local = set(cap.get("whisper_models_local") or [])
        return whisper_model not in local

    async def _start_whisper_cache_sync(
        self, agent: dict, whisper_model: str,
    ) -> str:
        ac = self._get_agent_client(agent)
        resp = await ac.whisper_cache_sync(model=whisper_model)
        return resp.get("sync_id", "")

    async def _start_job(self, job: dict, agent: dict) -> None:
        """POST /clip-pipeline/jobs/start → running へ遷移。"""
        job_id = job["id"]
        agent_id = agent["id"]
        params = _parse_json(job.get("params_json"), {})
        timeout_sec = self._timeout_running

        ac = self._get_agent_client(agent)
        try:
            await ac.job_start(
                job_id=job_id,
                video_path=job["video_path"],
                output_dir=job["output_dir"],
                whisper_model=job.get("whisper_model", ""),
                ollama_model=job.get("ollama_model", ""),
                params=params,
                timeout_sec=timeout_sec,
            )
        except ValidationError as e:
            await self._transition_failed(
                job, str(e), from_status=STATUS_DISPATCHING,
            )
            return
        except BotError as e:
            if is_retryable(e):
                # Agent が「busy」で 423 を返した場合は budget を消費せず待機。
                # 事前チェックをすり抜けたレース対策（2 本が同時に dispatching）。
                err_msg = str(e)
                if "busy" in err_msg.lower():
                    await self._transition_retry(
                        job, reason="agent_busy",
                        from_status=STATUS_DISPATCHING, last_error=err_msg,
                        consume_budget=False,
                    )
                else:
                    await self._transition_retry(
                        job, reason="transient",
                        from_status=STATUS_DISPATCHING, last_error=err_msg,
                    )
            else:
                await self._transition_failed(
                    job, str(e), from_status=STATUS_DISPATCHING,
                )
            return
        except Exception as e:
            await self._transition_retry(
                job, reason="agent_comm_fail",
                from_status=STATUS_DISPATCHING, last_error=str(e),
            )
            return

        ok = await self.bot.database.clip_pipeline_job_update_status(
            job_id, STATUS_RUNNING,
            expected_from=STATUS_DISPATCHING,
            assigned_agent=agent_id,
            started_at=jst_now(),
            timeout_at=_future(timeout_sec),
        )
        if ok:
            await self._broadcast(TransitionEvent(
                job_id=job_id, from_status=STATUS_DISPATCHING,
                to_status=STATUS_RUNNING, agent_id=agent_id,
            ))
            asyncio.create_task(self._monitor_running(job_id, agent))

    # --- Worker 2: whisper_cache_monitor (per-job task) ---

    async def _monitor_whisper_cache_sync(
        self, job_id: str, agent: dict, sync_id: str,
    ) -> None:
        if job_id in self._monitoring:
            return
        self._monitoring.add(job_id)
        ac = self._get_agent_client(agent)
        try:
            async for ev in ac.whisper_cache_sync_stream(sync_id):
                name = ev.get("event", "")
                data = ev.get("data", {}) or {}
                if name == "progress":
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
                        job = await self.bot.database.clip_pipeline_job_get(job_id)
                        if job:
                            await self._transition_retry(
                                job, reason="cache_sync_fail",
                                from_status=STATUS_WARMING_CACHE,
                                last_error=data.get("message", f"cache_sync {st}"),
                            )
                        break
                elif name == "error":
                    job = await self.bot.database.clip_pipeline_job_get(job_id)
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
            log.warning("clip cache_sync SSE lost for %s: %s", job_id, e)
            job = await self.bot.database.clip_pipeline_job_get(job_id)
            if job and job["status"] == STATUS_WARMING_CACHE:
                await self._transition_retry(
                    job, reason="cache_sync_sse_lost",
                    from_status=STATUS_WARMING_CACHE, last_error=str(e),
                )
        except Exception as e:
            log.exception("clip cache_sync monitor error for %s: %s", job_id, e)
        finally:
            self._monitoring.discard(job_id)

    async def _after_cache_sync_done(self, job_id: str, agent: dict) -> None:
        """cache_sync 完了 → jobs/start を発射し running へ。"""
        job = await self.bot.database.clip_pipeline_job_get(job_id)
        if not job or job["status"] != STATUS_WARMING_CACHE:
            return
        params = _parse_json(job.get("params_json"), {})
        timeout_sec = self._timeout_running
        ac = self._get_agent_client(agent)
        try:
            await ac.job_start(
                job_id=job_id,
                video_path=job["video_path"],
                output_dir=job["output_dir"],
                whisper_model=job.get("whisper_model", ""),
                ollama_model=job.get("ollama_model", ""),
                params=params,
                timeout_sec=timeout_sec,
            )
        except ValidationError as e:
            await self._transition_failed(
                job, str(e), from_status=STATUS_WARMING_CACHE,
            )
            return
        except BotError as e:
            if is_retryable(e):
                await self._transition_retry(
                    job, reason="post_cache_start_fail",
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

        ok = await self.bot.database.clip_pipeline_job_update_status(
            job_id, STATUS_RUNNING,
            expected_from=STATUS_WARMING_CACHE,
            assigned_agent=agent["id"],
            started_at=jst_now(),
            timeout_at=_future(timeout_sec),
        )
        if ok:
            await self._broadcast(TransitionEvent(
                job_id=job_id, from_status=STATUS_WARMING_CACHE,
                to_status=STATUS_RUNNING, agent_id=agent["id"],
            ))
            asyncio.create_task(self._monitor_running(job_id, agent))

    # --- Worker 3: running_monitor (per-job task) ---

    async def _monitor_running(self, job_id: str, agent: dict) -> None:
        if job_id in self._monitoring:
            return
        self._monitoring.add(job_id)
        ac = self._get_agent_client(agent)
        try:
            async for ev in ac.job_stream(job_id):
                name = ev.get("event", "")
                data = ev.get("data", {}) or {}
                if name == "progress":
                    await self._on_progress(
                        job_id,
                        int(data.get("percent", 0)),
                        data.get("step"),
                        agent.get("id"),
                        data,
                    )
                elif name == "step":
                    # step イベントは step のみを更新（percent を 0 で上書きしない）
                    await self._on_step(
                        job_id,
                        data.get("step"),
                        agent.get("id"),
                        data,
                    )
                elif name == "log":
                    await self._broadcast(TransitionEvent(
                        job_id=job_id, from_status=STATUS_RUNNING,
                        to_status=STATUS_RUNNING,
                        event="log", agent_id=agent.get("id"),
                        detail=data,
                    ))
                elif name == "result":
                    await self._on_job_done(job_id, data, agent.get("id"))
                    break
                elif name == "error":
                    job = await self.bot.database.clip_pipeline_job_get(job_id)
                    if not job:
                        break
                    if data.get("retryable"):
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
            log.warning("clip running SSE lost for %s: %s", job_id, e)
            job = await self.bot.database.clip_pipeline_job_get(job_id)
            if job and job["status"] == STATUS_RUNNING:
                await self._transition_retry(
                    job, reason="running_sse_lost",
                    from_status=STATUS_RUNNING, last_error=str(e),
                )
        except Exception as e:
            log.exception("clip running monitor error for %s: %s", job_id, e)
        finally:
            self._monitoring.discard(job_id)

    async def _on_progress(
        self, job_id: str, percent: int, step: str | None,
        agent_id: str | None, detail: dict,
    ) -> None:
        # step が進捗イベントに乗っていない場合は直近 step を補完
        if not step:
            step = self._last_step.get(job_id)
        else:
            self._last_step[job_id] = step
        # DB 書き込みはデバウンス
        now = time.monotonic()
        last = self._progress_last.get(job_id, 0.0)
        if now - last >= self._progress_debounce_sec:
            await self.bot.database.clip_pipeline_job_update_progress(
                job_id, percent, step=step,
            )
            self._progress_last[job_id] = now
        await self._broadcast(TransitionEvent(
            job_id=job_id, from_status=STATUS_RUNNING, to_status=STATUS_RUNNING,
            progress=percent, step=step, event="progress", agent_id=agent_id,
            detail=detail,
        ))

    async def _on_step(
        self, job_id: str, step: str | None, agent_id: str | None, detail: dict,
    ) -> None:
        """step イベント専用経路。progress を保持したまま step のみ更新する。"""
        if not step:
            return
        self._last_step[job_id] = step
        await self.bot.database.clip_pipeline_job_update_step(job_id, step)
        await self._broadcast(TransitionEvent(
            job_id=job_id, from_status=STATUS_RUNNING, to_status=STATUS_RUNNING,
            step=step, event="step", agent_id=agent_id, detail=detail,
        ))

    async def _on_job_done(
        self, job_id: str, result: dict, agent_id: str | None,
    ) -> None:
        """Agent から result イベントを受領したときの処理。

        result 期待フォーマット:
          {transcript_path, audio_features_path, emotions_path,
           highlights_path, highlights_count, edl_path, clip_paths: [...]}
        """
        # Agent が返す Windows ドライブレター形式を Pi マウント形式に正規化
        normalized = _normalize_result_paths(result, self._nas_paths())
        await self.bot.database.clip_pipeline_job_set_result(
            job_id, json.dumps(normalized, ensure_ascii=False),
        )
        ok = await self.bot.database.clip_pipeline_job_update_status(
            job_id, STATUS_DONE,
            expected_from=STATUS_RUNNING,
            finished_at=jst_now(),
            progress=100,
        )
        if ok:
            await self._broadcast(TransitionEvent(
                job_id=job_id, from_status=STATUS_RUNNING,
                to_status=STATUS_DONE, progress=100, event="result",
                agent_id=agent_id, detail=normalized,
            ))

    def _nas_paths(self) -> tuple[str, str]:
        """(base_path, outputs_subdir) を返す。パス正規化に使用。"""
        cfg = (
            self.bot.config.get("units", {})
            .get("clip_pipeline", {})
            .get("nas", {})
        ) or {}
        base = cfg.get("base_path", "/mnt/secretary-bot/auto-kirinuki")
        outputs = cfg.get("outputs_subdir", "outputs")
        return base, outputs

    # --- Worker 4: stuck_reaper ---

    async def _stuck_reaper_worker(self) -> None:
        while self._running:
            try:
                rows = await self.bot.database.clip_pipeline_job_find_timed_out()
                for row in rows:
                    await self._handle_timeout(row)
            except Exception as e:
                log.error("clip stuck_reaper error: %s", e)
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._stuck_interval_sec,
                )
            except TimeoutError:
                pass

    async def _handle_timeout(self, job: dict) -> None:
        status = job["status"]
        job_id = job["id"]
        log.warning("Clip job %s timed out in status=%s", job_id, status)
        if status == STATUS_DISPATCHING:
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
            await self._transition_failed(
                job, "queued timeout (24h)", from_status=STATUS_QUEUED,
            )

    # --- resume (起動時) ---

    async def _resume_monitors(self) -> None:
        """起動直後に warming_cache / running のジョブを拾って監視再開する。"""
        await asyncio.sleep(0.5)
        try:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM clip_pipeline_jobs "
                "WHERE status IN ('warming_cache', 'running') "
                "ORDER BY created_at ASC"
            )
            for row in rows:
                agent_id = row.get("assigned_agent")
                if not agent_id:
                    continue
                agent = self._find_agent(agent_id)
                if not agent:
                    continue
                if row["status"] == STATUS_WARMING_CACHE and row.get("cache_sync_id"):
                    asyncio.create_task(self._monitor_whisper_cache_sync(
                        row["id"], agent, row["cache_sync_id"],
                    ))
                elif row["status"] == STATUS_RUNNING:
                    asyncio.create_task(self._monitor_running(row["id"], agent))
        except Exception as e:
            log.warning("clip resume_monitors failed: %s", e)

    # --- 遷移ヘルパー ---

    async def _transition_retry(
        self, job: dict, *, reason: str, from_status: str,
        last_error: str | None = None,
        consume_budget: bool = True,
    ) -> None:
        """非終端 → queued に戻す。

        consume_budget=False のとき retry_count を消費しない。
        「Agent が別ジョブで busy」のような、このジョブ自体の失敗ではない
        ケースで永久に待てるようにする（先行ジョブ完了まで固定間隔で再試行）。
        """
        job_id = job["id"]
        retry_count = int(job.get("retry_count", 0))
        max_retries = int(job.get("max_retries", 2))
        if consume_budget and retry_count >= max_retries:
            await self._transition_failed(
                job, f"max retries exceeded: {reason}",
                from_status=from_status, last_error=last_error,
            )
            return
        if consume_budget:
            backoff = _compute_backoff(
                retry_count, self._backoff_base_sec, self._backoff_max_sec,
            )
            new_retry_count = retry_count + 1
        else:
            # 先行ジョブ待ちは固定 30s で軽くポーリング。budget は据え置き。
            backoff = 30.0
            new_retry_count = retry_count
        update_fields: dict = {
            "expected_from": from_status,
            "next_attempt_at": _future(backoff),
            "timeout_at": None,
            "dispatcher_lock_at": None,
        }
        if consume_budget:
            update_fields["retry_count"] = new_retry_count
            update_fields["last_error"] = last_error or reason
        await self.bot.database.clip_pipeline_job_update_status(
            job_id, STATUS_QUEUED, **update_fields,
        )
        await self._broadcast(TransitionEvent(
            job_id=job_id, from_status=from_status, to_status=STATUS_QUEUED,
            event="status",
            detail={
                "retry_count": new_retry_count, "reason": reason,
                "next_attempt_in_sec": int(backoff),
            },
        ))

    async def _transition_failed(
        self, job: dict, message: str, *, from_status: str,
        last_error: str | None = None,
    ) -> None:
        job_id = job["id"]
        await self.bot.database.clip_pipeline_job_update_status(
            job_id, STATUS_FAILED,
            expected_from=from_status,
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


def _parse_json(s: str | None, fallback):
    if not s:
        return fallback
    try:
        return json.loads(s)
    except Exception:
        return fallback


def _compute_backoff(retry_count: int, base: float, cap: float) -> float:
    raw = base * (2 ** retry_count)
    raw = min(raw, cap)
    jitter = raw * 0.1 * (random.random() * 2 - 1)
    return max(5.0, raw + jitter)


def _future(seconds: float) -> str:
    dt = datetime.now(JST) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_result_paths(
    result: dict, nas: tuple[str, str],
) -> dict:
    """Agent が返す Windows ドライブレター形式のパスを Pi マウント形式へ。

    `result` 期待フォーマット: 文字列パスは `transcript_path` / `edl_path` /
    `highlights_path` / `audio_features_path` / `emotions_path` / `clip_paths[]`。
    """
    base, outputs_sub = nas
    base = base.rstrip("/\\")
    outputs_clean = outputs_sub.strip("/\\")
    marker = f"/{outputs_clean}/"

    def _norm(p: str) -> str:
        if not isinstance(p, str) or not p:
            return p
        uni = p.replace("\\", "/")
        if uni.startswith(base + "/"):
            return uni
        idx = uni.find(marker)
        if idx >= 0:
            return f"{base}{uni[idx:]}"
        return uni

    out = dict(result)
    for key in ("transcript_path", "edl_path", "highlights_path",
                "audio_features_path", "emotions_path"):
        if key in out:
            out[key] = _norm(out[key])
    if isinstance(out.get("clip_paths"), list):
        out["clip_paths"] = [_norm(p) for p in out["clip_paths"]]
    return out
