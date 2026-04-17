"""Image Gen Console 同居ツールのエントリポイント。

`register(app, bot)` で FastAPI アプリに統合する。

API 本体（/api/image/*, /api/generation/*）は src/web/app.py に既存のものを
そのまま使う。本ツールは独立 SPA を /tools/image-gen/ で配信するだけ。
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.logger import get_logger

log = get_logger(__name__)

_THIS_DIR = os.path.dirname(__file__)
_STATIC_DIR = os.path.join(_THIS_DIR, "static")


def register(app: FastAPI, bot) -> None:
    """FastAPI アプリに Image Gen Console を組み込む。"""
    if not os.path.isdir(_STATIC_DIR):
        log.warning("Image Gen Console static dir not found: %s", _STATIC_DIR)
        return

    app.mount(
        "/tools/image-gen/static",
        StaticFiles(directory=_STATIC_DIR),
        name="image_gen_console_static",
    )

    # SPA エントリ（末尾スラッシュ有無どちらでも）
    # JS/CSS はバージョンクエリでキャッシュバスト、HTML 自体は no-cache。
    @app.get("/tools/image-gen/")
    @app.get("/tools/image-gen")
    async def _image_gen_index():
        from fastapi.responses import HTMLResponse
        import hashlib
        import glob as _glob
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
        html = html.replace('href="static/css/image_gen.css"', f'href="static/css/image_gen.css?v={ver}"')
        return HTMLResponse(
            content=html,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    log.info("Image Gen Console registered at /tools/image-gen")
