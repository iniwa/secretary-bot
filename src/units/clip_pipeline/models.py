"""auto-kirinuki（配信アーカイブ切り抜き）の軽量データモデル。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# === ジョブ状態 ===
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

# === パイプラインステップ ===
# Agent 側が逐次進めるサブステップ。progress 0-100 とは独立に "今どの段階か" を示す。
STEP_PREPROCESS = "preprocess"   # ffmpeg で音声抽出 / demucs で音源分離
STEP_TRANSCRIBE = "transcribe"   # Whisper で文字起こし
STEP_ANALYZE = "analyze"         # librosa で音響特徴量
STEP_EMOTION = "emotion"         # 感情スコアリング
STEP_HIGHLIGHT = "highlight"     # LLM でハイライト候補を抽出
STEP_EDL = "edl"                 # CMX 3600 EDL 生成
STEP_CLIPS = "clips"             # ffmpeg で切り抜き動画を書き出し

ALL_STEPS = (
    STEP_PREPROCESS, STEP_TRANSCRIBE, STEP_ANALYZE, STEP_EMOTION,
    STEP_HIGHLIGHT, STEP_EDL, STEP_CLIPS,
)

# ステップごとの progress 配分（合計 100）。Dispatcher が重み付き progress を算出する。
STEP_WEIGHTS: dict[str, int] = {
    STEP_PREPROCESS: 10,
    STEP_TRANSCRIBE: 35,
    STEP_ANALYZE: 10,
    STEP_EMOTION: 10,
    STEP_HIGHLIGHT: 10,
    STEP_EDL: 5,
    STEP_CLIPS: 20,
}

# === プラットフォーム ===
PLATFORM_DISCORD = "discord"
PLATFORM_WEBGUI = "webgui"


@dataclass
class JobStatus:
    """ジョブ状態のスナップショット。WebGUI/Discord にそのまま返せる形。"""
    job_id: str
    user_id: str
    platform: str
    status: str
    step: str | None
    progress: int
    assigned_agent: str | None
    video_path: str
    output_dir: str
    whisper_model: str
    ollama_model: str
    params: dict[str, Any]
    result: dict[str, Any] | None     # {transcript_path, highlights_count, edl_path, clip_paths[]}
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
    step: str | None = None
    event: str = "status"          # "status" | "progress" | "log" | "result" | "error"
    agent_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Discord / WebGUI からの入力デフォルト
DEFAULT_PARAMS: dict[str, Any] = {
    "top_n": 10,               # ハイライト候補数
    "min_clip_sec": 15,        # 切り抜き 1 本の下限秒数（満たない場合は前後に時間を足して確保）
    "do_export_clips": True,   # False なら EDL までで止める
    "mic_track": 1,            # マイク音声の trackindex（ffmpeg）
    "use_demucs": False,       # ソース分離を行うか
    "sleep_sec": 0,            # ジョブ間の間隔（バッチ用）
}
