"""FastAPI WebGUI + /health エンドポイント。"""

import asyncio
import json
import os
import subprocess
from datetime import datetime

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger

log = get_logger(__name__)


def create_web_app(bot) -> FastAPI:
    app = FastAPI(title="Secretary Bot WebGUI")

    async def _verify():
        pass

    _webgui_user_id = os.environ.get("WEBGUI_USER_ID", "")

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

    @app.post("/api/chat", )
    async def chat(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            raise HTTPException(400, "message is required")

        ft = get_flow_tracker()
        flow_id = await ft.start_flow()
        await ft.emit("MSG", "done", {"content": message[:80], "channel": "webgui"}, flow_id)

        async def _process_chat():
            async with _webgui_lock:
                await ft.emit("LOCK", "done", {"channel": "webgui"}, flow_id)
                try:
                    await bot.database.log_conversation("webgui", "user", message, user_id=_webgui_user_id)

                    recent_rows = await bot.database.get_recent_channel_messages(
                        "webgui", limit=6, user_id=_webgui_user_id,
                    )
                    conversation_context = [
                        r for r in recent_rows if r["content"] != message
                    ][-4:]

                    result = await bot.unit_router.route(message, channel="webgui", user_id=_webgui_user_id, flow_id=flow_id, conversation_context=conversation_context)
                    unit_name = result.get("unit", "chat")
                    user_message = result.get("message", message)

                    unit = bot.unit_manager.get(unit_name)
                    if unit is None:
                        unit = bot.unit_manager.get("chat")

                    actual_unit = getattr(unit, "unit", unit)
                    actual_unit.session_done = False
                    response = await unit.execute(None, {"message": user_message, "channel": "webgui", "user_id": _webgui_user_id, "flow_id": flow_id, "conversation_context": conversation_context})
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
                    await ft.emit("REPLY", "done", {"channel": "webgui", "response": response or "", "unit": unit_name}, flow_id)
                    await ft.end_flow(flow_id)
                except Exception as e:
                    log.error("WebGUI chat error: %s", e, exc_info=True)
                    try:
                        await ft.emit("REPLY", "error", {"channel": "webgui", "response": f"エラーが発生しました: {e}", "unit": "system"}, flow_id)
                        await ft.end_flow(flow_id)
                    except Exception:
                        pass

        asyncio.create_task(_process_chat())
        return {"flow_id": flow_id}

    @app.get("/api/logs", )
    async def get_logs(limit: int = 50, offset: int = 0, keyword: str | None = None, channel: str | None = None):
        logs = await bot.database.get_conversation_logs(limit=limit, offset=offset, keyword=keyword, channel=channel)
        return {"logs": logs}

    @app.get("/api/status", )
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

    @app.post("/api/ollama-recheck", )
    async def ollama_recheck():
        """Ollamaの接続状態を手動で再チェックする。"""
        available = await bot.llm_router.check_ollama()
        # ハートビート間隔も再調整
        bot.heartbeat._reschedule()
        return {"ollama_available": available}

    @app.post("/api/delegation-mode", )
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

    async def _delayed_restart(delay_seconds: float = 2):
        """レスポンス送信後に遅延してコンテナを再起動する。"""
        await asyncio.sleep(delay_seconds)
        await _restart_container()

    @app.post("/api/update-code", )
    async def update_code(background_tasks: BackgroundTasks):
        try:
            from src.bot import BASE_DIR
            git_dir = os.environ.get("GIT_REPO_DIR") or (
                os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
            )
            # ローカルHEADハッシュ
            hash_before = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=git_dir,
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()

            # リモート名を動的取得
            remote_name_result = subprocess.run(
                ["git", "remote"], cwd=git_dir,
                capture_output=True, text=True, timeout=10,
            )
            remote_name = remote_name_result.stdout.strip().splitlines()[0] if remote_name_result.stdout.strip() else "origin"

            # 現在のリモートURL取得
            remote_url_result = subprocess.run(
                ["git", "remote", "get-url", remote_name], cwd=git_dir,
                capture_output=True, text=True, timeout=10,
            )
            remote_url = remote_url_result.stdout.strip()

            log.info("git fetch: remote=%s", remote_name)

            # fetch
            fetch_result = subprocess.run(
                ["git", "fetch", remote_name], cwd=git_dir,
                capture_output=True, text=True, timeout=30,
            )
            if fetch_result.returncode != 0:
                err = fetch_result.stderr.strip()
                log.error("git fetch failed: %s", err)
                return {"updated": False, "message": f"git fetch 失敗\n{err}", "restarted": False, "restart_detail": "fetchエラー"}

            # ブランチ名を取得
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=git_dir,
                capture_output=True, text=True, timeout=10,
            )
            branch = branch_result.stdout.strip() or "main"

            # fetch後のリモートHEADと比較
            remote_hash = subprocess.run(
                ["git", "rev-parse", "FETCH_HEAD"], cwd=git_dir,
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()

            if hash_before == remote_hash:
                return {"updated": False, "message": f"Already up to date. ({hash_before[:7]})", "restarted": False, "restart_detail": "変更なしのためスキップ"}

            # pull
            pull_result = subprocess.run(
                ["git", "pull", remote_name, branch], cwd=git_dir,
                capture_output=True, text=True, timeout=30,
            )
            if pull_result.returncode != 0:
                err = pull_result.stderr.strip()
                log.error("git pull failed: %s", err)
                return {"updated": False, "message": f"git pull 失敗: {err}", "restarted": False, "restart_detail": "pullエラー"}

            output = pull_result.stdout.strip()
            log.info("Code updated: %s -> %s", hash_before[:7], remote_hash[:7])

            # レスポンス送信後に再起動（遅延付き）
            background_tasks.add_task(_delayed_restart, 2)
            return {"updated": True, "message": f"{hash_before[:7]} → {remote_hash[:7]}\n{output}", "restarted": True, "restart_detail": "まもなく再起動します…"}
        except Exception as e:
            log.error("Code update failed: %s", e)
            raise HTTPException(500, f"Update failed: {e}")

    @app.post("/api/restart", )
    async def restart(background_tasks: BackgroundTasks):
        background_tasks.add_task(_delayed_restart, 2)
        return {"restarted": True, "detail": "まもなく再起動します…"}

    # --- Units データ閲覧 API ---

    @app.get("/api/units/reminders", )
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

    @app.get("/api/units/todos", )
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

    @app.get("/api/units/memos", )
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

    # --- リマインダー CRUD ---

    @app.put("/api/units/reminders/{rid}", )
    async def update_reminder(rid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM reminders WHERE id = ?", (rid,))
        if not row:
            raise HTTPException(404, "not found")
        message = body.get("message", row["message"])
        remind_at = body.get("remind_at", row["remind_at"])
        await bot.database.execute(
            "UPDATE reminders SET message = ?, remind_at = ?, notified = 0 WHERE id = ?",
            (message, remind_at, rid),
        )
        try:
            dt = datetime.fromisoformat(remind_at)
            bot.heartbeat.schedule_reminder(rid, dt, message, row.get("user_id", ""))
        except Exception:
            pass
        return {"ok": True}

    @app.post("/api/units/reminders/{rid}/done", )
    async def done_reminder(rid: int):
        from src.database import jst_now
        row = await bot.database.fetchone("SELECT * FROM reminders WHERE id = ? AND active = 1", (rid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute(
            "UPDATE reminders SET active = 0, done_at = ? WHERE id = ?", (jst_now(), rid)
        )
        bot.heartbeat.cancel_reminder(rid)
        return {"ok": True}

    @app.delete("/api/units/reminders/{rid}", )
    async def delete_reminder(rid: int):
        row = await bot.database.fetchone("SELECT * FROM reminders WHERE id = ?", (rid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM reminders WHERE id = ?", (rid,))
        bot.heartbeat.cancel_reminder(rid)
        return {"ok": True}

    # --- ToDo CRUD ---

    @app.put("/api/units/todos/{tid}", )
    async def update_todo(tid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM todos WHERE id = ?", (tid,))
        if not row:
            raise HTTPException(404, "not found")
        title = body.get("title", row["title"])
        due_date = body.get("due_date") if "due_date" in body else row.get("due_date")
        await bot.database.execute(
            "UPDATE todos SET title = ?, due_date = ? WHERE id = ?", (title, due_date, tid)
        )
        return {"ok": True}

    @app.post("/api/units/todos/{tid}/done", )
    async def done_todo(tid: int):
        from src.database import jst_now
        row = await bot.database.fetchone("SELECT * FROM todos WHERE id = ? AND done = 0", (tid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ?", (jst_now(), tid)
        )
        return {"ok": True}

    @app.delete("/api/units/todos/{tid}", )
    async def delete_todo(tid: int):
        row = await bot.database.fetchone("SELECT * FROM todos WHERE id = ?", (tid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM todos WHERE id = ?", (tid,))
        return {"ok": True}

    # --- メモ CRUD ---

    @app.put("/api/units/memos/{mid}", )
    async def update_memo(mid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM memos WHERE id = ?", (mid,))
        if not row:
            raise HTTPException(404, "not found")
        content = body.get("content", row["content"])
        tags = body.get("tags") if "tags" in body else row.get("tags", "")
        await bot.database.execute(
            "UPDATE memos SET content = ?, tags = ? WHERE id = ?", (content, tags, mid)
        )
        return {"ok": True}

    @app.post("/api/units/memos/{mid}/append", )
    async def append_memo(mid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM memos WHERE id = ?", (mid,))
        if not row:
            raise HTTPException(404, "not found")
        append_text = body.get("content", "")
        if not append_text:
            raise HTTPException(400, "content is required")
        updated = row["content"] + "\n" + append_text
        await bot.database.execute(
            "UPDATE memos SET content = ? WHERE id = ?", (updated, mid)
        )
        return {"ok": True}

    @app.delete("/api/units/memos/{mid}", )
    async def delete_memo(mid: int):
        row = await bot.database.fetchone("SELECT * FROM memos WHERE id = ?", (mid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM memos WHERE id = ?", (mid,))
        return {"ok": True}

    @app.get("/api/units/timers", )
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

    @app.get("/api/units/loaded", )
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

    # --- ChromaDB 記憶閲覧 API ---

    _MEMORY_COLLECTIONS = ("ai_memory", "people_memory", "conversation_log")

    @app.get("/api/memory/{collection}", dependencies=[Depends(_verify)])
    async def get_memory(collection: str, limit: int = 200, offset: int = 0):
        if collection not in _MEMORY_COLLECTIONS:
            raise HTTPException(400, f"unknown collection: {collection}")
        items = bot.chroma.get_all(collection, limit=limit, offset=offset)
        total = bot.chroma.count(collection)
        return {"items": items, "total": total}

    @app.delete("/api/memory/{collection}/{doc_id}", dependencies=[Depends(_verify)])
    async def delete_memory(collection: str, doc_id: str):
        if collection not in _MEMORY_COLLECTIONS:
            raise HTTPException(400, f"unknown collection: {collection}")
        bot.chroma.delete(collection, doc_id)
        return {"ok": True}

    @app.get("/api/gemini-config", dependencies=[Depends(_verify)])
    async def get_gemini_config():
        return bot.config.get("gemini", {})

    @app.post("/api/gemini-config", )
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

    @app.get("/api/unit-gemini", )
    async def get_unit_gemini():
        units_cfg = bot.config.get("units", {})
        result = {}
        for name in units_cfg:
            result[name] = units_cfg[name].get("llm", {}).get("gemini_allowed", True)
        return result

    @app.post("/api/unit-gemini", )
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

    # --- デバッグ: ハートビートログ ---

    @app.get("/api/debug/heartbeat-logs", dependencies=[Depends(_verify)])
    async def debug_heartbeat_logs():
        return {"logs": list(bot.heartbeat.debug_logs)}

    # --- デバッグ: 楽天検索 ---

    @app.get("/api/debug/rakuten-search", dependencies=[Depends(_verify)])
    async def debug_rakuten_search():
        """最後のrakuten_search実行データを返す（検索結果・LLMプロンプト・出力）。"""
        unit = bot.cogs.get("RakutenSearchUnit")
        if unit is None:
            return {"available": False, "data": {}}
        return {"available": True, "data": getattr(unit, "last_debug", {})}

    # --- 楽天検索設定 ---

    @app.get("/api/rakuten-config", dependencies=[Depends(_verify)])
    async def get_rakuten_config():
        cfg = bot.config.get("rakuten_search", {})
        return {
            "max_results": cfg.get("max_results", 5),
            "fetch_details": cfg.get("fetch_details", True),
        }

    @app.post("/api/rakuten-config", dependencies=[Depends(_verify)])
    async def set_rakuten_config(request: Request):
        body = await request.json()
        cfg = bot.config.setdefault("rakuten_search", {})
        for key in ("max_results", "fetch_details"):
            if key in body:
                cfg[key] = body[key]
                await bot.database.set_setting(f"rakuten_search.{key}", json.dumps(body[key]))
        # ユニットの設定をホットリロード
        unit = bot.cogs.get("RakutenSearchUnit")
        if unit:
            unit._max_results = cfg.get("max_results", 5)
            unit._fetch_details_enabled = cfg.get("fetch_details", True)
        return {"ok": True}

    # --- デバッグ: LLM状態確認 ---

    @app.get("/api/debug/llm-state", )
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

    @app.get("/api/llm-config", )
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
            "ollama_timeout": int(llm_cfg.get("ollama_timeout", 300)),
            "gemini_model": bot.llm_router.gemini.model,
            "unit_models": unit_models,
        }

    @app.post("/api/llm-config", )
    async def set_llm_config(request: Request):
        body = await request.json()

        # グローバルモデル変更
        if "ollama_model" in body:
            model = body["ollama_model"].strip()
            bot.config.setdefault("llm", {})["ollama_model"] = model
            bot.llm_router.ollama.model = model
            await bot.database.set_setting("llm.ollama_model", model)
            # ユニット別上書きが無いユニットにも反映
            for cog in bot.cogs.values():
                if hasattr(cog, "llm") and cog.llm._ollama_model is None:
                    pass  # model=None → OllamaClient.model を参照するので自動反映

        # Geminiモデル変更
        if "gemini_model" in body:
            gmodel = body["gemini_model"].strip()
            if gmodel:
                bot.config.setdefault("llm", {})["gemini_model"] = gmodel
                bot.llm_router.gemini.model = gmodel
                await bot.database.set_setting("llm.gemini_model", gmodel)

        # タイムアウト変更
        if "ollama_timeout" in body:
            t = int(body["ollama_timeout"])
            if t < 10:
                raise HTTPException(400, "ollama_timeout must be >= 10")
            bot.config.setdefault("llm", {})["ollama_timeout"] = t
            bot.llm_router.ollama.timeout = t
            await bot.database.set_setting("llm.ollama_timeout", str(t))

        # ユニット別モデル変更
        if "unit_models" in body:
            for unit_name, model in body["unit_models"].items():
                model = model.strip() if model else ""
                ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
                if model:
                    ucfg.setdefault("llm", {})["ollama_model"] = model
                    await bot.database.set_setting(f"unit_llm.{unit_name}", model)
                else:
                    ucfg.get("llm", {}).pop("ollama_model", None)
                    await bot.database.delete_setting(f"unit_llm.{unit_name}")
                # 実行中のユニットのUnitLLMにも反映
                cog = bot.cogs.get(unit_name)
                if cog and hasattr(cog, "llm"):
                    cog.llm._ollama_model = model or None

        return {"ok": True}

    # --- ハートビート設定 ---

    @app.get("/api/heartbeat-config", dependencies=[Depends(_verify)])
    async def get_heartbeat_config():
        hb_cfg = bot.config.get("heartbeat", {})
        return {
            "interval_with_ollama_minutes": hb_cfg.get("interval_with_ollama_minutes", 15),
            "interval_without_ollama_minutes": hb_cfg.get("interval_without_ollama_minutes", 180),
            "compact_threshold_messages": hb_cfg.get("compact_threshold_messages", 20),
        }

    @app.post("/api/heartbeat-config", dependencies=[Depends(_verify)])
    async def set_heartbeat_config(request: Request):
        body = await request.json()
        hb_cfg = bot.config.setdefault("heartbeat", {})
        for key in ("interval_with_ollama_minutes", "interval_without_ollama_minutes", "compact_threshold_messages"):
            if key in body:
                val = int(body[key])
                if val < 1:
                    raise HTTPException(400, f"{key} must be >= 1")
                hb_cfg[key] = val
                await bot.database.set_setting(f"heartbeat.{key}", str(val))
        # 次回スケジュールに反映
        bot.heartbeat._reschedule()
        return {"ok": True}

    # --- ペルソナ設定 ---

    @app.get("/api/persona", )
    async def get_persona():
        return {"persona": bot.config.get("character", {}).get("persona", "")}

    @app.post("/api/persona", )
    async def set_persona(request: Request):
        body = await request.json()
        persona = body.get("persona", "")
        bot.config.setdefault("character", {})["persona"] = persona
        return {"ok": True}

    # --- フロー追跡 ---

    @app.get("/api/flow/state", )
    async def get_flow_state():
        tracker = get_flow_tracker()
        return tracker.get_state()

    @app.get("/api/flow/stream", )
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

    @app.get("/", response_class=HTMLResponse, )
    async def index():
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        try:
            with open(html_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>Secretary Bot WebGUI</h1><p>static/index.html not found</p>"

    return app
