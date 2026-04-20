"""clip_pipeline tool — Windows Agent 側の配信切り抜きジョブ実行。

API 仕様: secretary-bot/docs/auto_kirinuki/api.md （Pi 側 dispatcher が呼ぶ）
"""
from .router import init_clip_pipeline, router

__all__ = ["router", "init_clip_pipeline"]
