"""Tools: input-relay API (/api/tools/input-relay/*)。"""

from __future__ import annotations

import os
import subprocess

from fastapi import FastAPI, HTTPException

from src.logger import get_logger
from src.web._agent_helpers import agent_request, post_all_agents, update_all_agents
from src.web._context import WebContext

log = get_logger(__name__)


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    @app.post("/api/tools/input-relay/update", )
    async def tools_input_relay_update():
        """input-relayサブモジュールをGitHubから最新取得 → メインリポに commit & push。
        その後、全Windows Agentに /update を通知して git pull で反映させる。"""
        # 2重実行防止
        if ctx.update_lock.locked():
            return {
                "updated": False,
                "message": "別の更新処理が実行中です",
                "agents": [],
            }
        async with ctx.update_lock:
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
                agent_results = await update_all_agents(bot)
                # ⑥ input-relay ツールを再起動させて新ファイルをロード（agent.py 本体は再起動不要）
                agent_tool_restart = await post_all_agents(
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
        results = await agent_request(bot, "GET", "/tools/input-relay/status")
        return {"agents": results}

    @app.get("/api/tools/input-relay/logs/{role}", )
    async def tools_input_relay_logs(role: str, lines: int = 100):
        results = await agent_request(bot, "GET", f"/tools/input-relay/logs?lines={lines}", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    @app.post("/api/tools/input-relay/start/{role}", )
    async def tools_input_relay_start(role: str):
        results = await agent_request(bot, "POST", "/tools/input-relay/start", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    @app.post("/api/tools/input-relay/stop/{role}", )
    async def tools_input_relay_stop(role: str):
        results = await agent_request(bot, "POST", "/tools/input-relay/stop", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]

    @app.post("/api/tools/input-relay/restart/{role}", )
    async def tools_input_relay_restart(role: str):
        results = await agent_request(bot, "POST", "/tools/input-relay/restart", role=role)
        if not results:
            raise HTTPException(404, f"No agent with role '{role}'")
        return results[0]
