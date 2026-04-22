"""Dispatcher の状態機械テスト（FakeDB + FakeAgentClient）。

FastAPI / httpx / DB / 実 Agent を一切使わず、Dispatcher が DB とユニットに
対して期待通りの UPDATE / broadcast を発行するかを検証する。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.errors import (
    AgentCommunicationError,
    ResourceUnavailableError,
    TransientError,
    ValidationError,
)
from src.units.clip_pipeline import dispatcher as disp_mod
from src.units.clip_pipeline.dispatcher import Dispatcher
from src.units.clip_pipeline.models import (
    STATUS_DISPATCHING,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_WARMING_CACHE,
    TransitionEvent,
)


# =========================================================================
#   Fakes
# =========================================================================


class FakeDB:
    """Dispatcher が触る CRUD だけ実装したインメモリ DB モック。"""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.active_counts: dict[str, int] = {}
        self.update_log: list[dict] = []
        self.progress_log: list[tuple] = []
        self.step_log: list[tuple] = []
        self.result_log: list[tuple] = []
        self.timed_out: list[dict] = []

    def insert(self, job: dict) -> None:
        self.jobs[job["id"]] = job

    async def clip_pipeline_job_get(self, job_id: str) -> dict | None:
        j = self.jobs.get(job_id)
        return dict(j) if j else None

    async def clip_pipeline_job_update_status(
        self, job_id: str, to_status: str,
        expected_from: str | None = None, **fields,
    ) -> bool:
        j = self.jobs.get(job_id)
        if not j:
            return False
        if expected_from is not None and j.get("status") != expected_from:
            self.update_log.append({
                "job_id": job_id, "to_status": to_status,
                "expected_from": expected_from,
                "current": j.get("status"), "ok": False, "fields": fields,
            })
            return False
        j["status"] = to_status
        for k, v in fields.items():
            j[k] = v
        self.update_log.append({
            "job_id": job_id, "to_status": to_status,
            "expected_from": expected_from, "ok": True, "fields": fields,
        })
        return True

    async def clip_pipeline_job_update_progress(
        self, job_id: str, percent: int, step: str | None = None,
    ) -> None:
        self.progress_log.append((job_id, percent, step))
        j = self.jobs.get(job_id)
        if j:
            j["progress"] = percent
            if step is not None:
                j["step"] = step

    async def clip_pipeline_job_update_step(self, job_id: str, step: str) -> None:
        self.step_log.append((job_id, step))
        j = self.jobs.get(job_id)
        if j:
            j["step"] = step

    async def clip_pipeline_job_set_result(
        self, job_id: str, result_json: str,
    ) -> None:
        self.result_log.append((job_id, result_json))
        j = self.jobs.get(job_id)
        if j:
            j["result_json"] = result_json

    async def clip_pipeline_job_count_active_on_agent(
        self, agent_id: str, exclude_job_id: str | None = None,
    ) -> int:
        return int(self.active_counts.get(agent_id, 0))

    async def clip_pipeline_job_claim_queued(self) -> dict | None:
        for j in self.jobs.values():
            if j["status"] == "queued":
                j["status"] = "dispatching"
                return dict(j)
        return None

    async def clip_pipeline_job_find_timed_out(self) -> list[dict]:
        return [dict(r) for r in self.timed_out]


class FakeAgentPool:
    def __init__(self, agents: list[dict]) -> None:
        self._agents = agents
        self._available: set[str] = {a["id"] for a in agents}

    async def select_agent(self, preferred: str | None = None) -> dict | None:
        if preferred and preferred in self._available:
            for a in self._agents:
                if a["id"] == preferred:
                    return a
        # fallback: 最初の available
        for a in self._agents:
            if a["id"] in self._available:
                return a
        return None

    def mark_unavailable(self, agent_id: str) -> None:
        self._available.discard(agent_id)


class FakeUnit:
    """broadcast_event を記録するだけの最小ユニット。"""

    def __init__(self) -> None:
        self.events: list[TransitionEvent] = []

    async def broadcast_event(self, ev: TransitionEvent) -> None:
        self.events.append(ev)


class FakeAgentClient:
    """AgentClient の代替。capability / whisper / jobs 系を制御可能に。"""

    def __init__(
        self,
        *,
        capability_models: list[str] | None = None,
        capability_raises: Exception | None = None,
        job_start_raises: Exception | None = None,
        whisper_sync_raises: Exception | None = None,
        sync_id: str = "wsync_abc",
    ) -> None:
        self._cap_models = capability_models if capability_models is not None else []
        self._cap_raises = capability_raises
        self._job_start_raises = job_start_raises
        self._sync_raises = whisper_sync_raises
        self._sync_id = sync_id
        self.job_start_calls: list[dict] = []
        self.whisper_sync_calls: list[dict] = []

    async def capability(self) -> dict:
        if self._cap_raises is not None:
            raise self._cap_raises
        return {
            "whisper_models_local": list(self._cap_models),
            "busy": False,
        }

    async def whisper_cache_sync(self, *, model: str, sha256: str | None = None) -> dict:
        self.whisper_sync_calls.append({"model": model, "sha256": sha256})
        if self._sync_raises is not None:
            raise self._sync_raises
        return {"sync_id": self._sync_id}

    async def job_start(self, **kwargs) -> dict:
        self.job_start_calls.append(kwargs)
        if self._job_start_raises is not None:
            raise self._job_start_raises
        return {"ok": True}

    async def job_stream(self, job_id: str):
        # テスト側では per-job の monitor は subscribe しないので即終了で十分
        if False:
            yield  # pragma: no cover
        return

    async def whisper_cache_sync_stream(self, sync_id: str):
        if False:
            yield  # pragma: no cover
        return

    async def close(self) -> None:
        pass


# =========================================================================
#   Helpers
# =========================================================================


def _make_bot(db: FakeDB, agents: list[dict], config: dict | None = None) -> SimpleNamespace:
    cfg = {
        "units": {
            "clip_pipeline": config or {
                "dispatcher": {"progress_debounce_seconds": 0.0},
                "retry": {"max_retries": 2, "base_backoff_seconds": 30.0,
                          "max_backoff_seconds": 300.0},
                "timeouts": {},
                "nas": {"base_path": "/mnt/secretary-bot/auto-kirinuki",
                        "outputs_subdir": "outputs"},
            }
        }
    }
    pool = FakeAgentPool(agents)
    um = SimpleNamespace(agent_pool=pool)
    bot = SimpleNamespace(
        database=db,
        unit_manager=um,
        config=cfg,
    )
    bot.__dict__["_pool"] = pool  # テストから直接触れるよう
    return bot


def _job(job_id: str = "j1", *, whisper: str = "large-v3",
         status: str = STATUS_DISPATCHING, retry_count: int = 0) -> dict:
    return {
        "id": job_id, "user_id": "u", "platform": "webgui",
        "status": status, "step": None, "progress": 0,
        "assigned_agent": None,
        "video_path": r"N:\auto-kirinuki\inputs\stream01.mp4",
        "output_dir": r"N:\auto-kirinuki\outputs\stream01",
        "whisper_model": whisper, "ollama_model": "qwen3:14b",
        "params_json": json.dumps({"top_n": 5}),
        "retry_count": retry_count, "max_retries": 2,
        "last_error": None, "cache_sync_id": None,
    }


def _install_agent_client(
    disp: Dispatcher, agent: dict, ac: FakeAgentClient,
) -> None:
    disp._agent_clients[agent["id"]] = ac  # type: ignore[assignment]


# =========================================================================
#   Tests
# =========================================================================


@pytest.fixture
def agents() -> list[dict]:
    return [
        {"id": "sub-pc", "priority": 1, "host": "127.0.0.1", "port": 7777},
        {"id": "main-pc", "priority": 2, "host": "127.0.0.2", "port": 7777},
    ]


@pytest.fixture
def wire(agents):
    db = FakeDB()
    bot = _make_bot(db, agents)
    unit = FakeUnit()
    d = Dispatcher(bot, unit)
    return d, db, unit, agents, bot


async def test_happy_path_dispatching_to_running(wire):
    """cache OK のとき dispatching → running に遷移し、assigned_agent が sub-pc。"""
    d, db, unit, agents, _ = wire
    job = _job()
    db.insert(job)
    ac = FakeAgentClient(capability_models=["large-v3"])
    _install_agent_client(d, agents[0], ac)

    await d._handle_dispatching(job)

    # DB: status=running, assigned_agent=sub-pc
    self_job = db.jobs["j1"]
    assert self_job["status"] == STATUS_RUNNING
    assert self_job["assigned_agent"] == "sub-pc"
    # Agent への job_start が 1 回走った
    assert len(ac.job_start_calls) == 1
    assert ac.job_start_calls[0]["job_id"] == "j1"
    # broadcast: to_status=RUNNING が少なくとも 1 本
    assert any(e.to_status == STATUS_RUNNING for e in unit.events)


async def test_cache_missing_goes_to_warming_cache(wire):
    """whisper モデルがローカルに無ければ warming_cache へ。"""
    d, db, unit, agents, _ = wire
    job = _job(whisper="large-v3")
    db.insert(job)
    # capability が空リストを返す → cache_needed=True
    ac = FakeAgentClient(capability_models=[], sync_id="wsync_XYZ")
    _install_agent_client(d, agents[0], ac)

    await d._handle_dispatching(job)

    assert db.jobs["j1"]["status"] == STATUS_WARMING_CACHE
    assert db.jobs["j1"]["cache_sync_id"] == "wsync_XYZ"
    assert ac.whisper_sync_calls == [{"model": "large-v3", "sha256": None}]
    # job_start は "まだ" 呼ばれない（cache 完了後に別経路で呼ばれる）
    assert ac.job_start_calls == []


async def test_capability_failure_treated_as_cache_missing(wire):
    """capability 取得に失敗したら安全側で cache sync に回す。"""
    d, db, unit, agents, _ = wire
    job = _job()
    db.insert(job)
    ac = FakeAgentClient(
        capability_raises=AgentCommunicationError("timeout"),
        sync_id="wsync_fallback",
    )
    _install_agent_client(d, agents[0], ac)

    await d._handle_dispatching(job)

    assert db.jobs["j1"]["status"] == STATUS_WARMING_CACHE
    assert db.jobs["j1"]["cache_sync_id"] == "wsync_fallback"


async def test_no_agent_available_retries_without_budget(wire):
    """全 Agent busy のとき budget を消費せず queued に戻す。"""
    d, db, unit, agents, bot = wire
    job = _job(retry_count=1)
    db.insert(job)
    # 両 Agent に先行ジョブあり
    db.active_counts = {"sub-pc": 1, "main-pc": 1}

    await d._handle_dispatching(job)

    # 戻り先は queued（retry）、retry_count は増えない
    assert db.jobs["j1"]["status"] == STATUS_QUEUED
    # _transition_retry はこの経路で consume_budget=False なので update_fields に
    # retry_count キーを書かない
    last = [u for u in db.update_log if u["ok"]][-1]
    assert "retry_count" not in last["fields"]
    # broadcast: from=DISPATCHING, to=QUEUED
    assert any(
        e.from_status == STATUS_DISPATCHING and e.to_status == STATUS_QUEUED
        for e in unit.events
    )


async def test_validation_error_on_start_goes_failed(wire):
    """job_start が ValidationError を投げたら failed へ（リトライ不可）。"""
    d, db, unit, agents, _ = wire
    job = _job()
    db.insert(job)
    ac = FakeAgentClient(
        capability_models=["large-v3"],
        job_start_raises=ValidationError("video_path not found"),
    )
    _install_agent_client(d, agents[0], ac)

    await d._handle_dispatching(job)

    assert db.jobs["j1"]["status"] == STATUS_FAILED
    # broadcast の `error` イベントが出ている
    assert any(e.event == "error" for e in unit.events)


async def test_agent_busy_retries_without_budget(wire):
    """ResourceUnavailableError("busy") はレース対策扱いで budget 据え置き。"""
    d, db, unit, agents, _ = wire
    job = _job(retry_count=1)
    db.insert(job)
    ac = FakeAgentClient(
        capability_models=["large-v3"],
        job_start_raises=ResourceUnavailableError("agent busy with job foo"),
    )
    _install_agent_client(d, agents[0], ac)

    await d._handle_dispatching(job)

    assert db.jobs["j1"]["status"] == STATUS_QUEUED
    last = [u for u in db.update_log if u["ok"]][-1]
    # budget 据え置きなので retry_count は書き戻されない
    assert "retry_count" not in last["fields"]


async def test_transient_error_consumes_budget(wire):
    """その他の retryable なエラーは budget を消費しつつ queued に戻す。"""
    d, db, unit, agents, _ = wire
    job = _job(retry_count=0)
    db.insert(job)
    ac = FakeAgentClient(
        capability_models=["large-v3"],
        job_start_raises=TransientError("transient blip"),
    )
    _install_agent_client(d, agents[0], ac)

    await d._handle_dispatching(job)

    assert db.jobs["j1"]["status"] == STATUS_QUEUED
    last = [u for u in db.update_log if u["ok"]][-1]
    assert last["fields"].get("retry_count") == 1
    assert last["fields"].get("last_error") == "transient blip"


async def test_retry_budget_exhausted_becomes_failed(wire):
    """max_retries に達していたら failed。"""
    d, db, unit, agents, _ = wire
    job = _job(status=STATUS_RUNNING, retry_count=2)   # == max_retries
    db.insert(job)

    await d._transition_retry(
        job, reason="boom", from_status=STATUS_RUNNING,
        last_error="no more budget",
    )

    assert db.jobs["j1"]["status"] == STATUS_FAILED
    # broadcast も error で出る
    assert any(e.event == "error" and e.to_status == STATUS_FAILED
               for e in unit.events)


async def test_on_job_done_writes_result_and_done(wire):
    """result 受領 → set_result + status=done + progress=100 + 正規化パス。"""
    d, db, unit, agents, _ = wire
    job = _job(status=STATUS_RUNNING)
    db.insert(job)
    db.jobs["j1"]["status"] = STATUS_RUNNING  # running に戻す

    result = {
        "transcript_path": r"N:\auto-kirinuki\outputs\stream01\transcript.json",
        "edl_path":        r"N:\auto-kirinuki\outputs\stream01\timeline.edl",
        "clip_paths":      [r"N:\auto-kirinuki\outputs\stream01\clips\c1.mp4"],
        "highlights_count": 3,
    }
    await d._on_job_done("j1", result, "sub-pc")

    # DB status
    assert db.jobs["j1"]["status"] == STATUS_DONE
    assert db.jobs["j1"]["progress"] == 100
    # set_result の内容が正規化されている
    assert db.result_log
    saved = json.loads(db.result_log[-1][1])
    assert saved["edl_path"] == (
        "/mnt/secretary-bot/auto-kirinuki/outputs/stream01/timeline.edl"
    )
    assert saved["clip_paths"][0].startswith(
        "/mnt/secretary-bot/auto-kirinuki/outputs/stream01/clips/"
    )


async def test_select_agent_skips_in_memory_reserved(wire, agents):
    """_dispatching_to に登録された Agent は他ジョブからは選ばれない。"""
    d, db, _, _, _ = wire
    # sub-pc は別ジョブが握っている
    d._dispatching_to["sub-pc"] = "other-job"

    sel = await d._select_agent_for_job({"id": "j1"})
    assert sel is not None
    assert sel["id"] == "main-pc"


async def test_select_agent_none_when_all_busy_or_active(wire, agents):
    """全 Agent が active_count>0 または pool 側 unavailable なら None。"""
    d, db, _, _, bot = wire
    db.active_counts = {"sub-pc": 1, "main-pc": 1}

    sel = await d._select_agent_for_job({"id": "j1"})
    assert sel is None


async def test_on_step_updates_step_without_progress(wire):
    """step イベントは step を更新するが progress は触らない。"""
    d, db, unit, _, _ = wire
    job = _job(status=STATUS_RUNNING)
    db.insert(job)
    db.jobs["j1"]["progress"] = 42

    await d._on_step("j1", "transcribe", "sub-pc", {"step": "transcribe"})

    assert db.jobs["j1"]["step"] == "transcribe"
    assert db.jobs["j1"]["progress"] == 42  # 変わらず
    assert db.step_log == [("j1", "transcribe")]


async def test_on_progress_debounce_zero_writes_every_time(wire):
    """debounce=0 の設定下では progress 更新が都度 DB に届く。"""
    d, db, unit, _, _ = wire
    job = _job(status=STATUS_RUNNING)
    db.insert(job)

    await d._on_progress("j1", 10, "transcribe", "sub-pc", {})
    await d._on_progress("j1", 30, "transcribe", "sub-pc", {})
    await d._on_progress("j1", 50, None, "sub-pc", {})  # 前回 step が補完

    pcts = [p for (_jid, p, _step) in db.progress_log]
    steps = [s for (_jid, _p, s) in db.progress_log]
    assert pcts == [10, 30, 50]
    assert steps == ["transcribe", "transcribe", "transcribe"]
