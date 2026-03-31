"""FastAPI WebGUI + /health エンドポイント。"""

import asyncio
import json
import os
import secrets
import subprocess

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger

log = get_logger(__name__)


def create_web_app(bot) -> FastAPI:
    app = FastAPI(title="Secretary Bot WebGUI")
    security = HTTPBasic()

    _username = os.environ.get("WEBGUI_USERNAME", "admin")
    _password = os.environ.get("WEBGUI_PASSWORD", "")
    _webgui_user_id = os.environ.get("WEBGUI_USER_ID", "")

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
            ft = get_flow_tracker()
            flow_id = await ft.start_flow()
            await ft.emit("MSG", "done", {"content": message[:80], "channel": "webgui"}, flow_id)
            await ft.emit("LOCK", "done", {"channel": "webgui"}, flow_id)
            try:
                await bot.database.log_conversation("webgui", "user", message, user_id=_webgui_user_id)
                result = await bot.unit_router.route(message, channel="webgui", user_id=_webgui_user_id, flow_id=flow_id)
                unit_name = result.get("unit", "chat")
                user_message = result.get("message", message)

                unit = bot.unit_manager.get(unit_name)
                if unit is None:
                    unit = bot.unit_manager.get("chat")

                actual_unit = getattr(unit, "unit", unit)
                actual_unit.session_done = False
                response = await unit.execute(None, {"message": user_message, "channel": "webgui", "user_id": _webgui_user_id, "flow_id": flow_id})
                if actual_unit.session_done:
                    bot.unit_router.clear_session("webgui", _webgui_user_id)
                    actual_unit.clear_exchange("webgui")
                    await ft.emit("SESSION_UPDATE", "done", {"action": "cleared"}, flow_id)
                elif response:
                    actual_unit.save_exchange("webgui", user_message, response)
                    await ft.emit("SESSION_UPDATE", "done", {"action": "saved"}, flow_id)
                if response:
                    mode = "eco" if not bot.llm_router.ollama_available else "normal"
                    await bot.database.log_conversation("webgui", "assistant", response, mode=mode, unit=unit_name, user_id=_webgui_user_id)
                    await ft.emit("DB_LOG", "done", {"mode": mode, "unit": unit_name}, flow_id)
                    await ft.emit("REPLY", "done", {"channel": "webgui"}, flow_id)
                await ft.end_flow(flow_id)
                return {"response": response or "", "unit": unit_name}
            except Exception as e:
                log.error("WebGUI chat error: %s", e, exc_info=True)
                await ft.emit("REPLY", "error", {"error": str(e)}, flow_id)
                await ft.end_flow(flow_id)
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

    async def _restart_container() -> dict:
        """Portainer API 経由でコンテナを再起動する。{"restarted": bool, "detail": str} を返す。"""
        portainer_url = os.environ.get("PORTAINER_URL", "")
        portainer_token = os.environ.get("PORTAINER_API_TOKEN", "")
        if not (portainer_url and portainer_token):
            msg = "Portainer設定なし（PORTAINER_URL / PORTAINER_API_TOKEN）"
            log.warning("Portainer env vars not set — skipping restart")
            return {"restarted": False, "detail": msg}
        try:
            env_id = os.environ.get("PORTAINER_ENV_ID", "1")
            container_name = os.environ.get("CONTAINER_NAME", "secretary-bot")
            headers = {"X-API-Key": portainer_token}
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                filters = f'{{"name":["{container_name}"]}}'
                list_resp = await client.get(
                    f"{portainer_url}/api/endpoints/{env_id}/docker/containers/json",
                    headers=headers,
                    params={"filters": filters},
                )
                list_resp.raise_for_status()
                containers = list_resp.json()
                if not containers:
                    msg = f"コンテナ '{container_name}' が見つかりません"
                    log.error("Container not found: %s", container_name)
                    return {"restarted": False, "detail": msg}
                container_id = containers[0]["Id"]
                restart_resp = await client.post(
                    f"{portainer_url}/api/endpoints/{env_id}/docker/containers/{container_id}/restart",
                    headers=headers,
                )
                if restart_resp.status_code < 300:
                    log.info("Container restarted: %s", container_name)
                    return {"restarted": True, "detail": f"コンテナ '{container_name}' を再起動しました"}
                else:
                    msg = f"再起動API エラー (HTTP {restart_resp.status_code}): {restart_resp.text[:200]}"
                    log.error("Container restart failed: %s %s", restart_resp.status_code, restart_resp.text)
                    return {"restarted": False, "detail": msg}
        except Exception as e:
            log.error("Portainer API error: %s", e)
            return {"restarted": False, "detail": f"Portainer API 接続失敗: {e}"}

    @app.post("/api/update-code", dependencies=[Depends(_verify)])
    async def update_code():
        try:
            from src.bot import BASE_DIR
            git_dir = os.environ.get("GIT_REPO_DIR") or (
                os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
            )
            result = subprocess.run(
                ["git", "pull"], cwd=git_dir,
                capture_output=True, text=True, timeout=30,
            )
            output = result.stdout.strip()
            if "Already up to date" in output:
                return {"updated": False, "message": output, "restarted": False, "restart_detail": "変更なしのためスキップ"}

            r = await _restart_container()
            return {"updated": True, "message": output, "restarted": r["restarted"], "restart_detail": r["detail"]}
        except Exception as e:
            log.error("Code update failed: %s", e)
            raise HTTPException(500, f"Update failed: {e}")

    @app.post("/api/restart", dependencies=[Depends(_verify)])
    async def restart():
        r = await _restart_container()
        return {"restarted": r["restarted"], "detail": r["detail"]}

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

    @app.get("/api/units/timers", dependencies=[Depends(_verify)])
    async def get_timers():
        import time as _time
        timer_unit = bot.unit_manager.get("timer")
        if timer_unit is None:
            return {"items": []}
        actual = getattr(timer_unit, "unit", timer_unit)
        items = []
        now = _time.time()
        for tid, info in actual._timer_info.items():
            elapsed = now - info["created_at"]
            remaining = max(0, info["minutes"] * 60 - elapsed)
            items.append({
                "id": tid,
                "message": info["message"],
                "minutes": info["minutes"],
                "remaining_sec": int(remaining),
            })
        return {"items": items}

    @app.get("/api/units/loaded", dependencies=[Depends(_verify)])
    async def get_loaded_units():
        """現在ロードされているユニット一覧を返す。"""
        units = []
        for name, unit in bot.unit_manager.units.items():
            actual = getattr(unit, "unit", unit)
            units.append({
                "name": actual.UNIT_NAME,
                "description": actual.UNIT_DESCRIPTION,
                "delegate_to": actual.DELEGATE_TO,
                "breaker_state": actual.breaker.state,
            })
        return {"units": units}

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
                await bot.database.set_setting(f"gemini.{key}", json.dumps(body[key]))
        bot.llm_router._gemini_config = gemini_cfg
        return {"ok": True}

    # --- ユニット別Gemini許可 ---

    @app.get("/api/unit-gemini", dependencies=[Depends(_verify)])
    async def get_unit_gemini():
        units_cfg = bot.config.get("units", {})
        result = {}
        for name in units_cfg:
            result[name] = units_cfg[name].get("llm", {}).get("gemini_allowed", True)
        return result

    @app.post("/api/unit-gemini", dependencies=[Depends(_verify)])
    async def set_unit_gemini(request: Request):
        body = await request.json()
        unit_name = body.get("unit", "")
        allowed = bool(body.get("allowed", True))
        ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
        ucfg.setdefault("llm", {})["gemini_allowed"] = allowed
        # 実行中ユニットにも反映
        cog = bot.cogs.get(unit_name)
        if cog and hasattr(cog, "llm"):
            cog.llm._gemini_allowed = allowed
        await bot.database.set_setting(f"unit_gemini.{unit_name}", "true" if allowed else "false")
        return {"ok": True}

    # --- デバッグ: LLM状態確認 ---

    @app.get("/api/debug/llm-state", dependencies=[Depends(_verify)])
    async def debug_llm_state():
        units_info = {}
        for name, cog in bot.cogs.items():
            if hasattr(cog, "llm"):
                llm = cog.llm
                units_info[name] = {
                    "purpose": llm._purpose,
                    "ollama_only": llm._ollama_only,
                    "gemini_allowed": llm._gemini_allowed,
                    "ollama_model": llm._ollama_model,
                    "gemini_model": llm._gemini_model,
                }
        return {
            "ollama_available": bot.llm_router.ollama_available,
            "gemini_config": bot.llm_router._gemini_config,
            "units": units_info,
        }

    # --- LLM設定 ---

    @app.get("/api/llm-config", dependencies=[Depends(_verify)])
    async def get_llm_config():
        llm_cfg = bot.config.get("llm", {})
        units_cfg = bot.config.get("units", {})
        # ユニットごとのモデル上書き情報を収集
        unit_models = {}
        for name, ucfg in units_cfg.items():
            unit_llm = ucfg.get("llm", {})
            if unit_llm.get("ollama_model"):
                unit_models[name] = unit_llm["ollama_model"]
        return {
            "ollama_model": llm_cfg.get("ollama_model", "qwen3"),
            "unit_models": unit_models,
        }

    @app.post("/api/llm-config", dependencies=[Depends(_verify)])
    async def set_llm_config(request: Request):
        body = await request.json()

        # グローバルモデル変更
        if "ollama_model" in body:
            model = body["ollama_model"].strip()
            bot.config.setdefault("llm", {})["ollama_model"] = model
            bot.llm_router.ollama.model = model
            # ユニット別上書きが無いユニットにも反映
            for cog in bot.cogs.values():
                if hasattr(cog, "llm") and cog.llm._ollama_model is None:
                    pass  # model=None → OllamaClient.model を参照するので自動反映

        # ユニット別モデル変更
        if "unit_models" in body:
            for unit_name, model in body["unit_models"].items():
                model = model.strip() if model else ""
                ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
                if model:
                    ucfg.setdefault("llm", {})["ollama_model"] = model
                else:
                    ucfg.get("llm", {}).pop("ollama_model", None)
                # 実行中のユニットのUnitLLMにも反映
                cog = bot.cogs.get(unit_name)
                if cog and hasattr(cog, "llm"):
                    cog.llm._ollama_model = model or None

        return {"ok": True}

    # --- ペルソナ設定 ---

    @app.get("/api/persona", dependencies=[Depends(_verify)])
    async def get_persona():
        return {"persona": bot.config.get("character", {}).get("persona", "")}

    @app.post("/api/persona", dependencies=[Depends(_verify)])
    async def set_persona(request: Request):
        body = await request.json()
        persona = body.get("persona", "")
        bot.config.setdefault("character", {})["persona"] = persona
        return {"ok": True}

    # --- フロー追跡 ---

    @app.get("/api/flow/state", dependencies=[Depends(_verify)])
    async def get_flow_state():
        tracker = get_flow_tracker()
        return tracker.get_state()

    @app.get("/api/flow/stream", dependencies=[Depends(_verify)])
    async def flow_stream():
        tracker = get_flow_tracker()
        queue = tracker.subscribe()

        async def event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                tracker.unsubscribe(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
