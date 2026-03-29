"""FastAPI WebGUI + /health エンドポイント。"""

import asyncio
import os
import secrets
import subprocess

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from src.logger import get_logger

log = get_logger(__name__)


def create_web_app(bot) -> FastAPI:
    app = FastAPI(title="Secretary Bot WebGUI")
    security = HTTPBasic()

    _username = os.environ.get("WEBGUI_USERNAME", "admin")
    _password = os.environ.get("WEBGUI_PASSWORD", "")

    def _verify(credentials: HTTPBasicCredentials = Depends(security)):
        if not _password:
            return credentials
        ok_user = secrets.compare_digest(credentials.username, _username)
        ok_pass = secrets.compare_digest(credentials.password, _password)
        if not (ok_user and ok_pass):
            raise HTTPException(status_code=401, detail="Unauthorized",
                                headers={"WWW-Authenticate": "Basic"})
        return credentials

    # --- ヘルスチェック（認証不要） ---

    @app.get("/health")
    async def health():
        from src.bot import get_commit_hash, get_uptime_seconds
        return {
            "status": "ok",
            "version": get_commit_hash(),
            "uptime": int(get_uptime_seconds()),
        }

    # --- API ---

    # WebGUIはシングルユーザー想定なのでロック1つ
    _webgui_lock = asyncio.Lock()

    @app.post("/api/chat", dependencies=[Depends(_verify)])
    async def chat(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            raise HTTPException(400, "message is required")

        async with _webgui_lock:
            try:
                await bot.database.log_conversation("webgui", "user", message)
                result = await bot.unit_router.route(message, channel="webgui")
                unit_name = result.get("unit", "chat")
                user_message = result.get("message", message)

                unit = bot.unit_manager.get(unit_name)
                if unit is None:
                    unit = bot.unit_manager.get("chat")

                actual_unit = getattr(unit, "unit", unit)
                actual_unit.session_done = False
                response = await unit.execute(None, {"message": user_message, "channel": "webgui"})
                if actual_unit.session_done:
                    bot.unit_router.clear_session("webgui")
                    actual_unit.clear_exchange("webgui")
                elif response:
                    actual_unit.save_exchange("webgui", user_message, response)
                if response:
                    mode = "eco" if not bot.llm_router.ollama_available else "normal"
                    await bot.database.log_conversation("webgui", "assistant", response, mode=mode, unit=unit_name)
                return {"response": response or "", "unit": unit_name}
            except Exception as e:
                log.error("WebGUI chat error: %s", e, exc_info=True)
                return JSONResponse(
                    status_code=200,
                    content={"response": f"Error: {e}", "unit": "system"},
            )

    @app.get("/api/logs", dependencies=[Depends(_verify)])
    async def get_logs(limit: int = 50, offset: int = 0, keyword: str | None = None, channel: str | None = None):
        logs = await bot.database.get_conversation_logs(limit=limit, offset=offset, keyword=keyword, channel=channel)
        return {"logs": logs}

    @app.get("/api/status", dependencies=[Depends(_verify)])
    async def get_status():
        from src.bot import get_commit_hash, get_uptime_seconds
        agents_status = []
        pool = bot.unit_manager.agent_pool
        for agent in pool._agents:
            alive = await pool._is_alive(agent)
            agents_status.append({
                "id": agent["id"],
                "name": agent.get("name", agent["id"]),
                "alive": alive,
                "mode": pool.get_mode(agent["id"]),
            })
        return {
            "version": get_commit_hash(),
            "uptime": int(get_uptime_seconds()),
            "ollama": bot.llm_router.ollama_available,
            "agents": agents_status,
        }

    @app.post("/api/delegation-mode", dependencies=[Depends(_verify)])
    async def set_delegation_mode(request: Request):
        body = await request.json()
        agent_id = body.get("agent_id", "")
        mode = body.get("mode", "auto")
        if mode not in ("allow", "deny", "auto"):
            raise HTTPException(400, "mode must be allow/deny/auto")
        bot.unit_manager.agent_pool.set_mode(agent_id, mode)
        return {"ok": True}

    @app.post("/api/update-code", dependencies=[Depends(_verify)])
    async def update_code():
        try:
            from src.bot import BASE_DIR
            src_dir = os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
            result = subprocess.run(
                ["git", "pull"], cwd=src_dir,
                capture_output=True, text=True, timeout=30,
            )
            output = result.stdout.strip()
            if "Already up to date" in output:
                return {"updated": False, "message": output}

            # Portainer API でスタック再起動
            portainer_url = os.environ.get("PORTAINER_URL", "")
            portainer_token = os.environ.get("PORTAINER_API_TOKEN", "")
            stack_id = os.environ.get("PORTAINER_STACK_ID", "")

            if portainer_url and portainer_token and stack_id:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{portainer_url}/api/stacks/{stack_id}/stop",
                        headers={"X-API-Key": portainer_token},
                    )
                    await client.post(
                        f"{portainer_url}/api/stacks/{stack_id}/start",
                        headers={"X-API-Key": portainer_token},
                    )

            return {"updated": True, "message": output}
        except Exception as e:
            log.error("Code update failed: %s", e)
            raise HTTPException(500, f"Update failed: {e}")

    # --- Units データ閲覧 API ---

    @app.get("/api/units/reminders", dependencies=[Depends(_verify)])
    async def get_reminders(active: int | None = None):
        if active is not None:
            rows = await bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = ? ORDER BY remind_at DESC LIMIT 100",
                (active,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM reminders ORDER BY remind_at DESC LIMIT 100"
            )
        return {"items": rows}

    @app.get("/api/units/todos", dependencies=[Depends(_verify)])
    async def get_todos(done: int | None = None):
        if done is not None:
            rows = await bot.database.fetchall(
                "SELECT * FROM todos WHERE done = ? ORDER BY created_at DESC LIMIT 100",
                (done,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM todos ORDER BY created_at DESC LIMIT 100"
            )
        return {"items": rows}

    @app.get("/api/units/memos", dependencies=[Depends(_verify)])
    async def get_memos(keyword: str | None = None):
        if keyword:
            rows = await bot.database.fetchall(
                "SELECT * FROM memos WHERE content LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT 100",
                (f"%{keyword}%", f"%{keyword}%"),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM memos ORDER BY created_at DESC LIMIT 100"
            )
        return {"items": rows}

    @app.get("/api/gemini-config", dependencies=[Depends(_verify)])
    async def get_gemini_config():
        return bot.config.get("gemini", {})

    @app.post("/api/gemini-config", dependencies=[Depends(_verify)])
    async def set_gemini_config(request: Request):
        body = await request.json()
        gemini_cfg = bot.config.setdefault("gemini", {})
        for key in ("conversation", "memory_extraction", "unit_routing", "monthly_token_limit"):
            if key in body:
                gemini_cfg[key] = body[key]
        bot.llm_router._gemini_config = gemini_cfg
        return {"ok": True}

    # --- 静的ファイル & フロントエンド ---

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(_verify)])
    async def index():
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        try:
            with open(html_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>Secretary Bot WebGUI</h1><p>static/index.html not found</p>"

    return app
