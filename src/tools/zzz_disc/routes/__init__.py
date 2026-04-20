"""ZZZ Disc Manager: FastAPI ルータ（ビルド中心モデル）。

- /api/masters: キャラ+セットマスタ
- /api/discs: CRUD + 使われているビルド
- /api/characters/{id}/builds: キャラの全ビルド（current + プリセット）
- /api/builds/*: ビルド編集・プリセット保存・スロット割当
- /api/shared-discs: 複数ビルドで共有されている disc の一覧
- /api/hoyolab/*: アカウント管理 + 同期
- /api/jobs: VLM 抽出キュー
- /api/team-groups / /api/teams: 編成モード

ドメインごとにサブモジュールへ分割し、build_router で合成する。
"""

from __future__ import annotations

from fastapi import APIRouter

from .builds import build_builds_router
from .characters import build_characters_router
from .discs import build_discs_router
from .hoyolab import build_hoyolab_router
from .jobs import build_jobs_router
from .teams import build_teams_router


def build_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    router.include_router(build_discs_router(bot, config))
    router.include_router(build_characters_router(bot, config))
    router.include_router(build_builds_router(bot, config))
    router.include_router(build_hoyolab_router(bot, config))
    router.include_router(build_jobs_router(bot, config))
    router.include_router(build_teams_router(bot, config))
    return router
