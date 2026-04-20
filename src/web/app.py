"""FastAPI WebGUI + /health エンドポイント。"""

import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from src.logger import get_logger
from src.web._context import WebContext
from src.web.cache_headers import NO_CACHE_HEADERS, NoCacheStaticFiles
from src.web.routes import register_all_routes

log = get_logger(__name__)


def create_web_app(bot) -> FastAPI:
    app = FastAPI(title="Secretary Bot WebGUI")

    async def _verify():
        pass

    # WebGUIはシングルユーザー想定なのでロック1つ
    _webgui_lock = asyncio.Lock()
    # update-code / input-relay update の2重実行防止用（chatとは独立）
    _update_lock = asyncio.Lock()
    # Agent 再起動タイムスタンプ (agent_id → unix time) - Restarting 状態判定に使用
    _agent_restart_ts: dict[str, float] = {}
    _RESTART_WINDOW_SEC = 60  # この時間内に alive=False の Agent は "restarting" とみなす

    def _mark_agent_restarting(agent_id: str):
        import time
        _agent_restart_ts[str(agent_id)] = time.time()

    def _mark_agents_restarting_bulk(results: list[dict]):
        """restart-self を呼んだ結果リストから成功分だけマークする。"""
        for r in results or []:
            if r.get("success"):
                aid = r.get("id")
                if aid:
                    _mark_agent_restarting(aid)

    ctx = WebContext(
        bot=bot,
        verify=_verify,
        webgui_lock=_webgui_lock,
        update_lock=_update_lock,
        agent_restart_ts=_agent_restart_ts,
        webgui_user_id=os.environ.get("WEBGUI_USER_ID", ""),
        restart_window_sec=_RESTART_WINDOW_SEC,
        mark_agent_restarting=_mark_agent_restarting,
        mark_agents_restarting_bulk=_mark_agents_restarting_bulk,
    )

    register_all_routes(app, ctx)

    # --- 静的ファイル & フロントエンド ---

    # Cloudflare / ブラウザの ES モジュールキャッシュ対策
    # JS/CSS は常にオリジンに再検証させる（同居ツールの静的配信にも適用）
    _STATIC_PREFIXES = ("/static/", "/tools/zzz-disc/static/")

    @app.middleware("http")
    async def static_cache_control(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in _STATIC_PREFIXES) \
                and path.rsplit(".", 1)[-1] in ("js", "css"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    # ZZZ Disc Manager（同居ツール）: tools.zzz_disc.enabled=true のときのみ有効
    try:
        from src.tools.zzz_disc import register as register_zzz_disc
        register_zzz_disc(app, bot)
    except Exception as e:
        log.warning(f"ZZZ Disc Manager register failed: {e}")

    # Image Gen Console（同居ツール）: 独立 SPA を /tools/image-gen/ で配信
    try:
        from src.tools.image_gen_console import register as register_image_gen_console
        register_image_gen_console(app, bot)
    except Exception as e:
        log.warning(f"Image Gen Console register failed: {e}")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", NoCacheStaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        try:
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            # Cache busting: append version query to JS/CSS references
            import glob as _glob
            import hashlib
            static_dir_path = os.path.join(os.path.dirname(__file__), "static")
            h = hashlib.md5()
            for p in sorted(_glob.glob(os.path.join(static_dir_path, "**", "*.js"), recursive=True)):
                h.update(str(os.path.getmtime(p)).encode())
            ver = h.hexdigest()[:8]
            html = html.replace('src="/static/js/app.js"', f'src="/static/js/app.js?v={ver}"')
            html = html.replace('href="/static/css/base.css"', f'href="/static/css/base.css?v={ver}"')
            html = html.replace('href="/static/css/layout.css"', f'href="/static/css/layout.css?v={ver}"')
            html = html.replace('href="/static/css/components.css"', f'href="/static/css/components.css?v={ver}"')
            return HTMLResponse(content=html, headers=NO_CACHE_HEADERS)
        except FileNotFoundError:
            return HTMLResponse(
                content="<h1>Secretary Bot WebGUI</h1><p>static/index.html not found</p>",
                headers=NO_CACHE_HEADERS,
            )

    return app
