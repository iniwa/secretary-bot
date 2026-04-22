"""ClipPipelineUnit の単体ロジックテスト。

BaseUnit (discord.py Cog) フル構築は重いので、`object.__new__` でバイパスして
bot.config / database / _event_subscribers だけセットした軽量インスタンスで検証する。
出力ディレクトリ解決・broadcast_event・cancel_job のブランチを確認。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.units.clip_pipeline.models import (
    PLATFORM_WEBGUI,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_RUNNING,
    TransitionEvent,
)
from src.units.clip_pipeline.unit import ClipPipelineUnit


# =========================================================================
#   Fakes
# =========================================================================


class FakeDB:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.insert_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self._next_id = 1

    async def clip_pipeline_job_insert(
        self, *, user_id, platform, video_path, output_dir,
        whisper_model, ollama_model, params_json, max_retries,
    ) -> str:
        job_id = f"job{self._next_id:03d}"
        self._next_id += 1
        self.insert_calls.append({
            "job_id": job_id, "user_id": user_id, "platform": platform,
            "video_path": video_path, "output_dir": output_dir,
            "whisper_model": whisper_model, "ollama_model": ollama_model,
            "params_json": params_json, "max_retries": max_retries,
        })
        self.jobs[job_id] = {
            "id": job_id, "status": "queued",
            "video_path": video_path, "output_dir": output_dir,
            "whisper_model": whisper_model, "ollama_model": ollama_model,
            "params_json": params_json,
            "user_id": user_id, "platform": platform,
            "max_retries": max_retries,
        }
        return job_id

    async def clip_pipeline_job_get(self, job_id: str) -> dict | None:
        j = self.jobs.get(job_id)
        return dict(j) if j else None

    async def clip_pipeline_job_cancel(self, job_id: str) -> bool:
        self.cancel_calls.append(job_id)
        j = self.jobs.get(job_id)
        if not j:
            return False
        if j["status"] in ("done", "failed", "cancelled"):
            return False
        j["status"] = "cancelled"
        return True


class FakeDispatcher:
    def __init__(self) -> None:
        self.wake_count = 0

    def wake(self) -> None:
        self.wake_count += 1

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _build_unit(cfg: dict | None = None) -> tuple[ClipPipelineUnit, FakeDB, FakeDispatcher]:
    """BaseUnit の重い __init__ を回避して最小インスタンスを作る。"""
    db = FakeDB()
    config = {"units": {"clip_pipeline": cfg or {}}}
    bot = SimpleNamespace(
        database=db,
        config=config,
        unit_manager=SimpleNamespace(agent_pool=SimpleNamespace(_agents=[])),
    )
    u = object.__new__(ClipPipelineUnit)
    u.bot = bot  # type: ignore[attr-defined]
    u.dispatcher = FakeDispatcher()  # type: ignore[attr-defined]
    u._event_subscribers = set()
    u._started = True
    u._discord_jobs = {}
    u._discord_notifier_task = None
    return u, db, u.dispatcher  # type: ignore[return-value]


# =========================================================================
#   _default_output_dir_for
# =========================================================================


class TestDefaultOutputDir:
    """Agent 側 Windows パスと Pi POSIX の両方を解決できるか。"""

    def test_agent_outputs_base_takes_priority(self):
        u, _, _ = _build_unit({
            "nas": {
                "agent_outputs_base": r"N:\auto-kirinuki\outputs",
                "agent_base_path": r"N:\auto-kirinuki",
                "outputs_subdir": "outputs",
                "base_path": "/mnt/secretary-bot/auto-kirinuki",
            }
        })
        out = u._default_output_dir_for(r"D:\videos\stream01.mp4")
        assert out == r"N:\auto-kirinuki\outputs\stream01"

    def test_agent_base_path_fallback(self):
        u, _, _ = _build_unit({
            "nas": {
                "agent_base_path": r"N:\auto-kirinuki",
                "outputs_subdir": "outputs",
            }
        })
        out = u._default_output_dir_for(r"D:\videos\s2.mkv")
        assert out == r"N:\auto-kirinuki\outputs\s2"

    def test_posix_fallback_when_no_agent_base(self):
        u, _, _ = _build_unit({
            "nas": {"base_path": "/mnt/secretary-bot/auto-kirinuki",
                    "outputs_subdir": "outputs"}
        })
        out = u._default_output_dir_for("/home/iniwa/videos/big.mp4")
        assert out == "/mnt/secretary-bot/auto-kirinuki/outputs/big"

    def test_unknown_stem_when_basename_empty(self):
        u, _, _ = _build_unit({
            "nas": {"agent_outputs_base": r"N:\outputs"}
        })
        # basename なし → "unknown" にフォールバック
        out = u._default_output_dir_for("/")
        assert out == r"N:\outputs\unknown"

    def test_windows_path_with_posix_base_keeps_base_sep(self):
        """base が POSIX でも動画パスが Windows 形式のケース: base の形式に従う。"""
        u, _, _ = _build_unit({
            "nas": {"base_path": "/mnt/secretary-bot/auto-kirinuki",
                    "outputs_subdir": "outputs"}
        })
        out = u._default_output_dir_for(r"D:\videos\winstream.mp4")
        assert out == "/mnt/secretary-bot/auto-kirinuki/outputs/winstream"


# =========================================================================
#   enqueue
# =========================================================================


async def test_enqueue_requires_video_path():
    u, _, _ = _build_unit()
    with pytest.raises(Exception) as exc:
        await u.enqueue(
            user_id="u1", platform=PLATFORM_WEBGUI, video_path="",
        )
    # ValidationError は src.errors 由来
    assert "video_path" in str(exc.value)


async def test_enqueue_applies_defaults_and_wakes_dispatcher():
    u, db, disp = _build_unit({
        "default_whisper_model": "medium",
        "default_ollama_model": "qwen3:7b",
        "nas": {"agent_outputs_base": r"N:\auto-kirinuki\outputs"},
        "retry": {"max_retries": 5},
    })

    # 購読者を 1 件登録して broadcast も確認
    q: asyncio.Queue = asyncio.Queue()
    u.subscribe_events(q)

    job_id = await u.enqueue(
        user_id="u1", platform=PLATFORM_WEBGUI,
        video_path=r"N:\auto-kirinuki\inputs\stream.mp4",
    )

    assert job_id.startswith("job")
    call = db.insert_calls[0]
    assert call["whisper_model"] == "medium"
    assert call["ollama_model"] == "qwen3:7b"
    assert call["output_dir"] == r"N:\auto-kirinuki\outputs\stream"
    assert call["max_retries"] == 5
    # DEFAULT_PARAMS が merge されている
    params = json.loads(call["params_json"])
    assert params.get("top_n") == 10
    assert params.get("do_export_clips") is True

    # Dispatcher.wake が呼ばれ、queued broadcast が届く
    assert disp.wake_count == 1
    ev = await q.get()
    assert ev["status"] == "queued"
    assert ev["from_status"] is None


async def test_enqueue_params_override_defaults():
    u, db, _ = _build_unit({
        "nas": {"agent_outputs_base": r"N:\auto-kirinuki\outputs"},
    })
    await u.enqueue(
        user_id="u1", platform=PLATFORM_WEBGUI,
        video_path=r"N:\vid\a.mp4",
        whisper_model="large-v3",
        ollama_model="gemma4:e2b",
        params={"top_n": 3, "use_demucs": True, "mic_track": 0},
    )
    params = json.loads(db.insert_calls[0]["params_json"])
    assert params["top_n"] == 3
    assert params["use_demucs"] is True
    assert params["mic_track"] == 0
    # 未指定項目は DEFAULT_PARAMS のまま
    assert params["do_export_clips"] is True


# =========================================================================
#   cancel_job
# =========================================================================


async def test_cancel_nonexistent_returns_false():
    u, _, _ = _build_unit()
    assert (await u.cancel_job("unknown")) is False


async def test_cancel_non_terminal_flips_to_cancelled():
    u, db, _ = _build_unit()
    db.jobs["jX"] = {
        "id": "jX", "status": "running",
        "assigned_agent": None,  # agent client を作らない
        "video_path": "", "output_dir": "",
        "whisper_model": "", "ollama_model": "",
        "params_json": "{}",
    }
    q: asyncio.Queue = asyncio.Queue()
    u.subscribe_events(q)

    ok = await u.cancel_job("jX")
    assert ok is True
    assert db.jobs["jX"]["status"] == "cancelled"
    # broadcast: from=running, to=cancelled
    ev = await q.get()
    assert ev["from_status"] == "running"
    assert ev["status"] == STATUS_CANCELLED


async def test_cancel_already_terminal_returns_false():
    u, db, _ = _build_unit()
    db.jobs["jDone"] = {
        "id": "jDone", "status": "done",
        "assigned_agent": None,
        "video_path": "", "output_dir": "",
        "whisper_model": "", "ollama_model": "",
        "params_json": "{}",
    }
    ok = await u.cancel_job("jDone")
    assert ok is False


# =========================================================================
#   broadcast_event
# =========================================================================


async def test_broadcast_fans_out_and_drops_dead_queues():
    u, _, _ = _build_unit()

    # 生きてる購読者 2、満杯の購読者 1（死にかけ）
    q_live_1: asyncio.Queue = asyncio.Queue()
    q_live_2: asyncio.Queue = asyncio.Queue()
    q_full: asyncio.Queue = asyncio.Queue(maxsize=1)
    q_full.put_nowait({"already": "full"})  # 1 件で満杯

    u.subscribe_events(q_live_1)
    u.subscribe_events(q_live_2)
    u.subscribe_events(q_full)

    ev = TransitionEvent(
        job_id="jX", from_status="queued", to_status=STATUS_RUNNING,
        progress=5, event="status",
    )
    await u.broadcast_event(ev)

    # 生きてる 2 本には payload が届く
    m1 = q_live_1.get_nowait()
    m2 = q_live_2.get_nowait()
    assert m1["status"] == STATUS_RUNNING
    assert m2["job_id"] == "jX"
    # 満杯キューは購読者集合から除外される
    assert q_full not in u._event_subscribers
    assert q_live_1 in u._event_subscribers
    assert q_live_2 in u._event_subscribers


def test_row_to_dict_decodes_json_fields():
    u, _, _ = _build_unit()
    row = {
        "id": "abc", "user_id": "u", "platform": "webgui",
        "status": "done", "step": "clips", "progress": 100,
        "assigned_agent": "sub-pc",
        "video_path": "v", "output_dir": "o",
        "whisper_model": "large-v3", "ollama_model": "qwen3:14b",
        "params_json": '{"top_n": 5}',
        "result_json": '{"edl_path": "X", "highlights_count": 5}',
        "last_error": None, "retry_count": 1, "max_retries": 2,
        "cache_sync_id": "wsync_1",
        "created_at": None, "started_at": None, "finished_at": None,
    }
    out = u._row_to_dict(row)
    assert out["job_id"] == "abc"
    assert out["params"] == {"top_n": 5}
    assert out["result"] == {"edl_path": "X", "highlights_count": 5}
    assert out["retry_count"] == 1

    # 壊れた JSON はフォールバックで {} / None
    broken = dict(row, params_json="not-json", result_json="also-broken")
    o2 = u._row_to_dict(broken)
    assert o2["params"] == {}
    assert o2["result"] is None
