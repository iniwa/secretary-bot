"""FastAPI WebGUI + /health エンドポイント。"""

import asyncio
import json
import os
import re
import subprocess
from datetime import datetime

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.web.cache_headers import NO_CACHE_HEADERS, NoCacheStaticFiles

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
                    # 親リポが最新でも、サブモジュールの追跡ブランチは進んでいる
                    # 可能性があるので、ここでも submodule update を実行
                    sub_result = subprocess.run(
                        ["git", "submodule", "update", "--init", "--recursive", "--remote"],
                        cwd=git_dir, capture_output=True, text=True, timeout=120,
                    )
                    sub_changed = False
                    if sub_result.returncode != 0:
                        log.warning("submodule update failed: %s", sub_result.stderr.strip())
                    elif sub_result.stdout.strip():
                        log.info("submodule updated (main at head): %s", sub_result.stdout.strip())
                        sub_changed = True

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
                    if sub_changed:
                        # サブモジュールが進んだので再起動してコードを反映
                        background_tasks.add_task(_delayed_restart, 2)
                        return {
                            "updated": True,
                            "message": f"親リポは最新 ({hash_before[:7]}) / サブモジュール更新あり",
                            "restarted": True,
                            "restart_detail": "サブモジュール反映のため再起動します…",
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
                # --init: 未初期化なら clone
                # --remote: 親リポが記録している commit ではなく、各サブモジュールの
                #           追跡ブランチ最新まで進める（親の pin は無視）
                sub_result = subprocess.run(
                    ["git", "submodule", "update", "--init", "--recursive", "--remote"],
                    cwd=git_dir, capture_output=True, text=True, timeout=120,
                )
                if sub_result.returncode != 0:
                    log.warning("submodule update failed: %s", sub_result.stderr.strip())
                elif sub_result.stdout.strip():
                    log.info("submodule updated: %s", sub_result.stdout.strip())

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

    # --- 汎用 settings API ---
    # フロントからセクション単位で一括取得・保存するための汎用入口。
    # 個別エンドポイント（/api/llm-config 等）は互換維持のため並存。

    _SCALAR_PREFIXES: tuple[str, ...] = (
        "llm.", "gemini.", "heartbeat.", "inner_mind.", "character.",
        "chat.", "rss.", "weather.", "searxng.", "rakuten_search.",
        "stt.", "delegation.", "activity.", "docker_monitor.", "memory.",
    )

    def _serialize(val) -> str:
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            return val
        return json.dumps(val, ensure_ascii=False)

    def _coerce(val: str):
        if val == "true":
            return True
        if val == "false":
            return False
        try:
            if "." in val:
                return float(val)
            return int(val)
        except (ValueError, TypeError):
            pass
        try:
            return json.loads(val)
        except (ValueError, json.JSONDecodeError, TypeError):
            return val

    @app.get("/api/settings", dependencies=[Depends(_verify)])
    async def get_settings(prefix: str = ""):
        if prefix and not any(prefix.startswith(p) or p.startswith(prefix) for p in _SCALAR_PREFIXES):
            raise HTTPException(400, f"prefix '{prefix}' not allowed")
        raw = await bot.database.get_all_settings(prefix)
        return {k: _coerce(v) for k, v in raw.items()}

    @app.post("/api/settings", dependencies=[Depends(_verify)])
    async def set_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        saved: list[str] = []
        for key, val in body.items():
            if not any(key.startswith(p) for p in _SCALAR_PREFIXES):
                raise HTTPException(400, f"key '{key}' not allowed")
            await bot.database.set_setting(key, _serialize(val))
            # bot.config にもネストで反映（次回再起動まで現行プロセスでも有効に）
            parts = key.split(".")
            cur = bot.config
            for seg in parts[:-1]:
                cur = cur.setdefault(seg, {}) if isinstance(cur, dict) else cur
            if isinstance(cur, dict):
                cur[parts[-1]] = val
            saved.append(key)
        return {"ok": True, "saved": saved}

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
        interval = await bot.database.get_setting("inner_mind.min_speak_interval_minutes")
        channel_id = await bot.database.get_setting("inner_mind.speak_channel_id")
        user_id = await bot.database.get_setting("inner_mind.target_user_id")
        tavily_raw = await bot.database.get_setting("inner_mind.tavily_news.queries")
        if tavily_raw:
            tavily_queries = [s.strip() for s in tavily_raw.split(",") if s.strip()]
        else:
            tavily_queries = list(im_cfg.get("tavily_news", {}).get("queries", []) or [])
        return {
            "enabled": (enabled == "true") if enabled is not None else im_cfg.get("enabled", False),
            "min_speak_interval_minutes": int(interval) if interval is not None else im_cfg.get("min_speak_interval_minutes", 0),
            "thinking_interval_ticks": im_cfg.get("thinking_interval_ticks", 2),
            "speak_channel_id": channel_id if channel_id is not None else im_cfg.get("speak_channel_id", ""),
            "target_user_id": user_id if user_id is not None else im_cfg.get("target_user_id", ""),
            "tavily_queries": tavily_queries,
        }

    @app.post("/api/inner-mind/settings", dependencies=[Depends(_verify)])
    async def set_inner_mind_settings(request: Request):
        body = await request.json()
        im_cfg = bot.config.setdefault("inner_mind", {})

        if "enabled" in body:
            val = bool(body["enabled"])
            im_cfg["enabled"] = val
            await bot.database.set_setting("inner_mind.enabled", str(val).lower())

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

        if "tavily_queries" in body:
            raw = body["tavily_queries"]
            if isinstance(raw, list):
                items = [str(s).strip() for s in raw if str(s).strip()]
            else:
                items = [s.strip() for s in str(raw).split(",") if s.strip()]
            # CSV形式で保存（内部カンマを含むクエリは想定外）
            csv = ",".join(items)
            im_cfg.setdefault("tavily_news", {})["queries"] = items
            await bot.database.set_setting("inner_mind.tavily_news.queries", csv)

        return {"ok": True}

    @app.get("/api/inner-mind/dispatches", dependencies=[Depends(_verify)])
    async def get_inner_mind_dispatches(limit: int = 20):
        """直近の dispatch 結果（mimi_monologue のうち action 実行あり）を返す。"""
        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit = 20
        rows = await bot.database.fetchall(
            "SELECT id, created_at, action, reasoning, action_result, pending_id "
            "FROM mimi_monologue "
            "WHERE action IS NOT NULL AND action != 'no_op' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return {"dispatches": rows}

    # --- InnerMind 自律: pending_actions ---

    @app.get("/api/inner-mind/autonomy", dependencies=[Depends(_verify)])
    async def get_autonomy_settings():
        raw = await bot.database.get_all_settings("inner_mind.autonomy.")
        return {k.removeprefix("inner_mind.autonomy."): _coerce(v) for k, v in raw.items()}

    @app.post("/api/inner-mind/autonomy", dependencies=[Depends(_verify)])
    async def set_autonomy_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        saved = []
        for short_key, val in body.items():
            key = f"inner_mind.autonomy.{short_key}"
            await bot.database.set_setting(key, _serialize(val))
            saved.append(key)
        return {"ok": True, "saved": saved}

    @app.get("/api/inner-mind/autonomy/units", dependencies=[Depends(_verify)])
    async def get_autonomy_units():
        """全ユニットの (tier, AUTONOMOUS_ACTIONS) を列挙。UI のチェックリスト用。"""
        tier2, tier3 = [], []
        for cog in list(bot.cogs.values()):
            tier = getattr(cog, "AUTONOMY_TIER", 4)
            actions = getattr(cog, "AUTONOMOUS_ACTIONS", []) or []
            unit_name = getattr(cog, "UNIT_NAME", "") or cog.__class__.__name__
            if not actions:
                continue
            for m in actions:
                entry = {
                    "unit_name": unit_name,
                    "method": m,
                    "description": getattr(cog, "UNIT_DESCRIPTION", ""),
                    "key": f"{unit_name}.{m}",
                }
                if tier == 2:
                    tier2.append(entry)
                elif tier == 3:
                    tier3.append(entry)
        return {"tier2": tier2, "tier3": tier3}

    @app.get("/api/pending", dependencies=[Depends(_verify)])
    async def list_pending(status: str | None = None, limit: int = 100):
        items = await bot.database.list_pending_actions(status=status, limit=limit)
        pending_count = await bot.database.count_pending_unread()
        return {"items": items, "counts": {"pending": pending_count}}

    @app.get("/api/pending/unread-count", dependencies=[Depends(_verify)])
    async def pending_unread_count():
        c = await bot.database.count_pending_unread()
        return {"count": c}

    @app.get("/api/pending/{pid}", dependencies=[Depends(_verify)])
    async def get_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        return p

    @app.post("/api/pending/{pid}/approve", dependencies=[Depends(_verify)])
    async def approve_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        if p.get("status") != "pending":
            raise HTTPException(409, f"already {p.get('status')}")
        try:
            params = json.loads(p.get("params") or "{}")
        except Exception:
            params = {}
        result = await bot.actuator._execute_unit(
            p.get("unit_name") or "", p.get("method") or "",
            {"params": params}, p.get("monologue_id"),
        )
        if result.get("status") == "executed":
            await bot.database.resolve_pending_action(
                pid, "executed",
                json.dumps(result.get("result"), ensure_ascii=False), None,
            )
            await bot.actuator._rewrite_approval_message(p, "✅ WebGUIで承認")
        else:
            await bot.database.resolve_pending_action(
                pid, "failed", None, result.get("reason"),
            )
        return {"ok": True, "result": result}

    @app.post("/api/pending/{pid}/reject", dependencies=[Depends(_verify)])
    async def reject_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        if p.get("status") != "pending":
            raise HTTPException(409, f"already {p.get('status')}")
        await bot.database.resolve_pending_action(pid, "rejected", None, None)
        await bot.actuator._rewrite_approval_message(p, "❌ WebGUIで却下")
        return {"ok": True}

    @app.post("/api/pending/{pid}/cancel", dependencies=[Depends(_verify)])
    async def cancel_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        if p.get("status") != "pending":
            raise HTTPException(409, f"already {p.get('status')}")
        await bot.database.resolve_pending_action(pid, "cancelled", None, None)
        await bot.actuator._rewrite_approval_message(p, "🚫 キャンセル")
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
        """days=0 は全期間（None）。それ以外は「今日を含む直近 days 日」の起点 00:00（JST）。
        例: days=7 かつ今日=2026-04-15 → '2026-04-09 00:00:00'。"""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        if days <= 0:
            return None
        _JST = _tz(_td(hours=9))
        start_date = _dt.now(tz=_JST).date() - _td(days=days - 1)
        return start_date.strftime("%Y-%m-%d 00:00:00")

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
        days: int = 7, start: str | None = None, end: str | None = None,
        day: str = "",
    ):
        """期間内のゲーム / フォアグラウンド集計（ランキング）。
        優先度: day > start/end > days。"""
        days = max(0, min(int(days or 7), 3650))
        if day:
            parts = ["date(start_at) = ?"]
            params: list = [day]
            meta = {"day": day}
        else:
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
            SELECT pc, process_name, during_game,
                   SUM(COALESCE(duration_sec, 0)) AS sec, COUNT(*) AS sessions
            FROM foreground_sessions WHERE {where_clause}
            GROUP BY pc, process_name, during_game ORDER BY sec DESC
            """,
            tuple(params),
        )
        # 両 PC 同時操作時間（Main サンプルの active_pcs に main と sub の両方が含まれる回数 × poll間隔）
        sim_where_parts = ["pc='main'", "active_pcs LIKE '%main%'", "active_pcs LIKE '%sub%'"]
        sim_params: list = []
        if day:
            sim_where_parts.append("date(ts) = ?")
            sim_params.append(day)
        elif start or end:
            r_parts, r_params, _ = _activity_range(days, start, end)
            # activity_samples.ts を起点にするため start_at を ts へ書き換え
            for rp in r_parts:
                sim_where_parts.append(rp.replace("start_at", "ts"))
            sim_params.extend(r_params)
        else:
            cutoff_sim = _activity_cutoff(days)
            if cutoff_sim:
                sim_where_parts.append("ts >= ?")
                sim_params.append(cutoff_sim)
        sim_row = await bot.database.fetchone(
            f"SELECT COUNT(*) AS c FROM activity_samples WHERE {' AND '.join(sim_where_parts)}",
            tuple(sim_params),
        )
        poll_interval = int(bot.config.get("activity", {}).get("poll_interval_seconds", 60))
        simultaneous_sec = (sim_row["c"] if sim_row else 0) * poll_interval
        return {**meta, "games": games, "foreground": fg, "simultaneous_sec": simultaneous_sec}

    @app.get("/api/activity/summary", dependencies=[Depends(_verify)])
    async def activity_summary(
        days: int = 7, start: str | None = None, end: str | None = None,
        day: str = "",
    ):
        """期間サマリ: 総プレイ時間・セッション数・アクティブ日数・最長セッション。
        優先度: day > start/end > days。"""
        days = max(0, min(int(days or 7), 3650))
        if day:
            parts = ["date(start_at) = ?"]
            params: list = [day]
            meta = {"day": day}
        else:
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
        pc: str = "main",
    ):
        """日別の活動時間。棒グラフ / カレンダー用。
        pc='main' (default): game_sessions（ゲームプレイ時間）
        pc='sub': foreground_sessions WHERE pc='sub'（Sub PC 作業時間）
        pc='both': 両方を含めて返す（クライアントで並列表示）
        start/end (YYYY-MM-DD) が最優先、次に year/month、最後に直近 days 日。"""
        from datetime import date as _date
        if pc not in ("main", "sub", "both"):
            pc = "main"
        if start or end:
            parts, params_list, meta = _activity_range(days, start, end)
            base_where = " AND ".join(["end_at IS NOT NULL", *parts])
            params: tuple = tuple(params_list)
        elif year and month:
            month_start = _date(year, month, 1)
            if month == 12:
                next_month = _date(year + 1, 1, 1)
            else:
                next_month = _date(year, month + 1, 1)
            m_start = month_start.strftime("%Y-%m-%d 00:00:00")
            m_end = next_month.strftime("%Y-%m-%d 00:00:00")
            base_where = "end_at IS NOT NULL AND start_at >= ? AND start_at < ?"
            params = (m_start, m_end)
            meta = {"year": year, "month": month}
        else:
            days = max(1, min(int(days or 30), 3650))
            cutoff = _activity_cutoff(days)
            base_where = "end_at IS NOT NULL AND start_at >= ?"
            params = (cutoff,)
            meta = {"days": days, "since": cutoff}

        # Main: game_sessions（pc カラムなし → そのまま）
        main_by_day: dict[str, dict] = {}
        if pc in ("main", "both"):
            rows = await bot.database.fetchall(
                f"""
                SELECT date(start_at) AS day, game_name,
                       SUM(COALESCE(duration_sec, 0)) AS sec
                FROM game_sessions
                WHERE {base_where}
                GROUP BY day, game_name ORDER BY day, sec DESC
                """,
                params,
            )
            for r in rows:
                d = r["day"]
                if d not in main_by_day:
                    main_by_day[d] = {"sec": 0, "items": []}
                main_by_day[d]["sec"] += int(r["sec"] or 0)
                main_by_day[d]["items"].append(
                    {"name": r["game_name"], "sec": int(r["sec"] or 0)}
                )

        # Sub: foreground_sessions WHERE pc='sub'
        sub_by_day: dict[str, dict] = {}
        if pc in ("sub", "both"):
            sub_where = f"{base_where} AND pc = 'sub'"
            rows = await bot.database.fetchall(
                f"""
                SELECT date(start_at) AS day, process_name,
                       SUM(COALESCE(duration_sec, 0)) AS sec
                FROM foreground_sessions
                WHERE {sub_where}
                GROUP BY day, process_name ORDER BY day, sec DESC
                """,
                params,
            )
            for r in rows:
                d = r["day"]
                if d not in sub_by_day:
                    sub_by_day[d] = {"sec": 0, "items": []}
                sub_by_day[d]["sec"] += int(r["sec"] or 0)
                sub_by_day[d]["items"].append(
                    {"name": r["process_name"], "sec": int(r["sec"] or 0)}
                )

        all_days = sorted(set(main_by_day.keys()) | set(sub_by_day.keys()))
        daily: list[dict] = []
        for d in all_days:
            m = main_by_day.get(d)
            s = sub_by_day.get(d)
            entry: dict = {"day": d}
            main_sec = (m["sec"] if m else 0) if pc in ("main", "both") else 0
            sub_sec = (s["sec"] if s else 0) if pc in ("sub", "both") else 0
            if pc in ("main", "both"):
                entry["main_sec"] = main_sec
                entry["main_items"] = m["items"] if m else []
            if pc in ("sub", "both"):
                entry["sub_sec"] = sub_sec
                entry["sub_items"] = s["items"] if s else []
            # 後方互換: 既存の単独モード呼び出し用
            if pc == "main":
                entry["total_sec"] = main_sec
                entry["games"] = [
                    {"game_name": i["name"], "sec": i["sec"]} for i in entry["main_items"]
                ]
            elif pc == "sub":
                entry["total_sec"] = sub_sec
                entry["processes"] = [
                    {"process_name": i["name"], "sec": i["sec"]} for i in entry["sub_items"]
                ]
            else:
                entry["total_sec"] = main_sec + sub_sec
            daily.append(entry)
        return {**meta, "pc": pc, "daily": daily}

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

    # --- Image Gen (Phase 1) ---

    def _get_image_gen_unit():
        u = bot.unit_manager.get("image_gen")
        if not u:
            raise HTTPException(503, "image_gen unit not loaded")
        return u

    def _get_nas_mount_point() -> str:
        """NAS マウントポイントを config から取得。"""
        nas_cfg = bot.config.get("units", {}).get("image_gen", {}).get("nas", {}) or {}
        # prompt 指示は mount_point、実 config は base_path。両対応 + 既定値
        return nas_cfg.get("mount_point") or nas_cfg.get("base_path") or "/mnt/ai-image"

    _IMG_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

    @app.post("/api/image/generate", dependencies=[Depends(_verify)])
    async def image_generate(request: Request):
        unit = _get_image_gen_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        workflow_name = (body.get("workflow_name") or "").strip()
        if not workflow_name:
            raise HTTPException(400, "workflow_name is required")
        positive = body.get("positive")
        negative = body.get("negative")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params must be an object")
        try:
            job_id = await unit.enqueue(
                user_id=_webgui_user_id or "webgui",
                platform="web",
                workflow_name=workflow_name,
                positive=positive,
                negative=negative,
                params=params,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"job_id": job_id}

    @app.get("/api/image/jobs", dependencies=[Depends(_verify)])
    async def image_jobs_list(status: str | None = None, limit: int = 50, offset: int = 0):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        jobs = await unit.list_jobs(
            user_id=None,  # WebGUI はシングルユーザーなので全件。必要なら _webgui_user_id に絞る
            status=status,
            limit=limit,
            offset=offset,
        )
        return {"jobs": jobs}

    @app.get("/api/image/jobs/stream")
    async def image_jobs_stream():
        """ImageGenUnit のイベントを SSE で配信（/api/flow/stream と同じ形）。"""
        unit = _get_image_gen_unit()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        unit.subscribe_events(queue)

        async def event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                unit.unsubscribe_events(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/image/jobs/{job_id}", dependencies=[Depends(_verify)])
    async def image_job_detail(job_id: str):
        unit = _get_image_gen_unit()
        job = await unit.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/image/jobs/{job_id}/cancel", dependencies=[Depends(_verify)])
    async def image_job_cancel(job_id: str):
        unit = _get_image_gen_unit()
        ok = await unit.cancel_job(job_id)
        return {"ok": bool(ok)}

    @app.get("/api/image/gallery", dependencies=[Depends(_verify)])
    async def image_gallery(limit: int = 50, offset: int = 0):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        rows = await unit.list_gallery(limit=limit, offset=offset)
        items: list[dict] = []
        for r in rows:
            for p in r.get("result_paths") or []:
                items.append({
                    "job_id": r.get("job_id"),
                    "path": p,
                    "thumb_url": f"/api/image/file?path={p}",
                    "url": f"/api/image/file?path={p}",
                    "created_at": r.get("finished_at"),
                    "positive": r.get("positive"),
                })
        return {"items": items}

    @app.get("/api/image/workflows", dependencies=[Depends(_verify)])
    async def image_workflows():
        rows = await bot.database.workflow_list()
        out = []
        for r in rows:
            required_nodes = []
            required_loras = []
            try:
                required_nodes = json.loads(r.get("required_nodes") or "[]")
            except (TypeError, ValueError):
                pass
            try:
                required_loras = json.loads(r.get("required_loras") or "[]")
            except (TypeError, ValueError):
                pass
            out.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "description": r.get("description"),
                "category": r.get("category"),
                "main_pc_only": bool(r.get("main_pc_only")),
                "starred": bool(r.get("starred")),
                "default_timeout_sec": r.get("default_timeout_sec"),
                "required_nodes": required_nodes,
                "required_loras": required_loras,
            })
        return {"workflows": out}

    # --- /api/generation/* ( Phase 3.5c 並立 + セクション合成 ) ---

    @app.post("/api/generation/submit", dependencies=[Depends(_verify)])
    async def generation_submit(request: Request):
        unit = _get_image_gen_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        workflow_name = (body.get("workflow_name") or "").strip()
        if not workflow_name:
            raise HTTPException(400, "workflow_name is required")
        positive = body.get("positive")
        negative = body.get("negative")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params must be an object")
        section_ids = body.get("section_ids") or []
        if not isinstance(section_ids, list):
            raise HTTPException(400, "section_ids must be an array")
        try:
            section_ids = [int(v) for v in section_ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "section_ids must be integers")
        user_position = str(body.get("user_position") or "tail")
        modality = body.get("modality")
        lora_overrides = body.get("lora_overrides")
        if lora_overrides is not None and not isinstance(lora_overrides, list):
            raise HTTPException(400, "lora_overrides must be an array")
        try:
            job_id = await unit.enqueue(
                user_id=_webgui_user_id or "webgui",
                platform="web",
                workflow_name=workflow_name,
                positive=positive,
                negative=negative,
                params=params,
                section_ids=section_ids or None,
                user_position=user_position,
                modality=modality,
                lora_overrides=lora_overrides,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"job_id": job_id}

    @app.get("/api/generation/jobs", dependencies=[Depends(_verify)])
    async def generation_jobs_list(
        status: str | None = None, limit: int = 50, offset: int = 0,
        modality: str = "image",
    ):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        # unit.list_jobs は image 固定。他モダリティは将来 DB 直参照で対応。
        if modality != "image":
            rows = await bot.database.generation_job_list(
                status=status, modality=modality, limit=limit, offset=offset,
            )
            jobs = [await unit._row_to_dict(r) for r in rows]
        else:
            jobs = await unit.list_jobs(
                user_id=None, status=status, limit=limit, offset=offset,
            )
        return {"jobs": jobs}

    @app.get("/api/generation/jobs/stream")
    async def generation_jobs_stream():
        """ImageGenUnit イベントの SSE（/api/image/jobs/stream と同一ソース）。"""
        unit = _get_image_gen_unit()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        unit.subscribe_events(queue)

        async def event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                unit.unsubscribe_events(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/generation/jobs/{job_id}", dependencies=[Depends(_verify)])
    async def generation_job_detail(job_id: str):
        unit = _get_image_gen_unit()
        job = await unit.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/generation/jobs/{job_id}/cancel", dependencies=[Depends(_verify)])
    async def generation_job_cancel(job_id: str):
        unit = _get_image_gen_unit()
        ok = await unit.cancel_job(job_id)
        return {"ok": bool(ok)}

    @app.get("/api/generation/gallery", dependencies=[Depends(_verify)])
    async def generation_gallery(
        limit: int = 50, offset: int = 0,
        favorite: int = 0, tag: str | None = None,
    ):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        rows = await unit.list_gallery(
            limit=limit, offset=offset,
            favorite_only=bool(favorite), tag=(tag or None),
        )
        items: list[dict] = []
        for r in rows:
            paths = r.get("result_paths") or []
            kinds = r.get("result_kinds") or []
            if len(kinds) != len(paths):
                kinds = ["image"] * len(paths)
            for p, kind in zip(paths, kinds):
                items.append({
                    "job_id": r.get("job_id"),
                    "path": p,
                    "kind": kind,
                    "thumb_url": f"/api/image/file?path={p}",
                    "url": f"/api/image/file?path={p}",
                    "created_at": r.get("finished_at"),
                    "positive": r.get("positive"),
                    "negative": r.get("negative"),
                    "favorite": r.get("favorite", False),
                    "tags": r.get("tags") or [],
                })
        return {"items": items}

    @app.patch(
        "/api/generation/jobs/{job_id}/favorite",
        dependencies=[Depends(_verify)],
    )
    async def generation_job_favorite(job_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        favorite = bool(body.get("favorite"))
        ok = await bot.database.generation_job_set_favorite(job_id, favorite)
        if not ok:
            raise HTTPException(404, "job not found")
        return {"ok": True, "favorite": favorite}

    @app.patch(
        "/api/generation/jobs/{job_id}/tags",
        dependencies=[Depends(_verify)],
    )
    async def generation_job_tags(job_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        tags = body.get("tags") or []
        if not isinstance(tags, list):
            raise HTTPException(400, "tags must be an array")
        # 文字列に正規化、空除去、重複除去（順序保持）
        seen = set()
        cleaned: list[str] = []
        for t in tags:
            s = str(t).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        tags_json = json.dumps(cleaned, ensure_ascii=False) if cleaned else None
        ok = await bot.database.generation_job_set_tags(job_id, tags_json)
        if not ok:
            raise HTTPException(404, "job not found")
        return {"ok": True, "tags": cleaned}

    @app.get(
        "/api/generation/workflows/{name}/loras",
        dependencies=[Depends(_verify)],
    )
    async def generation_workflow_loras(name: str):
        """workflow に含まれる LoraLoader ノード一覧（UI のセレクタ用）。"""
        unit = _get_image_gen_unit()
        try:
            loras = await unit.workflow_mgr.list_lora_nodes_by_name(name)
        except Exception as e:
            raise HTTPException(500, f"failed to load loras: {e}")
        return {"loras": loras}

    @app.get("/api/generation/gallery/tags", dependencies=[Depends(_verify)])
    async def generation_gallery_tags():
        """ギャラリーで使われているタグ一覧（出現回数つき）。"""
        rows = await bot.database.fetchall(
            "SELECT tags FROM generation_jobs "
            "WHERE modality = 'image' AND status = 'done' "
            "  AND tags IS NOT NULL AND tags != ''"
        )
        counts: dict[str, int] = {}
        for r in rows:
            try:
                tags = json.loads(r.get("tags") or "[]") or []
            except Exception:
                continue
            for t in tags:
                s = str(t).strip()
                if not s:
                    continue
                counts[s] = counts.get(s, 0) + 1
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return {"tags": [{"tag": k, "count": v} for k, v in items]}

    # --- section categories ---

    @app.get("/api/generation/section-categories", dependencies=[Depends(_verify)])
    async def section_categories_list():
        rows = await bot.database.section_category_list()
        return {"categories": [
            {
                "key": r["key"],
                "label": r["label"],
                "description": r.get("description"),
                "display_order": int(r.get("display_order") or 500),
                "is_builtin": bool(r.get("is_builtin")),
            }
            for r in rows
        ]}

    @app.post("/api/generation/section-categories", dependencies=[Depends(_verify)])
    async def section_category_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        key = (body.get("key") or "").strip()
        label = (body.get("label") or "").strip()
        if not key or not label:
            raise HTTPException(400, "key and label are required")
        if await bot.database.section_category_get(key):
            raise HTTPException(409, "category key already exists")
        cid = await bot.database.section_category_insert(
            key=key, label=label,
            description=body.get("description"),
            display_order=int(body.get("display_order") or 500),
        )
        return {"id": cid, "key": key}

    @app.patch(
        "/api/generation/section-categories/{key}",
        dependencies=[Depends(_verify)],
    )
    async def section_category_update(key: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        ok = await bot.database.section_category_update(
            key,
            label=body.get("label"),
            description=body.get("description"),
            display_order=body.get("display_order"),
        )
        if not ok:
            raise HTTPException(404, "category not found or no changes")
        return {"ok": True}

    @app.delete(
        "/api/generation/section-categories/{key}",
        dependencies=[Depends(_verify)],
    )
    async def section_category_delete(key: str):
        row = await bot.database.section_category_get(key)
        if not row:
            raise HTTPException(404, "category not found")
        if row.get("is_builtin"):
            raise HTTPException(400, "builtin category cannot be deleted")
        ok = await bot.database.section_category_delete(key)
        return {"ok": bool(ok)}

    # --- sections (prompt fragments) ---

    def _section_to_dict(r: dict) -> dict:
        return {
            "id": int(r["id"]),
            "category_key": r.get("category_key"),
            "name": r.get("name"),
            "description": r.get("description"),
            "positive": r.get("positive"),
            "negative": r.get("negative"),
            "tags": r.get("tags"),
            "is_builtin": bool(r.get("is_builtin")),
            "starred": bool(r.get("starred")),
            "updated_at": r.get("updated_at"),
        }

    @app.get("/api/generation/sections", dependencies=[Depends(_verify)])
    async def sections_list(
        category_key: str | None = None, starred_only: bool = False,
    ):
        rows = await bot.database.section_list(
            category_key=category_key, starred_only=bool(starred_only),
        )
        return {"sections": [_section_to_dict(r) for r in rows]}

    @app.get("/api/generation/sections/{section_id}", dependencies=[Depends(_verify)])
    async def section_detail(section_id: int):
        r = await bot.database.section_get(int(section_id))
        if not r:
            raise HTTPException(404, "section not found")
        return _section_to_dict(r)

    @app.post("/api/generation/sections", dependencies=[Depends(_verify)])
    async def section_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        category_key = (body.get("category_key") or "").strip()
        name = (body.get("name") or "").strip()
        if not category_key or not name:
            raise HTTPException(400, "category_key and name are required")
        if not await bot.database.section_category_get(category_key):
            raise HTTPException(400, "unknown category_key")
        sid = await bot.database.section_insert(
            category_key=category_key, name=name,
            positive=body.get("positive"), negative=body.get("negative"),
            description=body.get("description"), tags=body.get("tags"),
            starred=int(bool(body.get("starred"))),
        )
        return {"id": sid}

    @app.patch("/api/generation/sections/{section_id}", dependencies=[Depends(_verify)])
    async def section_update(section_id: int, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        fields: dict = {}
        for k in ("name", "description", "positive", "negative", "tags", "category_key"):
            if k in body:
                fields[k] = body[k]
        if "starred" in body:
            fields["starred"] = int(bool(body["starred"]))
        if not fields:
            raise HTTPException(400, "no fields to update")
        ok = await bot.database.section_update(int(section_id), **fields)
        if not ok:
            raise HTTPException(404, "section not found")
        return {"ok": True}

    @app.delete("/api/generation/sections/{section_id}", dependencies=[Depends(_verify)])
    async def section_delete(section_id: int):
        row = await bot.database.section_get(int(section_id))
        if not row:
            raise HTTPException(404, "section not found")
        if row.get("is_builtin"):
            raise HTTPException(400, "builtin section cannot be deleted")
        ok = await bot.database.section_delete(int(section_id))
        return {"ok": bool(ok)}

    # --- section presets (selected sections + user prompts snapshot) ---

    def _section_preset_to_dict(r: dict) -> dict:
        try:
            payload = json.loads(r.get("payload_json") or "{}")
        except Exception:
            payload = {}
        return {
            "id": int(r["id"]),
            "name": r.get("name"),
            "description": r.get("description"),
            "payload": payload,
            "updated_at": r.get("updated_at"),
        }

    def _validate_section_preset_payload(payload) -> dict:
        if not isinstance(payload, dict):
            raise HTTPException(400, "payload must be an object")
        sids = payload.get("section_ids") or []
        if not isinstance(sids, list):
            raise HTTPException(400, "section_ids must be an array")
        try:
            sids = [int(v) for v in sids]
        except (TypeError, ValueError):
            raise HTTPException(400, "section_ids must be integers")
        pos = str(payload.get("user_position") or "tail")
        return {
            "section_ids": sids,
            "user_positive": str(payload.get("user_positive") or ""),
            "user_negative": str(payload.get("user_negative") or ""),
            "user_position": pos,
        }

    @app.get("/api/generation/section-presets", dependencies=[Depends(_verify)])
    async def section_presets_list():
        rows = await bot.database.section_preset_list()
        return {"presets": [_section_preset_to_dict(r) for r in rows]}

    @app.post("/api/generation/section-presets", dependencies=[Depends(_verify)])
    async def section_preset_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        name = (body.get("name") or "").strip()
        if not name or len(name) > 64:
            raise HTTPException(400, "name is required (1-64 chars)")
        if await bot.database.section_preset_get_by_name(name):
            raise HTTPException(409, "name already exists")
        normalized = _validate_section_preset_payload(body.get("payload"))
        pid = await bot.database.section_preset_insert(
            name=name,
            description=(body.get("description") or None),
            payload_json=json.dumps(normalized, ensure_ascii=False),
        )
        return {"id": pid}

    @app.patch(
        "/api/generation/section-presets/{preset_id}",
        dependencies=[Depends(_verify)],
    )
    async def section_preset_update(preset_id: int, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        existing = await bot.database.section_preset_get(int(preset_id))
        if not existing:
            raise HTTPException(404, "preset not found")
        kwargs: dict = {}
        if "name" in body:
            new_name = (body.get("name") or "").strip()
            if not new_name or len(new_name) > 64:
                raise HTTPException(400, "name must be 1-64 chars")
            if new_name != existing["name"]:
                if await bot.database.section_preset_get_by_name(new_name):
                    raise HTTPException(409, "name already exists")
            kwargs["name"] = new_name
        if "description" in body:
            kwargs["description"] = body.get("description") or None
        if "payload" in body:
            normalized = _validate_section_preset_payload(body.get("payload"))
            kwargs["payload_json"] = json.dumps(normalized, ensure_ascii=False)
        if not kwargs:
            raise HTTPException(400, "no fields to update")
        ok = await bot.database.section_preset_update(int(preset_id), **kwargs)
        if not ok:
            raise HTTPException(500, "update failed")
        return {"ok": True}

    @app.delete(
        "/api/generation/section-presets/{preset_id}",
        dependencies=[Depends(_verify)],
    )
    async def section_preset_delete(preset_id: int):
        ok = await bot.database.section_preset_delete(int(preset_id))
        if not ok:
            raise HTTPException(404, "preset not found")
        return {"ok": True}

    @app.post("/api/generation/compose-preview", dependencies=[Depends(_verify)])
    async def section_compose_preview(request: Request):
        """クライアントのプレビューと同じロジックをサーバで走らせる検証用。"""
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        section_ids = body.get("section_ids") or []
        if not isinstance(section_ids, list):
            raise HTTPException(400, "section_ids must be an array")
        try:
            section_ids = [int(v) for v in section_ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "section_ids must be integers")
        user_positive = body.get("positive")
        user_negative = body.get("negative")
        user_position = str(body.get("user_position") or "tail")
        rows = await bot.database.section_get_many(section_ids)
        from src.units.image_gen.section_composer import compose_prompt
        result = compose_prompt(
            rows,
            user_positive=user_positive,
            user_negative=user_negative,
            user_position=user_position,
        )
        return {
            "positive": result.positive,
            "negative": result.negative,
            "warnings": list(result.warnings),
            "dropped": list(result.dropped),
        }

    # --- prompt_crafter セッション API ---

    def _get_prompt_crafter_unit():
        u = bot.unit_manager.get("prompt_crafter")
        if not u:
            raise HTTPException(503, "prompt_crafter unit not loaded")
        return u

    def _prompt_session_to_dict(row: dict) -> dict:
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "platform": row.get("platform"),
            "positive": row.get("positive") or "",
            "negative": row.get("negative") or "",
            "base_workflow_id": row.get("base_workflow_id"),
            "updated_at": row.get("updated_at"),
            "expires_at": row.get("expires_at"),
        }

    @app.get("/api/image/prompts", dependencies=[Depends(_verify)])
    async def prompts_list(limit: int = 20):
        limit = max(1, min(100, int(limit)))
        user_id = _webgui_user_id or "webgui"
        rows = await bot.database.prompt_session_list(user_id=user_id, limit=limit)
        return {"sessions": [_prompt_session_to_dict(r) for r in rows]}

    @app.get("/api/image/prompts/active", dependencies=[Depends(_verify)])
    async def prompts_active():
        user_id = _webgui_user_id or "webgui"
        unit = _get_prompt_crafter_unit()
        sess = await unit.get_active_prompt(user_id, "web")
        return {"session": sess}

    @app.post("/api/image/prompts/craft", dependencies=[Depends(_verify)])
    async def prompts_craft(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        instruction = (body.get("instruction") or "").strip()
        if not instruction:
            raise HTTPException(400, "instruction is required")
        base_workflow_id = body.get("base_workflow_id")
        if base_workflow_id is not None:
            try:
                base_workflow_id = int(base_workflow_id)
            except (TypeError, ValueError):
                raise HTTPException(400, "base_workflow_id must be integer")
        user_id = _webgui_user_id or "webgui"
        unit = _get_prompt_crafter_unit()
        try:
            result = await unit.craft(
                user_id=user_id, platform="web",
                instruction=instruction,
                base_workflow_id=base_workflow_id,
            )
        except Exception as e:
            raise HTTPException(500, f"craft failed: {e}")
        return result

    @app.delete("/api/image/prompts/active", dependencies=[Depends(_verify)])
    async def prompts_clear_active():
        user_id = _webgui_user_id or "webgui"
        unit = _get_prompt_crafter_unit()
        ok = await unit.clear_active(user_id, "web")
        return {"ok": bool(ok)}

    @app.delete("/api/image/prompts/{session_id}", dependencies=[Depends(_verify)])
    async def prompts_delete(session_id: int):
        await bot.database.prompt_session_delete(int(session_id))
        return {"ok": True}

    @app.get("/api/image/agents", dependencies=[Depends(_verify)])
    async def image_agents():
        """ComfyUI へのリンク用に agent_pool の host 情報を返す。"""
        ig_cfg = (bot.config.get("units") or {}).get("image_gen") or {}
        comfy_port = int(ig_cfg.get("comfyui_port", 8188))
        agents = getattr(bot.unit_manager.agent_pool, "_agents", []) or []
        out = []
        for a in agents:
            host = a.get("host") or ""
            if not host:
                continue
            public_url = (a.get("comfyui_public_url") or "").strip()
            url = public_url if public_url else f"http://{host}:{comfy_port}/"
            out.append({
                "id": a.get("id", ""),
                "name": a.get("name") or a.get("id", ""),
                "role": a.get("role", ""),
                "comfyui_url": url,
            })
        return {"agents": out}

    def _find_agent(agent_id: str) -> dict | None:
        for a in (getattr(bot.unit_manager.agent_pool, "_agents", []) or []):
            if a.get("id") == agent_id:
                return a
        return None

    async def _comfyui_proxy(agent_id: str, method: str, path: str, timeout: float):
        agent = _find_agent(agent_id)
        if not agent:
            raise HTTPException(404, f"agent not found: {agent_id}")
        url = f"http://{agent['host']}:{agent.get('port', 7777)}{path}"
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, headers=headers)
            return JSONResponse(
                content=resp.json() if resp.content else {},
                status_code=resp.status_code,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"agent unreachable: {e}")

    @app.get("/api/image/agents/{agent_id}/comfyui/status", dependencies=[Depends(_verify)])
    async def image_comfyui_status(agent_id: str):
        return await _comfyui_proxy(agent_id, "GET", "/comfyui/status", timeout=5.0)

    @app.post("/api/image/agents/{agent_id}/comfyui/start", dependencies=[Depends(_verify)])
    async def image_comfyui_start(agent_id: str):
        return await _comfyui_proxy(agent_id, "POST", "/comfyui/start", timeout=120.0)

    @app.post("/api/image/agents/{agent_id}/comfyui/stop", dependencies=[Depends(_verify)])
    async def image_comfyui_stop(agent_id: str):
        return await _comfyui_proxy(agent_id, "POST", "/comfyui/stop", timeout=30.0)

    @app.get("/api/image/agents/{agent_id}/comfyui/history", dependencies=[Depends(_verify)])
    async def image_comfyui_history(agent_id: str, limit: int = 20):
        limit = max(1, min(100, int(limit)))
        return await _comfyui_proxy(
            agent_id, "GET", f"/comfyui/history?limit={limit}", timeout=15.0,
        )

    # --- Workflows CRUD (プリセット管理) ---

    _WORKFLOW_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

    def _workflow_row_to_dict(row: dict, include_json: bool = False) -> dict:
        out = {
            "id": row.get("id"),
            "name": row.get("name"),
            "description": row.get("description") or "",
            "category": row.get("category") or "",
            "main_pc_only": bool(row.get("main_pc_only")),
            "starred": bool(row.get("starred")),
            "default_timeout_sec": int(row.get("default_timeout_sec") or 300),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for k in ("required_nodes", "required_models", "required_loras"):
            raw = row.get(k) or "[]"
            try:
                out[k] = json.loads(raw)
            except Exception:
                out[k] = []
        if include_json:
            try:
                out["workflow_json"] = json.loads(row.get("workflow_json") or "{}")
            except Exception:
                out["workflow_json"] = {}
        return out

    @app.get("/api/image/workflows", dependencies=[Depends(_verify)])
    async def image_workflows_list(category: str | None = None):
        rows = await bot.database.workflow_list(category=category)
        return {"workflows": [_workflow_row_to_dict(r) for r in rows]}

    @app.get("/api/image/workflows/{workflow_id}", dependencies=[Depends(_verify)])
    async def image_workflows_get(workflow_id: int):
        row = await bot.database.workflow_get(int(workflow_id))
        if not row:
            raise HTTPException(404, "workflow not found")
        return _workflow_row_to_dict(row, include_json=True)

    @app.post("/api/image/workflows", dependencies=[Depends(_verify)])
    async def image_workflows_upsert(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        name = (body.get("name") or "").strip()
        if not _WORKFLOW_NAME_RE.match(name):
            raise HTTPException(400, "name must match [a-zA-Z0-9_-]{1,64}")
        workflow_json = body.get("workflow_json")
        if not isinstance(workflow_json, dict) or not workflow_json:
            raise HTTPException(400, "workflow_json must be a non-empty object")
        unit = _get_image_gen_unit()
        try:
            wid = await unit.workflow_mgr.register_workflow(
                name=name,
                workflow_json=workflow_json,
                description=(body.get("description") or "") or None,
                category=(body.get("category") or "t2i"),
                main_pc_only=bool(body.get("main_pc_only", False)),
                default_timeout_sec=int(body.get("default_timeout_sec") or 300),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"register failed: {e}")
        return {"ok": True, "id": wid, "name": name}

    @app.delete("/api/image/workflows/{workflow_id}", dependencies=[Depends(_verify)])
    async def image_workflows_delete(workflow_id: int):
        row = await bot.database.workflow_get(int(workflow_id))
        if not row:
            raise HTTPException(404, "workflow not found")
        await bot.database.execute(
            "DELETE FROM workflows WHERE id = ?", (int(workflow_id),),
        )
        try:
            _get_image_gen_unit().workflow_mgr.invalidate_cache(row.get("name"))
        except Exception:
            pass
        return {"ok": True}

    @app.get("/api/image/file", dependencies=[Depends(_verify)])
    async def image_file(path: str):
        """NAS 配下の画像ファイルを配信（path traversal ガード付き）。"""
        from pathlib import Path
        from fastapi.responses import FileResponse

        if not path:
            raise HTTPException(400, "path is required")

        mount_point = _get_nas_mount_point()
        try:
            mount_real = Path(mount_point).resolve()
        except Exception:
            raise HTTPException(500, "invalid nas mount_point")

        # 入力パス解釈: 絶対パス（mount_point 配下）または mount_point 相対
        raw = Path(path)
        target = raw if raw.is_absolute() else (mount_real / raw)
        try:
            real = target.resolve(strict=False)
        except Exception:
            raise HTTPException(400, "invalid path")

        # mount_point 配下チェック
        try:
            real.relative_to(mount_real)
        except ValueError:
            raise HTTPException(403, "path outside nas mount")

        # outputs/ 配下のみ許可
        outputs_subdir = (
            bot.config.get("units", {}).get("image_gen", {})
            .get("nas", {}).get("outputs_subdir", "outputs")
        )
        outputs_real = (mount_real / outputs_subdir).resolve()
        try:
            real.relative_to(outputs_real)
        except ValueError:
            raise HTTPException(403, "only outputs/ is allowed")

        if not real.is_file():
            raise HTTPException(404, "file not found")

        ext = real.suffix.lower()
        if ext not in _IMG_ALLOWED_EXTS:
            raise HTTPException(415, f"unsupported extension: {ext}")

        media_map = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp",
        }
        return FileResponse(
            str(real), media_type=media_map.get(ext, "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=300"},
        )

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
            return HTMLResponse(content=html, headers=NO_CACHE_HEADERS)
        except FileNotFoundError:
            return HTMLResponse(
                content="<h1>Secretary Bot WebGUI</h1><p>static/index.html not found</p>",
                headers=NO_CACHE_HEADERS,
            )

    return app
