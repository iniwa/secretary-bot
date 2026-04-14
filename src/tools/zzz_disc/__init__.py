"""ZZZ Disc Manager 同居ツールのエントリポイント。

`register(app, bot)` で FastAPI アプリに統合する。
config.yaml の `tools.zzz_disc.enabled` が False ならノーオペ。
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.logger import get_logger
from . import models
from .routes import build_router
from .job_queue import ZzzDiscJobQueue

log = get_logger(__name__)

_THIS_DIR = os.path.dirname(__file__)
_STATIC_DIR = os.path.join(_THIS_DIR, "static")
_MASTER_DIR = os.path.join(_THIS_DIR, "master_data")


def _load_config(bot) -> dict | None:
    """bot.config から tools.zzz_disc 設定を拾う。無ければ None。"""
    cfg = getattr(bot, "config", None) or {}
    tools = cfg.get("tools") or {}
    return tools.get("zzz_disc")


async def _seed_master_data(db) -> None:
    """master_data/*.json を INSERT OR IGNORE で投入。"""
    chars_path = os.path.join(_MASTER_DIR, "characters.json")
    sets_path = os.path.join(_MASTER_DIR, "sets.json")
    if os.path.exists(chars_path):
        try:
            with open(chars_path, encoding="utf-8") as f:
                characters = json.load(f)
            for c in characters:
                await models.upsert_character(
                    db,
                    slug=c["slug"],
                    name_ja=c["name_ja"],
                    element=c.get("element"),
                    faction=c.get("faction"),
                    icon_url=c.get("icon_url"),
                    display_order=int(c.get("display_order", 0)),
                )
        except Exception as e:
            log.warning("failed to seed zzz_characters: %s", e)
    if os.path.exists(sets_path):
        try:
            with open(sets_path, encoding="utf-8") as f:
                sets = json.load(f)
            for s in sets:
                await models.upsert_set_master(
                    db,
                    slug=s["slug"],
                    name_ja=s["name_ja"],
                    aliases=s.get("aliases") or [],
                    two_pc_effect=s.get("two_pc_effect"),
                    four_pc_effect=s.get("four_pc_effect"),
                )
        except Exception as e:
            log.warning("failed to seed zzz_set_masters: %s", e)


def register(app: FastAPI, bot) -> None:
    """FastAPI アプリに ZZZ Disc Manager を組み込む。"""
    cfg = _load_config(bot)
    if not cfg or not cfg.get("enabled"):
        log.info("ZZZ Disc Manager is disabled (tools.zzz_disc.enabled != true)")
        return

    # bot 側で images_dir を決める
    images_dir = cfg.get("images_dir") or "/app/data/zzz_disc_images"
    queue_cfg = cfg.get("queue") or {}
    max_concurrent = int(queue_cfg.get("max_concurrent", 1))
    history_retention = int(queue_cfg.get("history_retention", 200))

    # スキーマ初期化 + マスタ初期投入 + ジョブキュー起動 は startup イベントで行う
    @app.on_event("startup")
    async def _zzz_disc_startup():
        try:
            await models.init_schema(bot.database)
            await _seed_master_data(bot.database)
            jq = ZzzDiscJobQueue(
                bot,
                max_concurrent=max_concurrent,
                history_retention=history_retention,
                images_dir=images_dir,
            )
            await jq.start()
            bot._zzz_disc_job_queue = jq
            log.info("ZZZ Disc Manager registered at /tools/zzz-disc")
        except Exception as e:
            log.exception("ZZZ Disc Manager startup failed: %s", e)

    @app.on_event("shutdown")
    async def _zzz_disc_shutdown():
        jq = getattr(bot, "_zzz_disc_job_queue", None)
        if jq is not None:
            try:
                await jq.stop()
            except Exception:
                pass

    # 静的配信
    if os.path.isdir(_STATIC_DIR):
        app.mount(
            "/tools/zzz-disc/static",
            StaticFiles(directory=_STATIC_DIR),
            name="zzz_disc_static",
        )

    # SPA エントリ（末尾スラッシュ有無どちらでも）
    @app.get("/tools/zzz-disc/")
    @app.get("/tools/zzz-disc")
    async def _zzz_disc_index():
        index_path = os.path.join(_STATIC_DIR, "index.html")
        if not os.path.exists(index_path):
            return {"error": "index.html not found", "static_dir": _STATIC_DIR}
        return FileResponse(index_path)

    # API ルータ
    router = build_router(bot, {
        "match_threshold": float(cfg.get("match_threshold", 3.0)),
        "images_dir": images_dir,
    })
    app.include_router(router, prefix="/tools/zzz-disc")
