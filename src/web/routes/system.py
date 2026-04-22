"""システム管理 API: ollama / delegation-mode / update-code / restart / agents/*。"""

from __future__ import annotations

import os
import subprocess

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request

from src.logger import get_logger
from src.web._agent_helpers import (
    agent_request,
    delayed_restart,
    find_agent_by_id,
    get_all_agent_versions,
    post_all_agents,
    update_all_agents,
)
from src.web._context import WebContext

log = get_logger(__name__)


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

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

    @app.post("/api/agents/{agent_id}/pause", dependencies=[Depends(ctx.verify)])
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

    @app.delete("/api/agents/{agent_id}/pause", dependencies=[Depends(ctx.verify)])
    async def unpause_agent(agent_id: str):
        """エージェントの一時停止を解除する。"""
        pool = bot.unit_manager.agent_pool
        pool.unpause_agent(agent_id)
        log.info("Agent %s unpaused via WebGUI", agent_id)
        return {"ok": True}

    @app.post("/api/update-code", )
    async def update_code(background_tasks: BackgroundTasks):
        # 2重実行防止（ダブルクリック・中継再送対策）
        if ctx.update_lock.locked():
            return {
                "updated": False,
                "message": "別の更新処理が実行中です",
                "restarted": False,
                "restart_detail": "重複実行を防止しました",
            }
        async with ctx.update_lock:
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
                    agent_versions = await get_all_agent_versions(bot)
                    stale_agents = [
                        a for a in agent_versions
                        if a.get("alive") and a.get("version_full") and a["version_full"] != remote_hash
                    ]
                    if stale_agents:
                        log.info("Pi up-to-date but %d agent(s) stale, triggering update", len(stale_agents))
                        agent_update_results = await update_all_agents(bot)
                        agent_restart_results = await post_all_agents(bot, "/restart-self", timeout=5)
                        ctx.mark_agents_restarting_bulk(agent_restart_results)
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
                        background_tasks.add_task(delayed_restart, 2)
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
                agent_update_results = await update_all_agents(bot)
                # Agent プロセスを自己再起動させてコードを反映（start_agent.bat のループが再起動担当）
                agent_restart_results = await post_all_agents(bot, "/restart-self", timeout=5)
                ctx.mark_agents_restarting_bulk(agent_restart_results)

                # レスポンス送信後に再起動（遅延付き）
                background_tasks.add_task(delayed_restart, 2)
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
        background_tasks.add_task(delayed_restart, 2)
        return {"restarted": True, "detail": "まもなく再起動します…"}

    @app.post("/api/agents/restart-all", )
    async def restart_all_agents_endpoint():
        """全 Windows Agent プロセスを手動で再起動する（コード更新なし）。
        start_agent.bat のループが自動再起動を担当する前提。"""
        if ctx.update_lock.locked():
            return {
                "success": False,
                "message": "別の更新処理が実行中です",
                "agents": [],
            }
        async with ctx.update_lock:
            results = await post_all_agents(bot, "/restart-self", timeout=5)
            ctx.mark_agents_restarting_bulk(results)
            ok_count = sum(1 for r in results if r.get("success"))
            return {
                "success": True,
                "message": f"{ok_count} / {len(results)} 件の Agent に再起動を要求しました",
                "agents": results,
            }

    @app.post("/api/agents/{agent_id}/restart", )
    async def restart_agent_one(agent_id: str):
        """個別 Agent を再起動する。"""
        if ctx.update_lock.locked():
            return {"success": False, "message": "別の更新処理が実行中です"}
        async with ctx.update_lock:
            agent = find_agent_by_id(bot, agent_id)
            if not agent:
                raise HTTPException(404, f"Agent '{agent_id}' not found")
            token = os.environ.get("AGENT_SECRET_TOKEN", "")
            headers = {"X-Agent-Token": token} if token else {}
            url = f"http://{agent['host']}:{agent['port']}/restart-self"
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(url, headers=headers)
                    data = resp.json() if resp.content else {}
                ctx.mark_agent_restarting(agent_id)
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

        agents = await get_all_agent_versions(bot)

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

    @app.get("/api/gpu-status/logs")
    async def gpu_status_logs(lines: int = 200):
        """全 Windows Agent の GPU 診断ログ（nvidia-smi / ollama ps）を並列取得。"""
        results = await agent_request(bot, "GET", f"/gpu/logs?lines={lines}")
        return {"agents": results}
