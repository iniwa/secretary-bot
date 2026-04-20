"""ZZZ Disc Manager 同居ツールのエントリポイント。

`register(app, bot)` で FastAPI アプリに統合する。
config.yaml の `tools.zzz_disc.enabled` が False ならノーオペ。
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI

from src.logger import get_logger
from src.web.cache_headers import NO_CACHE_HEADERS, NoCacheStaticFiles

from . import models
from .job_queue import ZzzDiscJobQueue
from .routes import build_router

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
    """キャラのみ master_data/characters.json から投入。

    sets.json は seed しない（HoYoLAB 同期が JP 名で動的に作成するため、
    seed すると未使用な英語 slug 行が選択肢に大量に出てしまう）。
    """
    chars_path = os.path.join(_MASTER_DIR, "characters.json")
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
                    hoyolab_agent_id=(str(c["hoyolab_agent_id"])
                                      if c.get("hoyolab_agent_id") is not None else None),
                )
        except Exception as e:
            log.warning("failed to seed zzz_characters: %s", e)


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
    vlm_model = cfg.get("vlm_model") or "gemma4"

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
                vlm_model=vlm_model,
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
            NoCacheStaticFiles(directory=_STATIC_DIR),
            name="zzz_disc_static",
        )

    # SPA エントリ（末尾スラッシュ有無どちらでも）
    # Cloudflare / ブラウザキャッシュ対策として JS/CSS 参照にバージョンクエリを付与。
    # 加えて HTML 自体も no-cache で返し、更新直後に古い HTML が配られないようにする。
    @app.get("/tools/zzz-disc/")
    @app.get("/tools/zzz-disc")
    async def _zzz_disc_index():
        import glob as _glob
        import hashlib

        from fastapi.responses import HTMLResponse
        index_path = os.path.join(_STATIC_DIR, "index.html")
        if not os.path.exists(index_path):
            return {"error": "index.html not found", "static_dir": _STATIC_DIR}
        with open(index_path, encoding="utf-8") as f:
            html = f.read()
        h = hashlib.md5()
        for p in sorted(_glob.glob(os.path.join(_STATIC_DIR, "**", "*.js"), recursive=True)):
            h.update(str(os.path.getmtime(p)).encode())
        for p in sorted(_glob.glob(os.path.join(_STATIC_DIR, "**", "*.css"), recursive=True)):
            h.update(str(os.path.getmtime(p)).encode())
        ver = h.hexdigest()[:8]
        html = html.replace('src="static/js/app.js"', f'src="static/js/app.js?v={ver}"')
        html = html.replace('href="static/css/zzz_disc.css"', f'href="static/css/zzz_disc.css?v={ver}"')
        return HTMLResponse(content=html, headers=NO_CACHE_HEADERS)

    # API ルータ
    router = build_router(bot, {
        "match_threshold": float(cfg.get("match_threshold", 3.0)),
        "images_dir": images_dir,
    })
    app.include_router(router, prefix="/tools/zzz-disc")
