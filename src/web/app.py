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

    @app.post("/api/chat", )
    async def chat(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        reply_unit = body.get("reply_unit")  # 返信ルーティング用
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

                    history_minutes = bot.config.get("units", {}).get("chat", {}).get("history_minutes", 60)
                    recent_rows = await bot.database.get_recent_channel_messages(
                        "webgui", limit=6, user_id=_webgui_user_id,
                        minutes=history_minutes,
                    )
                    conversation_context = [
                        r for r in recent_rows if r["content"] != message
                    ][-4:]

                    # 返信ルーティング: reply_unit指定時はLLMルーティングをバイパス
                    if reply_unit and reply_unit != "chat" and bot.unit_manager.get(reply_unit):
                        unit_name = reply_unit
                        user_message = message
                        log.info("WebGUI reply-based routing to: %s", unit_name)
                        await ft.emit("UNIT_DECIDE", "done", {"unit": unit_name, "reply": True}, flow_id)
                    else:
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
                        bot.unit_router.refresh_session("webgui", _webgui_user_id)
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
    async def get_logs(limit: int = 50, offset: int = 0, keyword: str | None = None, channel: str | None = None, bot_only: bool = False):
        logs = await bot.database.get_conversation_logs(limit=limit, offset=offset, keyword=keyword, channel=channel, bot_only=bot_only)
        return {"logs": logs}

    @app.get("/api/status", )
    async def get_status():
        import time
        status = await bot.status_collector.collect()
        # Agent 再起動直後の一時 down を "restarting" 状態として扱う
        now = time.time()
        stale_ids = []
        for agent in status.get("agents", []) or []:
            aid = str(agent.get("id") or agent.get("host") or "")
            ts = _agent_restart_ts.get(aid)
            if ts is None:
                continue
            elapsed = now - ts
            if agent.get("alive") or elapsed >= _RESTART_WINDOW_SEC:
                stale_ids.append(aid)
            else:
                agent["status"] = "restarting"
                agent["restart_elapsed"] = int(elapsed)
        for aid in stale_ids:
            _agent_restart_ts.pop(aid, None)
        return status

    @app.get("/api/version", )
    async def get_version():
        """現在のメインリポ/サブモジュールの short hash を返す。"""
        from src.bot import BASE_DIR
        git_dir = os.environ.get("GIT_REPO_DIR") or (
            os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
        )
        try:
            main_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=git_dir,
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            main_hash = "unknown"

        sub_abs = os.path.join(git_dir, "windows-agent", "tools", "input-relay")
        try:
            input_relay_hash = subprocess.run(
                ["git", "-C", sub_abs, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or "unknown"
        except Exception:
            input_relay_hash = "unknown"

        return {
            "main": main_hash or "unknown",
            "input_relay": input_relay_hash,
        }

    @app.post("/api/ollama-recheck", )
    async def ollama_recheck():
        """Ollamaの接続状態を手動で再チェックする。"""
        available = await bot.llm_router.check_ollama(force=True)
        # ハートビート間隔も再調整
        bot.heartbeat._reschedule()
        return {"ollama_available": available}

    @app.get("/api/ollama-models")
    async def ollama_models():
        """Ollamaで利用可能なモデル一覧を返す。"""
        models = await bot.llm_router.ollama.list_models()
        return {"models": models}

    @app.get("/api/ollama-status")
    async def ollama_status():
        """Ollamaインスタンスの詳細ステータスを返す。"""
        status = bot.llm_router.ollama.get_status()
        # URL → エージェント名をマッピング
        for inst in status["instances"]:
            inst["name"] = bot.llm_router.get_url_name(inst["url"])
        return status

    @app.post("/api/delegation-mode", )
    async def set_delegation_mode(request: Request):
        body = await request.json()
        agent_id = body.get("agent_id", "")
        mode = body.get("mode", "auto")
        if mode not in ("allow", "deny", "auto"):
            raise HTTPException(400, "mode must be allow/deny/auto")
        bot.unit_manager.agent_pool.set_mode(agent_id, mode)
        await bot.database.set_setting(f"delegation_mode.{agent_id}", mode)
        return {"ok": True}

    @app.post("/api/agents/{agent_id}/pause", dependencies=[Depends(_verify)])
    async def pause_agent(agent_id: str, request: Request):
        """エージェントを一時停止する。"""
        body = await request.json()
        duration_minutes = body.get("duration_minutes", 30)
        if duration_minutes <= 0 or duration_minutes > 720:  # 最大12時間
            raise HTTPException(400, "duration_minutes must be 1-720")
        pool = bot.unit_manager.agent_pool
        pool.pause_agent(agent_id, duration_minutes * 60)
        log.info("Agent %s paused for %d minutes via WebGUI", agent_id, duration_minutes)
        return {"ok": True, "paused_minutes": duration_minutes}

    @app.delete("/api/agents/{agent_id}/pause", dependencies=[Depends(_verify)])
    async def unpause_agent(agent_id: str):
        """エージェントの一時停止を解除する。"""
        pool = bot.unit_manager.agent_pool
        pool.unpause_agent(agent_id)
        log.info("Agent %s unpaused via WebGUI", agent_id)
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

    async def _post_all_agents(bot, path: str, timeout: float = 8) -> list[dict]:
        """全Windows Agent の指定 path に POST を並列で発行する汎用ヘルパー。
        Agent が down していても timeout 内に諦め、全体の完了を妨げない。"""
        agents = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
        if not agents:
            return []
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}

        async def _post_one(agent: dict) -> dict:
            agent_id = agent.get("id", agent["host"])
            url = f"http://{agent['host']}:{agent['port']}{path}"
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers)
                    data = resp.json() if resp.content else {}
                log.info("Agent %s POST %s OK", agent_id, path)
                return {"id": agent_id, "name": agent.get("name"), "success": True, **data}
            except Exception as e:
                log.warning("Agent %s POST %s failed: %s", agent_id, path, e)
                return {"id": agent_id, "name": agent.get("name"), "success": False, "error": str(e)}

        return await asyncio.gather(*[_post_one(a) for a in agents._agents])

    async def _update_all_agents(bot) -> list[dict]:
        """全Windows Agentに /update を並列で呼んでコード更新させる。"""
        return await _post_all_agents(bot, "/update", timeout=8)

    async def _get_all_agent_versions(bot) -> list[dict]:
        """全 Windows Agent の /version を並列取得してハッシュを返す。"""
        agents = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
        if not agents:
            return []
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}

        async def _get_one(agent: dict) -> dict:
            agent_id = agent.get("id", agent["host"])
            url = f"http://{agent['host']}:{agent['port']}/version"
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(url, headers=headers)
                    data = resp.json() if resp.content else {}
                full = (data.get("version") or "").strip()
                return {
                    "id": agent_id,
                    "name": agent.get("name"),
                    "alive": True,
                    "version": full[:7] if full else "",
                    "version_full": full,
                }
            except Exception as e:
                return {
                    "id": agent_id,
                    "name": agent.get("name"),
                    "alive": False,
                    "version": "",
                    "error": str(e),
                }

        return await asyncio.gather(*[_get_one(a) for a in agents._agents])

    def _find_agent_by_id(agent_id: str) -> dict | None:
        pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
        if not pool:
            return None
        for a in pool._agents:
            if str(a.get("id", a.get("host"))) == str(agent_id):
                return a
        return None

    @app.post("/api/update-code", )
    async def update_code(background_tasks: BackgroundTasks):
        # 2重実行防止（ダブルクリック・中継再送対策）
        if _update_lock.locked():
            return {
                "updated": False,
                "message": "別の更新処理が実行中です",
                "restarted": False,
                "restart_detail": "重複実行を防止しました",
            }
        async with _update_lock:
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
                    # Pi は最新だが、エージェント側が古い可能性があるのでチェック
                    agent_versions = await _get_all_agent_versions(bot)
                    stale_agents = [
                        a for a in agent_versions
                        if a.get("alive") and a.get("version_full") and a["version_full"] != remote_hash
                    ]
                    if stale_agents:
                        log.info("Pi up-to-date but %d agent(s) stale, triggering update", len(stale_agents))
                        agent_update_results = await _update_all_agents(bot)
                        agent_restart_results = await _post_all_agents(bot, "/restart-self", timeout=5)
                        _mark_agents_restarting_bulk(agent_restart_results)
                        stale_names = ", ".join(a.get("name", a["id"]) for a in stale_agents)
                        return {
                            "updated": False,
                            "message": f"Pi は最新 ({hash_before[:7]})。エージェント更新: {stale_names}",
                            "restarted": False,
                            "restart_detail": "エージェントのみ更新",
                            "agents": agent_update_results,
                            "agents_restart": agent_restart_results,
                        }
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

                # サブモジュール更新
                sub_result = subprocess.run(
                    ["git", "submodule", "update", "--init", "--recursive"], cwd=git_dir,
                    capture_output=True, text=True, timeout=60,
                )
                if sub_result.returncode != 0:
                    log.warning("submodule update failed: %s", sub_result.stderr.strip())

                # 全Windows Agentに更新を通知（並列・8秒timeout）
                agent_update_results = await _update_all_agents(bot)
                # Agent プロセスを自己再起動させてコードを反映（start_agent.bat のループが再起動担当）
                agent_restart_results = await _post_all_agents(bot, "/restart-self", timeout=5)
                _mark_agents_restarting_bulk(agent_restart_results)

                # レスポンス送信後に再起動（遅延付き）
                background_tasks.add_task(_delayed_restart, 2)
                return {
                    "updated": True,
                    "message": f"{hash_before[:7]} → {remote_hash[:7]}\n{output}",
                    "restarted": True,
                    "restart_detail": "まもなく再起動します…",
                    "agents": agent_update_results,
                    "agents_restart": agent_restart_results,
                }
            except Exception as e:
                log.error("Code update failed: %s", e)
                raise HTTPException(500, f"Update failed: {e}")

    @app.post("/api/restart", )
    async def restart(background_tasks: BackgroundTasks):
        background_tasks.add_task(_delayed_restart, 2)
        return {"restarted": True, "detail": "まもなく再起動します…"}

    @app.post("/api/agents/restart-all", )
    async def restart_all_agents_endpoint():
        """全 Windows Agent プロセスを手動で再起動する（コード更新なし）。
        start_agent.bat のループが自動再起動を担当する前提。"""
        if _update_lock.locked():
            return {
                "success": False,
                "message": "別の更新処理が実行中です",
                "agents": [],
            }
        async with _update_lock:
            results = await _post_all_agents(bot, "/restart-self", timeout=5)
            _mark_agents_restarting_bulk(results)
            ok_count = sum(1 for r in results if r.get("success"))
            return {
                "success": True,
                "message": f"{ok_count} / {len(results)} 件の Agent に再起動を要求しました",
                "agents": results,
            }

    @app.post("/api/agents/{agent_id}/restart", )
    async def restart_agent_one(agent_id: str):
        """個別 Agent を再起動する。"""
        if _update_lock.locked():
            return {"success": False, "message": "別の更新処理が実行中です"}
        async with _update_lock:
            agent = _find_agent_by_id(agent_id)
            if not agent:
                raise HTTPException(404, f"Agent '{agent_id}' not found")
            token = os.environ.get("AGENT_SECRET_TOKEN", "")
            headers = {"X-Agent-Token": token} if token else {}
            url = f"http://{agent['host']}:{agent['port']}/restart-self"
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(url, headers=headers)
                    data = resp.json() if resp.content else {}
                _mark_agent_restarting(agent_id)
                log.info("Agent %s restart triggered (single)", agent_id)
                return {"success": True, "agent_id": agent_id, **data}
            except Exception as e:
                log.warning("Agent %s restart failed: %s", agent_id, e)
                return {"success": False, "agent_id": agent_id, "error": str(e)}

    @app.get("/api/agents/versions", )
    async def get_agents_versions():
        """Pi と全 Windows Agent のハッシュを取得し、mismatch を検出する。"""
        from src.bot import BASE_DIR
        git_dir = os.environ.get("GIT_REPO_DIR") or (
            os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
        )
        try:
            pi_full = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=git_dir,
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            pi_full = ""
        pi_short = pi_full[:7] if pi_full else "unknown"

        agents = await _get_all_agent_versions(bot)

        # 生きている agent が全員 Pi と同じ hash なら OK
        live_agents = [a for a in agents if a.get("alive")]
        all_match = bool(live_agents) and all(a.get("version") == pi_short for a in live_agents)
        any_dead = any(not a.get("alive") for a in agents)

        return {
            "pi": pi_short,
            "pi_full": pi_full,
            "agents": agents,
            "all_match": all_match,
            "any_dead": any_dead,
        }

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

    # --- 天気通知 CRUD ---

    @app.get("/api/units/weather", )
    async def get_weather_subscriptions(active: int | None = None):
        if active is not None:
            rows = await bot.database.fetchall(
                "SELECT * FROM weather_subscriptions WHERE active = ? ORDER BY id DESC LIMIT 100",
                (active,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM weather_subscriptions ORDER BY id DESC LIMIT 100"
            )
        return {"items": rows}

    @app.put("/api/units/weather/{wid}", )
    async def update_weather_sub(wid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM weather_subscriptions WHERE id = ?", (wid,))
        if not row:
            raise HTTPException(404, "not found")
        notify_hour = body.get("notify_hour", row["notify_hour"])
        notify_minute = body.get("notify_minute", row["notify_minute"])
        location = row["location"]
        latitude = row["latitude"]
        longitude = row["longitude"]

        # 地域変更がリクエストされた場合、ジオコーディングして更新
        new_location = body.get("location")
        if new_location and new_location != location:
            weather_unit = bot.unit_manager.get("weather")
            if weather_unit:
                actual = getattr(weather_unit, "unit", weather_unit)
                geo = await actual._geocode(new_location)
                if not geo:
                    raise HTTPException(400, f"「{new_location}」の位置情報が見つかりません")
                location = geo["name"]
                latitude = geo["latitude"]
                longitude = geo["longitude"]

        await bot.database.execute(
            "UPDATE weather_subscriptions SET notify_hour = ?, notify_minute = ?, location = ?, latitude = ?, longitude = ? WHERE id = ?",
            (notify_hour, notify_minute, location, latitude, longitude, wid),
        )
        # スケジューラ更新
        if row["active"]:
            bot.heartbeat.schedule_weather_daily(
                wid, notify_hour, notify_minute,
                row["user_id"], latitude, longitude, location,
            )
        return {"ok": True}

    @app.delete("/api/units/weather/{wid}", )
    async def delete_weather_sub(wid: int):
        row = await bot.database.fetchone("SELECT * FROM weather_subscriptions WHERE id = ?", (wid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM weather_subscriptions WHERE id = ?", (wid,))
        bot.heartbeat.cancel_weather_daily(wid)
        return {"ok": True}

    @app.post("/api/units/weather/{wid}/toggle", )
    async def toggle_weather_sub(wid: int):
        row = await bot.database.fetchone("SELECT * FROM weather_subscriptions WHERE id = ?", (wid,))
        if not row:
            raise HTTPException(404, "not found")
        new_active = 0 if row["active"] else 1
        await bot.database.execute(
            "UPDATE weather_subscriptions SET active = ? WHERE id = ?", (new_active, wid)
        )
        if new_active:
            bot.heartbeat.schedule_weather_daily(
                wid, row["notify_hour"], row["notify_minute"],
                row["user_id"], row["latitude"], row["longitude"], row["location"],
            )
        else:
            bot.heartbeat.cancel_weather_daily(wid)
        return {"ok": True, "active": new_active}

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
        # 全件取得して新しい順にソート（ChromaDBはソート非対応のため）
        all_items = bot.chroma.get_all(collection, limit=10000, offset=0)
        total = len(all_items)
        # created_at があればそれでソート、なければ逆順（新しいもの優先）
        has_dates = any((it.get("metadata") or {}).get("created_at") for it in all_items)
        if has_dates:
            all_items.sort(key=lambda x: (x.get("metadata") or {}).get("created_at", ""), reverse=True)
        else:
            all_items.reverse()
        items = all_items[offset:offset + limit]
        # Resolve user_id → display name for people_memory
        if collection == "people_memory":
            for item in items:
                uid = (item.get("metadata") or {}).get("user_id")
                if uid:
                    try:
                        user = bot.get_user(int(uid))
                        if user:
                            item["metadata"]["user_name"] = user.display_name
                    except (ValueError, TypeError):
                        pass
        return {"items": items, "total": total}

    @app.get("/api/memory/{collection}/search", dependencies=[Depends(_verify)])
    async def search_memory(collection: str, q: str = "", n: int = 20):
        """セマンティック検索。"""
        if collection not in _MEMORY_COLLECTIONS:
            raise HTTPException(400, f"unknown collection: {collection}")
        if not q.strip():
            raise HTTPException(400, "query parameter 'q' is required")
        results = bot.chroma.search(collection, q.strip(), n_results=n)
        # Add id from ChromaDB (search doesn't return ids by default)
        # Re-fetch with query to include ids
        col = bot.chroma.get_collection(collection)
        try:
            raw = col.query(query_texts=[q.strip()], n_results=n, include=["documents", "metadatas", "distances"])
            items = []
            ids = raw.get("ids", [[]])[0]
            docs = raw.get("documents", [[]])[0]
            metas = raw.get("metadatas", [[]])[0]
            dists = raw.get("distances", [[]])[0]
            for i, doc_id in enumerate(ids):
                item = {
                    "id": doc_id,
                    "text": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "distance": dists[i] if i < len(dists) else None,
                }
                items.append(item)
            # Resolve user names for people_memory
            if collection == "people_memory":
                for item in items:
                    uid = (item.get("metadata") or {}).get("user_id")
                    if uid:
                        try:
                            user = bot.get_user(int(uid))
                            if user:
                                item["metadata"]["user_name"] = user.display_name
                        except (ValueError, TypeError):
                            pass
            return {"items": items, "total": len(items)}
        except Exception as e:
            raise HTTPException(500, str(e))

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

    # --- LLMログ（Ollama/Gemini） ---

    @app.get("/api/logs/llm", dependencies=[Depends(_verify)])
    async def get_llm_logs(limit: int = 50, offset: int = 0, provider: str | None = None):
        logs = await bot.database.get_llm_logs(limit=limit, offset=offset, provider=provider)
        # instance を URL/IP → エージェント名に変換（過去ログ互換）
        url_to_name = getattr(bot.llm_router, "_url_to_name", {}) or {}
        for log_row in logs:
            inst = log_row.get("instance")
            if not inst:
                continue
            # 完全一致（新形式: URL または既に名前）
            if inst in url_to_name:
                log_row["instance"] = url_to_name[inst]
                continue
            # 部分一致（旧形式: IP やホスト名のみ）
            for url, name in url_to_name.items():
                if inst in url or url.find(inst) >= 0:
                    log_row["instance"] = name
                    break
        return {"logs": logs}

    # --- デバッグ: ハートビートログ ---

    @app.get("/api/debug/heartbeat-logs", dependencies=[Depends(_verify)])
    async def debug_heartbeat_logs():
        return {"logs": list(bot.heartbeat.debug_logs)}

    # --- STT設定 ---

    _STT_CONFIG_KEYS = {
        "summary_threshold_chars": int,
        "silence_trigger_minutes": int,
        "gap_split_minutes": int,
        "min_chunk_chars": int,
        "retention_days": int,
    }

    @app.get("/api/stt-config", dependencies=[Depends(_verify)])
    async def get_stt_config():
        cfg = bot.config.get("stt", {}).get("processing", {})
        return {k: cfg.get(k) for k in _STT_CONFIG_KEYS}

    @app.post("/api/stt-config", dependencies=[Depends(_verify)])
    async def set_stt_config(request: Request):
        body = await request.json()
        cfg = bot.config.setdefault("stt", {}).setdefault("processing", {})
        updated = {}
        for key, caster in _STT_CONFIG_KEYS.items():
            if key in body:
                try:
                    val = caster(body[key])
                except (TypeError, ValueError):
                    continue
                cfg[key] = val
                updated[key] = val
                await bot.database.set_setting(f"stt.processing.{key}", json.dumps(val))
        return {"ok": True, "updated": updated}

    # --- 管理: STT要約の再生成 ---

    @app.post("/api/stt/resummarize", dependencies=[Depends(_verify)])
    async def stt_resummarize(request: Request):
        """指定した stt_summaries 行を現在のプロンプトで作り直す。ids=[...] または all_non_japanese=true。"""
        from src.stt.processor import STTProcessor
        import re
        body = await request.json()
        processor = STTProcessor(bot)

        target_ids: list[int] = []
        if body.get("all_non_japanese"):
            rows = await bot.database.fetchall(
                "SELECT id, summary FROM stt_summaries"
            )
            non_ja = re.compile(r"[\uAC00-\uD7AF\u4E00-\u9FFF]")
            for r in rows:
                if non_ja.search(r["summary"]):
                    target_ids.append(r["id"])
        else:
            target_ids = [int(i) for i in body.get("ids", [])]

        results = []
        for sid in target_ids:
            ok = await processor.resummarize(sid)
            results.append({"id": sid, "ok": ok})
        return {"targets": target_ids, "results": results}

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

    # --- 会話履歴設定 ---

    @app.get("/api/chat-config", dependencies=[Depends(_verify)])
    async def get_chat_config():
        cfg = bot.config.get("units", {}).get("chat", {})
        return {
            "history_minutes": cfg.get("history_minutes", 60),
        }

    @app.post("/api/chat-config", dependencies=[Depends(_verify)])
    async def set_chat_config(request: Request):
        body = await request.json()
        cfg = bot.config.setdefault("units", {}).setdefault("chat", {})
        if "history_minutes" in body:
            val = int(body["history_minutes"])
            cfg["history_minutes"] = val
            await bot.database.set_setting("units.chat.history_minutes", json.dumps(val))
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
            "ollama_model": llm_cfg.get("ollama_model", "gemma4"),
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
        await bot.database.set_setting("character.persona", persona)
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

    # --- InnerMind モノローグ ---

    @app.get("/api/monologue", dependencies=[Depends(_verify)])
    async def get_monologues(limit: int = 50):
        rows = await bot.database.get_monologues(limit=limit)
        return {"monologues": rows}

    @app.get("/api/inner-mind/status", dependencies=[Depends(_verify)])
    async def get_inner_mind_status():
        """InnerMindの現在状態（self_model, activity, 最終思考等）。"""
        self_model = await bot.database.get_self_model()
        last_mono = await bot.database.get_last_monologue()

        # アクティビティ状態
        activity = {}
        if hasattr(bot, "activity_detector"):
            try:
                activity = await bot.activity_detector.get_status()
            except Exception:
                activity = {"error": "取得失敗"}

        # InnerMind有効/無効
        im = getattr(bot, "inner_mind", None)
        enabled = False
        if im:
            enabled_val = await im._get_setting("enabled", False)
            from src.inner_mind.core import _to_bool
            enabled = _to_bool(enabled_val)

        return {
            "self_model": self_model,
            "last_monologue": last_mono,
            "activity": activity,
            "enabled": enabled,
        }

    @app.get("/api/inner-mind/context", dependencies=[Depends(_verify)])
    async def get_inner_mind_context():
        """InnerMindの現在のコンテキストソース一覧を返す。"""
        im = getattr(bot, "inner_mind", None)
        if not im:
            return {"sources": [], "error": "InnerMind not initialized"}
        try:
            results = await im.registry.collect_all({})
            sources = []
            for sr in results:
                sources.append({
                    "name": sr["name"],
                    "text": sr["text"],
                })
            return {"sources": sources}
        except Exception as e:
            return {"sources": [], "error": str(e)}

    @app.get("/api/inner-mind/settings", dependencies=[Depends(_verify)])
    async def get_inner_mind_settings():
        im_cfg = bot.config.get("inner_mind", {})
        # DB保存値を優先読み込み
        enabled = await bot.database.get_setting("inner_mind.enabled")
        prob = await bot.database.get_setting("inner_mind.speak_probability")
        interval = await bot.database.get_setting("inner_mind.min_speak_interval_minutes")
        channel_id = await bot.database.get_setting("inner_mind.speak_channel_id")
        user_id = await bot.database.get_setting("inner_mind.target_user_id")
        return {
            "enabled": (enabled == "true") if enabled is not None else im_cfg.get("enabled", False),
            "speak_probability": float(prob) if prob is not None else im_cfg.get("speak_probability", 0.20),
            "min_speak_interval_minutes": int(interval) if interval is not None else im_cfg.get("min_speak_interval_minutes", 0),
            "thinking_interval_ticks": im_cfg.get("thinking_interval_ticks", 2),
            "speak_channel_id": channel_id if channel_id is not None else im_cfg.get("speak_channel_id", ""),
            "target_user_id": user_id if user_id is not None else im_cfg.get("target_user_id", ""),
        }

    @app.post("/api/inner-mind/settings", dependencies=[Depends(_verify)])
    async def set_inner_mind_settings(request: Request):
        body = await request.json()
        im_cfg = bot.config.setdefault("inner_mind", {})

        if "enabled" in body:
            val = bool(body["enabled"])
            im_cfg["enabled"] = val
            await bot.database.set_setting("inner_mind.enabled", str(val).lower())

        if "speak_probability" in body:
            val = float(body["speak_probability"])
            if not 0 <= val <= 1:
                raise HTTPException(400, "speak_probability must be 0.0-1.0")
            im_cfg["speak_probability"] = val
            await bot.database.set_setting("inner_mind.speak_probability", str(val))

        if "min_speak_interval_minutes" in body:
            val = int(body["min_speak_interval_minutes"])
            if val < 0:
                raise HTTPException(400, "min_speak_interval_minutes must be >= 0")
            im_cfg["min_speak_interval_minutes"] = val
            await bot.database.set_setting("inner_mind.min_speak_interval_minutes", str(val))

        if "speak_channel_id" in body:
            val = str(body["speak_channel_id"])
            im_cfg["speak_channel_id"] = val
            await bot.database.set_setting("inner_mind.speak_channel_id", val)
        if "target_user_id" in body:
            val = str(body["target_user_id"])
            im_cfg["target_user_id"] = val
            await bot.database.set_setting("inner_mind.target_user_id", val)

        return {"ok": True}

    # --- Tools: input-relay ---

    async def _agent_request(method: str, path: str, role: str | None = None) -> list[dict]:
        """Windows Agent にリクエストを送る。role指定があればそのAgentのみ。"""
        agents_pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
        if not agents_pool:
            return []
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}
        results = []
        for agent in agents_pool._agents:
            if role and agent.get("role") != role:
                continue
            base = {
                "agent": agent.get("id", agent["host"]),
                "agent_id": agent.get("id"),
                "agent_name": agent.get("name"),
                "role": agent.get("role", "unknown"),
                "host": agent.get("host"),
                "port": agent.get("port"),
            }
            url = f"http://{agent['host']}:{agent['port']}{path}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    if method == "GET":
                        resp = await client.get(url, headers=headers)
                    else:
                        resp = await client.post(url, headers=headers)
                    data = resp.json()
                results.append({**base, "alive": True, **data})
            except Exception as e:
                results.append({**base, "alive": False, "error": str(e)})
        return results

    @app.post("/api/tools/input-relay/update", )
    async def tools_input_relay_update():
        """input-relayサブモジュールをGitHubから最新取得 → メインリポに commit & push。
        その後、全Windows Agentに /update を通知して git pull で反映させる。"""
        # 2重実行防止
        if _update_lock.locked():
            return {
                "updated": False,
                "message": "別の更新処理が実行中です",
                "agents": [],
            }
        async with _update_lock:
            try:
                from src.bot import BASE_DIR
                git_dir = os.environ.get("GIT_REPO_DIR") or (
                    os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
                )
                sub_path = "windows-agent/tools/input-relay"
                sub_abs = os.path.join(git_dir, sub_path)

                # 現在のsubmoduleハッシュ
                old_hash = subprocess.run(
                    ["git", "-C", sub_abs, "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()

                # ① GitHub（.gitmodules のURL）から submodule の最新を取得
                sub_result = subprocess.run(
                    ["git", "submodule", "update", "--remote", sub_path], cwd=git_dir,
                    capture_output=True, text=True, timeout=60,
                )
                if sub_result.returncode != 0:
                    err = sub_result.stderr.strip()
                    log.error("submodule update --remote failed: %s", err)
                    raise HTTPException(500, f"submodule 更新失敗: {err}")

                new_hash = subprocess.run(
                    ["git", "-C", sub_abs, "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()

                # ② 変化なし → commit/push 不要
                if old_hash == new_hash:
                    return {
                        "updated": False,
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                        "message": f"Already up to date ({new_hash})",
                        "agents": [],
                    }

                # ③ メインリポに add + commit
                add_result = subprocess.run(
                    ["git", "add", sub_path], cwd=git_dir,
                    capture_output=True, text=True, timeout=10,
                )
                if add_result.returncode != 0:
                    raise HTTPException(500, f"git add 失敗: {add_result.stderr.strip()}")

                commit_msg = f"submodule: input-relay {old_hash} → {new_hash}"
                commit_result = subprocess.run(
                    ["git",
                     "-c", f"user.name={os.environ.get('GIT_COMMIT_USER_NAME', 'secretary-bot')}",
                     "-c", f"user.email={os.environ.get('GIT_COMMIT_USER_EMAIL', 'bot@iniwa.local')}",
                     "commit", "-m", commit_msg],
                    cwd=git_dir, capture_output=True, text=True, timeout=10,
                )
                if commit_result.returncode != 0:
                    raise HTTPException(500, f"commit 失敗: {commit_result.stderr.strip()}")

                # ④ 動的 remote / branch 取得して push
                remote_name_result = subprocess.run(
                    ["git", "remote"], cwd=git_dir,
                    capture_output=True, text=True, timeout=10,
                )
                remote_name = remote_name_result.stdout.strip().splitlines()[0] if remote_name_result.stdout.strip() else "origin"
                branch_result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=git_dir,
                    capture_output=True, text=True, timeout=10,
                )
                branch = branch_result.stdout.strip() or "main"
                push_result = subprocess.run(
                    ["git", "push", remote_name, branch], cwd=git_dir,
                    capture_output=True, text=True, timeout=30,
                )
                if push_result.returncode != 0:
                    err = push_result.stderr.strip()
                    log.error("git push failed: %s", err)
                    raise HTTPException(500, f"push 失敗: {err}")

                log.info("input-relay submodule updated: %s → %s", old_hash, new_hash)

                # ⑤ 全Windows Agentに /update を通知（git pull → 新ハッシュへ追従）
                agent_results = await _update_all_agents(bot)
                # ⑥ input-relay ツールを再起動させて新ファイルをロード（agent.py 本体は再起動不要）
                agent_tool_restart = await _post_all_agents(
                    bot, "/tools/input-relay/restart", timeout=10
                )

                return {
                    "updated": True,
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "message": f"{old_hash} → {new_hash}",
                    "agents": agent_results,
                    "agents_tool_restart": agent_tool_restart,
                }
            except HTTPException:
                raise
            except Exception as e:
                log.error("input-relay update failed: %s", e)
                raise HTTPException(500, f"Update failed: {e}")

    @app.get("/api/tools/input-relay/status", )
    async def tools_input_relay_status():
        results = await _agent_request("GET", "/tools/input-relay/status")
        return {"agents": results}

    @app.get("/api/tools/input-relay/logs/{role}", )
    async def tools_input_relay_logs(role: str, lines: int = 100):
        results = await _agent_request("GET", f"/tools/input-relay/logs?lines={lines}", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    @app.post("/api/tools/input-relay/start/{role}", )
    async def tools_input_relay_start(role: str):
        results = await _agent_request("POST", "/tools/input-relay/start", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    @app.post("/api/tools/input-relay/stop/{role}", )
    async def tools_input_relay_stop(role: str):
        results = await _agent_request("POST", "/tools/input-relay/stop", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    @app.post("/api/tools/input-relay/restart/{role}", )
    async def tools_input_relay_restart(role: str):
        results = await _agent_request("POST", "/tools/input-relay/restart", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    # --- STT管理 ---

    async def _agent_request_json(method: str, path: str, role: str | None = None, json_body: dict | None = None) -> list[dict]:
        """Windows Agent にJSONボディ付きリクエストを送る。"""
        agents_pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
        if not agents_pool:
            return []
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}
        results = []
        for agent in agents_pool._agents:
            if role and agent.get("role") != role:
                continue
            url = f"http://{agent['host']}:{agent['port']}{path}"
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    if method == "GET":
                        resp = await client.get(url, headers=headers)
                    else:
                        resp = await client.post(url, headers=headers, json=json_body)
                    data = resp.json()
                results.append({"agent": agent.get("id", agent["host"]), "role": agent.get("role", "unknown"), **data})
            except Exception as e:
                results.append({"agent": agent.get("id", agent["host"]), "role": agent.get("role", "unknown"), "error": str(e)})
        return results

    @app.get("/api/stt/status", )
    async def stt_status():
        """全AgentのSTT状態を返す。"""
        results = await _agent_request("GET", "/stt/status")
        return {"agents": results}

    @app.get("/api/stt/devices", )
    async def stt_devices(role: str = "sub"):
        """指定PCのマイクデバイス一覧を返す。"""
        results = await _agent_request("GET", "/stt/devices", role=role)
        if not results:
            return {"devices": [], "error": f"{role} PC agent not reachable"}
        return results[0]

    @app.post("/api/stt/control", )
    async def stt_control(request: Request):
        """STT制御（init/start/stop/set_device）。roleパラメータで対象PC指定。"""
        body = await request.json()
        role = body.pop("role", "sub")
        results = await _agent_request_json("POST", "/stt/control", role=role, json_body=body)
        if not results:
            raise HTTPException(503, f"{role} PC agent not reachable")
        return results[0]

    @app.get("/api/stt/model/status", )
    async def stt_model_status():
        """Sub PCのWhisperモデル状態を返す。"""
        results = await _agent_request("GET", "/stt/model/status", role="sub")
        if not results:
            return {"loaded": False, "error": "Sub PC agent not reachable"}
        return results[0]

    @app.get("/api/stt/transcripts", )
    async def stt_transcripts(role: str = "sub"):
        """指定PCの最新transcriptを返す。"""
        results = await _agent_request("GET", "/stt/transcripts", role=role)
        if not results:
            return {"transcripts": []}
        return results[0]

    @app.get("/api/stt/summaries")
    async def stt_summaries(limit: int = 20):
        """ローカルDBからSTT要約一覧を返す。"""
        rows = await bot.database.fetchall(
            "SELECT id, summary, transcript_ids, created_at "
            "FROM stt_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return {"summaries": [dict(r) for r in rows]}

    # --- OBS: ゲームプロセス管理 ---
    # NOTE: ゲームは Main PC で実行されるため game_processes.json は Main PC が
    # マスター。Sub PC の OBS Manager は /activity 経由で Main PC に問い合わせる。
    # そのため /api/obs/games の編集は Main PC に向ける。

    @app.get("/api/obs/games", )
    async def obs_games():
        results = await _agent_request("GET", "/obs/games", role="main")
        if not results:
            return {"games": [], "groups": []}
        return results[0]

    @app.post("/api/obs/games", )
    async def obs_games_save(request: Request):
        body = await request.json()
        results = await _agent_request_json("POST", "/obs/games", role="main", json_body=body)
        if not results:
            raise HTTPException(502, "Main PC agent unreachable")
        return results[0]

    # OBS 本体（WebSocket / ファイル整理 / ログ）は Sub PC 上で動作
    @app.get("/api/obs/status", )
    async def obs_status():
        results = await _agent_request("GET", "/obs/status", role="sub")
        if not results:
            return {"obs_connected": False}
        return results[0]

    @app.get("/api/obs/logs", )
    async def obs_logs(lines: int = 100):
        results = await _agent_request("GET", f"/obs/logs?lines={lines}", role="sub")
        if not results:
            return {"logs": []}
        return results[0]

    # --- Main PC アクティビティ（ゲームプレイ状況） ---

    @app.get("/api/activity/main", )
    async def activity_main():
        """Main PC のフォアグラウンド / ゲーム状況を返す。"""
        results = await _agent_request("GET", "/activity", role="main")
        if not results:
            return {"alive": False, "error": "Main PC agent not reachable"}
        return results[0]

    # --- Activity history (Main PC 過去プレイ履歴) ---

    def _activity_cutoff(days: int) -> str | None:
        """days=0 は全期間（None）。それ以外は days 日前の ISO 文字列。"""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        if days <= 0:
            return None
        _JST = _tz(_td(hours=9))
        return (_dt.now(tz=_JST) - _td(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def _activity_range(
        days: int, start: str | None = None, end: str | None = None
    ) -> tuple[list[str], list, dict]:
        """期間指定を where 断片とパラメータに変換。
        start/end (YYYY-MM-DD) が与えられたら優先。end は排他的終端として翌日00:00を使う。
        """
        from datetime import date as _date, timedelta as _td
        parts: list[str] = []
        params: list = []
        meta: dict = {}
        if start or end:
            if start:
                parts.append("start_at >= ?")
                params.append(f"{start} 00:00:00")
                meta["start"] = start
            if end:
                try:
                    end_excl = (_date.fromisoformat(end) + _td(days=1)).strftime(
                        "%Y-%m-%d 00:00:00"
                    )
                except ValueError:
                    end_excl = f"{end} 23:59:59"
                parts.append("start_at < ?")
                params.append(end_excl)
                meta["end"] = end
            return parts, params, meta
        cutoff = _activity_cutoff(days)
        if cutoff:
            parts.append("start_at >= ?")
            params.append(cutoff)
        meta["days"] = days
        meta["since"] = cutoff
        return parts, params, meta

    @app.get("/api/activity/stats", dependencies=[Depends(_verify)])
    async def activity_stats(
        days: int = 7, start: str | None = None, end: str | None = None
    ):
        """期間内のゲーム / フォアグラウンド集計（ランキング）。start/end (YYYY-MM-DD) が優先。"""
        days = max(0, min(int(days or 7), 3650))
        parts, params, meta = _activity_range(days, start, end)
        where_clause = " AND ".join(["end_at IS NOT NULL", *parts])
        games = await bot.database.fetchall(
            f"""
            SELECT game_name, SUM(COALESCE(duration_sec, 0)) AS sec, COUNT(*) AS sessions,
                   MAX(start_at) AS last_played, MAX(COALESCE(duration_sec, 0)) AS longest_sec
            FROM game_sessions WHERE {where_clause}
            GROUP BY game_name ORDER BY sec DESC
            """,
            tuple(params),
        )
        fg = await bot.database.fetchall(
            f"""
            SELECT process_name, during_game,
                   SUM(COALESCE(duration_sec, 0)) AS sec, COUNT(*) AS sessions
            FROM foreground_sessions WHERE {where_clause}
            GROUP BY process_name, during_game ORDER BY sec DESC
            """,
            tuple(params),
        )
        return {**meta, "games": games, "foreground": fg}

    @app.get("/api/activity/summary", dependencies=[Depends(_verify)])
    async def activity_summary(
        days: int = 7, start: str | None = None, end: str | None = None
    ):
        """期間サマリ: 総プレイ時間・セッション数・アクティブ日数・最長セッション。"""
        days = max(0, min(int(days or 7), 3650))
        parts, params, meta = _activity_range(days, start, end)
        where_clause = " AND ".join(["end_at IS NOT NULL", *parts])

        row = await bot.database.fetchone(
            f"""
            SELECT COUNT(*) AS sessions,
                   COALESCE(SUM(duration_sec), 0) AS total_sec,
                   COALESCE(MAX(duration_sec), 0) AS longest_sec,
                   COUNT(DISTINCT date(start_at)) AS active_days,
                   COUNT(DISTINCT game_name) AS distinct_games
            FROM game_sessions WHERE {where_clause}
            """,
            tuple(params),
        )
        earliest = await bot.database.fetchone(
            "SELECT MIN(start_at) AS earliest FROM game_sessions WHERE end_at IS NOT NULL"
        )
        return {
            **meta,
            "sessions": row["sessions"] if row else 0,
            "total_sec": row["total_sec"] if row else 0,
            "longest_sec": row["longest_sec"] if row else 0,
            "active_days": row["active_days"] if row else 0,
            "distinct_games": row["distinct_games"] if row else 0,
            "earliest": earliest["earliest"] if earliest else None,
        }

    @app.get("/api/activity/daily", dependencies=[Depends(_verify)])
    async def activity_daily(
        days: int = 30,
        year: int | None = None,
        month: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ):
        """日別のゲーム時間（ゲーム別の内訳付き）。棒グラフ / カレンダー用。
        start/end (YYYY-MM-DD) が最優先、次に year/month、最後に直近 days 日。"""
        from datetime import date as _date
        if start or end:
            parts, params_list, meta = _activity_range(days, start, end)
            where = " AND ".join(["end_at IS NOT NULL", *parts])
            params: tuple = tuple(params_list)
        elif year and month:
            month_start = _date(year, month, 1)
            if month == 12:
                next_month = _date(year + 1, 1, 1)
            else:
                next_month = _date(year, month + 1, 1)
            m_start = month_start.strftime("%Y-%m-%d 00:00:00")
            m_end = next_month.strftime("%Y-%m-%d 00:00:00")
            where = "end_at IS NOT NULL AND start_at >= ? AND start_at < ?"
            params = (m_start, m_end)
            meta = {"year": year, "month": month}
        else:
            days = max(1, min(int(days or 30), 3650))
            cutoff = _activity_cutoff(days)
            where = "end_at IS NOT NULL AND start_at >= ?"
            params = (cutoff,)
            meta = {"days": days, "since": cutoff}

        rows = await bot.database.fetchall(
            f"""
            SELECT date(start_at) AS day, game_name,
                   SUM(COALESCE(duration_sec, 0)) AS sec
            FROM game_sessions
            WHERE {where}
            GROUP BY day, game_name ORDER BY day
            """,
            params,
        )
        by_day: dict[str, dict] = {}
        for r in rows:
            d = r["day"]
            if d not in by_day:
                by_day[d] = {"day": d, "total_sec": 0, "games": []}
            by_day[d]["total_sec"] += int(r["sec"] or 0)
            by_day[d]["games"].append({"game_name": r["game_name"], "sec": int(r["sec"] or 0)})
        return {**meta, "daily": list(by_day.values())}

    @app.get("/api/activity/sessions", dependencies=[Depends(_verify)])
    async def activity_sessions(
        days: int = 30,
        game: str = "",
        day: str = "",
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """個別プレイセッション一覧（新しい順）。game / day / start+end で絞込み。
        優先度: day > start/end > days。"""
        days = max(0, min(int(days or 30), 3650))
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))

        where_parts = ["end_at IS NOT NULL"]
        params: list = []
        if day:
            where_parts.append("date(start_at) = ?")
            params.append(day)
        elif start or end:
            range_parts, range_params, _ = _activity_range(days, start, end)
            where_parts.extend(range_parts)
            params.extend(range_params)
        else:
            cutoff = _activity_cutoff(days)
            if cutoff:
                where_parts.append("start_at >= ?")
                params.append(cutoff)
        if game:
            where_parts.append("game_name = ?")
            params.append(game)
        where = "WHERE " + " AND ".join(where_parts)

        total_row = await bot.database.fetchone(
            f"SELECT COUNT(*) AS c FROM game_sessions {where}", tuple(params)
        )
        rows = await bot.database.fetchall(
            f"""
            SELECT id, game_name, start_at, end_at, duration_sec
            FROM game_sessions {where}
            ORDER BY start_at DESC LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        )
        return {
            "total": total_row["c"] if total_row else 0,
            "limit": limit, "offset": offset,
            "sessions": rows,
        }

    # --- RSS フィード管理 ---

    @app.get("/api/rss/feeds", dependencies=[Depends(_verify)])
    async def rss_feeds():
        # プリセットフィードを同期
        from src.rss.fetcher import RSSFetcher
        fetcher = RSSFetcher(bot)
        await fetcher.ensure_preset_feeds()

        feeds = await bot.database.fetchall(
            "SELECT * FROM rss_feeds ORDER BY category, title"
        )
        presets = bot.config.get("rss", {}).get("presets", {})
        categories = {k: v.get("label", k) for k, v in presets.items()}

        # ユーザーの無効化設定を取得
        disabled_feed_ids: set[int] = set()
        disabled_categories: list[str] = []
        if _webgui_user_id:
            prefs = await bot.database.fetchall(
                "SELECT feed_id, category, enabled FROM rss_user_prefs "
                "WHERE user_id = ? AND enabled = 0",
                (_webgui_user_id,),
            )
            for p in prefs:
                if p.get("feed_id") is not None:
                    disabled_feed_ids.add(p["feed_id"])
                if p.get("category"):
                    disabled_categories.append(p["category"])

        # feed ごとに user_disabled フラグを付与
        feeds_out = []
        for f in feeds:
            item = dict(f)
            item["user_disabled"] = item.get("id") in disabled_feed_ids
            feeds_out.append(item)

        return {
            "feeds": feeds_out,
            "categories": categories,
            "disabled_categories": disabled_categories,
        }

    @app.post("/api/rss/feeds", dependencies=[Depends(_verify)])
    async def rss_feed_add(request: Request):
        body = await request.json()
        url = (body.get("url") or "").strip()
        title = (body.get("title") or url[:50]).strip()
        category = (body.get("category") or "other").strip()
        if not url:
            raise HTTPException(400, "URL is required")
        existing = await bot.database.fetchone(
            "SELECT id FROM rss_feeds WHERE url = ?", (url,)
        )
        if existing:
            raise HTTPException(409, f"Already exists (#{existing['id']})")
        from src.database import jst_now
        await bot.database.execute(
            """INSERT INTO rss_feeds (url, title, category, is_preset, added_by, created_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (url, title, category, "webgui", jst_now()),
        )
        return {"ok": True}

    @app.delete("/api/rss/feeds/{feed_id}", dependencies=[Depends(_verify)])
    async def rss_feed_delete(feed_id: int):
        feed = await bot.database.fetchone(
            "SELECT * FROM rss_feeds WHERE id = ?", (feed_id,)
        )
        if not feed:
            raise HTTPException(404, "Feed not found")
        if feed["is_preset"]:
            raise HTTPException(400, "Cannot delete preset feed")
        await bot.database.execute("DELETE FROM rss_articles WHERE feed_id = ?", (feed_id,))
        await bot.database.execute("DELETE FROM rss_feeds WHERE id = ?", (feed_id,))
        return {"ok": True}

    @app.get("/api/rss/articles", dependencies=[Depends(_verify)])
    async def rss_articles(
        category: str | None = None, limit: int = 50, offset: int = 0
    ):
        uid = _webgui_user_id or ""
        if category:
            rows = await bot.database.fetchall(
                """SELECT a.*, f.title AS feed_title, f.category,
                          fb.rating AS user_rating
                   FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
                   LEFT JOIN rss_feedback fb
                     ON fb.article_id = a.id AND fb.user_id = ?
                   WHERE f.category = ?
                   ORDER BY a.published_at DESC LIMIT ? OFFSET ?""",
                (uid, category, limit, offset),
            )
        else:
            rows = await bot.database.fetchall(
                """SELECT a.*, f.title AS feed_title, f.category,
                          fb.rating AS user_rating
                   FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
                   LEFT JOIN rss_feedback fb
                     ON fb.article_id = a.id AND fb.user_id = ?
                   ORDER BY a.published_at DESC LIMIT ? OFFSET ?""",
                (uid, limit, offset),
            )
        return {"articles": rows}

    @app.post("/api/rss/fetch", dependencies=[Depends(_verify)])
    async def rss_fetch_now():
        """手動で全フィードをフェッチ。"""
        from src.rss.fetcher import RSSFetcher
        fetcher = RSSFetcher(bot)
        result = await fetcher.fetch_all_feeds()
        return result

    @app.post("/api/rss/articles/{article_id}/feedback", dependencies=[Depends(_verify)])
    async def rss_article_feedback(article_id: int, request: Request):
        """記事の 👍 / 👎 フィードバックを記録（rating=0 は取り消し）。"""
        if not _webgui_user_id:
            raise HTTPException(400, "WEBGUI_USER_ID not configured")
        body = await request.json()
        rating = body.get("rating")
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid rating")
        if rating not in (-1, 0, 1):
            raise HTTPException(400, "Invalid rating")

        if rating == 0:
            await bot.database.execute(
                "DELETE FROM rss_feedback WHERE user_id = ? AND article_id = ?",
                (_webgui_user_id, article_id),
            )
        else:
            from src.database import jst_now
            await bot.database.execute(
                """INSERT OR REPLACE INTO rss_feedback
                   (user_id, article_id, rating, created_at)
                   VALUES (?, ?, ?, ?)""",
                (_webgui_user_id, article_id, rating, jst_now()),
            )
        return {"ok": True, "rating": rating}

    @app.post("/api/rss/feeds/{feed_id}/toggle", dependencies=[Depends(_verify)])
    async def rss_feed_toggle(feed_id: int, request: Request):
        """フィードのユーザー単位有効/無効切り替え。"""
        if not _webgui_user_id:
            raise HTTPException(400, "WEBGUI_USER_ID not configured")
        body = await request.json()
        enabled = bool(body.get("enabled"))

        feed = await bot.database.fetchone(
            "SELECT id FROM rss_feeds WHERE id = ?", (feed_id,)
        )
        if not feed:
            raise HTTPException(404, "Feed not found")

        if enabled:
            await bot.database.execute(
                "DELETE FROM rss_user_prefs WHERE user_id = ? AND feed_id = ?",
                (_webgui_user_id, feed_id),
            )
        else:
            await bot.database.execute(
                """INSERT OR REPLACE INTO rss_user_prefs
                   (user_id, feed_id, enabled) VALUES (?, ?, 0)""",
                (_webgui_user_id, feed_id),
            )
        return {"ok": True, "enabled": enabled}

    # --- Docker ログ監視 ---

    @app.get("/api/docker-monitor/errors", dependencies=[Depends(_verify)])
    async def get_docker_errors(
        dismissed: int = 0,
        level: str = "error",
        limit: int = 100,
        offset: int = 0,
    ):
        # level は 'error' / 'warning' / 'all'
        if level == "all":
            rows = await bot.database.fetchall(
                "SELECT * FROM docker_error_log WHERE dismissed = ? "
                "ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                (dismissed, limit, offset),
            )
            total_row = await bot.database.fetchone(
                "SELECT COUNT(*) as cnt FROM docker_error_log WHERE dismissed = ?",
                (dismissed,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM docker_error_log WHERE dismissed = ? AND level = ? "
                "ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                (dismissed, level, limit, offset),
            )
            total_row = await bot.database.fetchone(
                "SELECT COUNT(*) as cnt FROM docker_error_log WHERE dismissed = ? AND level = ?",
                (dismissed, level),
            )
        return {"items": rows, "total": total_row["cnt"] if total_row else 0}

    @app.post("/api/docker-monitor/errors/{error_id}/dismiss", dependencies=[Depends(_verify)])
    async def dismiss_docker_error(error_id: int):
        await bot.database.execute(
            "UPDATE docker_error_log SET dismissed = 1 WHERE id = ?", (error_id,)
        )
        return {"ok": True}

    @app.post("/api/docker-monitor/errors/dismiss-all", dependencies=[Depends(_verify)])
    async def dismiss_all_docker_errors(level: str = "error"):
        # level 指定で対象を絞る（'all' 指定時はレベル問わず全て）
        if level == "all":
            await bot.database.execute(
                "UPDATE docker_error_log SET dismissed = 1 WHERE dismissed = 0"
            )
        else:
            await bot.database.execute(
                "UPDATE docker_error_log SET dismissed = 1 WHERE dismissed = 0 AND level = ?",
                (level,),
            )
        return {"ok": True}

    @app.delete("/api/docker-monitor/errors/{error_id}", dependencies=[Depends(_verify)])
    async def delete_docker_error(error_id: int):
        await bot.database.execute("DELETE FROM docker_error_log WHERE id = ?", (error_id,))
        return {"ok": True}

    @app.get("/api/docker-monitor/exclusions", dependencies=[Depends(_verify)])
    async def get_docker_exclusions():
        rows = await bot.database.fetchall(
            "SELECT * FROM docker_log_exclusions ORDER BY created_at DESC"
        )
        return {"items": rows}

    @app.post("/api/docker-monitor/exclusions", dependencies=[Depends(_verify)])
    async def add_docker_exclusion(request: Request):
        data = await request.json()
        container_name = data.get("container_name", "").strip()
        pattern = data.get("pattern", "").strip()
        reason = data.get("reason", "").strip()
        if not pattern:
            raise HTTPException(400, "pattern is required")
        from src.database import jst_now
        try:
            await bot.database.execute(
                "INSERT INTO docker_log_exclusions "
                "(container_name, pattern, reason, added_by, created_at) "
                "VALUES (?, ?, ?, 'webgui', ?)",
                (container_name, pattern, reason, jst_now()),
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, "pattern already exists")
            raise
        return {"ok": True}

    @app.delete("/api/docker-monitor/exclusions/{exc_id}", dependencies=[Depends(_verify)])
    async def delete_docker_exclusion(exc_id: int):
        await bot.database.execute("DELETE FROM docker_log_exclusions WHERE id = ?", (exc_id,))
        return {"ok": True}

    # /api/docker-monitor/settings は廃止（エラー通知は常時有効化されたため）

    # --- 静的ファイル & フロントエンド ---

    # Cloudflare / ブラウザの ES モジュールキャッシュ対策
    # JS/CSS は常にオリジンに再検証させる
    @app.middleware("http")
    async def static_cache_control(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") and request.url.path.rsplit(".", 1)[-1] in ("js", "css"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse, )
    async def index():
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        try:
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            # Cache busting: append version query to JS/CSS references
            import hashlib, glob as _glob
            static_dir_path = os.path.join(os.path.dirname(__file__), "static")
            h = hashlib.md5()
            for p in sorted(_glob.glob(os.path.join(static_dir_path, "**", "*.js"), recursive=True)):
                h.update(str(os.path.getmtime(p)).encode())
            ver = h.hexdigest()[:8]
            html = html.replace('src="/static/js/app.js"', f'src="/static/js/app.js?v={ver}"')
            html = html.replace('href="/static/css/base.css"', f'href="/static/css/base.css?v={ver}"')
            html = html.replace('href="/static/css/layout.css"', f'href="/static/css/layout.css?v={ver}"')
            html = html.replace('href="/static/css/components.css"', f'href="/static/css/components.css?v={ver}"')
            return html
        except FileNotFoundError:
            return "<h1>Secretary Bot WebGUI</h1><p>static/index.html not found</p>"

    return app
