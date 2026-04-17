"""画像生成基盤の軽量データモデル（dataclass）。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ジョブ状態の定数（文字列）
STATUS_QUEUED = "queued"
STATUS_DISPATCHING = "dispatching"
STATUS_WARMING_CACHE = "warming_cache"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED})
NON_TERMINAL_STATUSES = frozenset({
    STATUS_QUEUED, STATUS_DISPATCHING, STATUS_WARMING_CACHE, STATUS_RUNNING,
})

# プラットフォーム
PLATFORM_DISCORD = "discord"
PLATFORM_WEB = "web"


@dataclass
class JobStatus:
    """ジョブ状態のスナップショット。WebGUI/Discord にそのまま返せる形。"""
    job_id: str
    user_id: str
    platform: str
    workflow_id: int | None
    workflow_name: str | None
    status: str
    progress: int
    assigned_agent: str | None
    positive: str | None
    negative: str | None
    params: dict[str, Any]
    result_paths: list[str]
    result_kinds: list[str]
    modality: str
    last_error: str | None
    retry_count: int
    max_retries: int
    created_at: str | None
    started_at: str | None
    finished_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TransitionEvent:
    """Dispatcher 遷移イベント（pub/sub 用）。"""
    job_id: str
    from_status: str | None
    to_status: str
    progress: int = 0
    event: str = "status"          # "status" | "progress" | "result" | "error"
    agent_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowRequirements:
    """ワークフローの依存抽出結果。"""
    nodes: list[str] = field(default_factory=list)          # class_type 一覧
    models: list[dict[str, str]] = field(default_factory=list)   # [{type, filename}]
    loras: list[dict[str, str]] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)        # {{VAR}} 一覧


# デフォルトパラメータ（t2i_default 想定）
DEFAULT_PARAMS: dict[str, Any] = {
    "WIDTH": 1024,
    "HEIGHT": 1024,
    "STEPS": 30,
    "CFG": 5.5,
    "SAMPLER": "euler_ancestral",
    "SCHEDULER": "karras",
    "SEED": -1,  # -1 は Pi 側で乱数生成
}
