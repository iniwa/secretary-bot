"""コア API: /health, /api/chat, /api/logs, /api/status, /api/version。"""

from __future__ import annotations

import asyncio
import os
import subprocess

from fastapi import FastAPI, HTTPException, Request

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.web._context import WebContext

log = get_logger(__name__)


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    @app.get("/health")
    async def health():
        from src.bot import get_commit_hash, get_uptime_seconds
        return {
            "status": "ok",
            "version": get_commit_hash(),
            "uptime": int(get_uptime_seconds()),
        }

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
            async with ctx.webgui_lock:
                await ft.emit("LOCK", "done", {"channel": "webgui"}, flow_id)
                try:
                    await bot.database.log_conversation("webgui", "user", message, user_id=ctx.webgui_user_id)

                    history_minutes = bot.config.get("units", {}).get("chat", {}).get("history_minutes", 60)
                    recent_rows = await bot.database.get_recent_channel_messages(
                        "webgui", limit=6, user_id=ctx.webgui_user_id,
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
                        result = await bot.unit_router.route(message, channel="webgui", user_id=ctx.webgui_user_id, flow_id=flow_id, conversation_context=conversation_context)
                        unit_name = result.get("unit", "chat")
                        user_message = result.get("message", message)

                    unit = bot.unit_manager.get(unit_name)
                    if unit is None:
                        unit = bot.unit_manager.get("chat")

                    actual_unit = getattr(unit, "unit", unit)
                    actual_unit.session_done = False
                    response = await unit.execute(None, {"message": user_message, "channel": "webgui", "user_id": ctx.webgui_user_id, "flow_id": flow_id, "conversation_context": conversation_context})
                    if actual_unit.session_done:
                        bot.unit_router.clear_session("webgui", ctx.webgui_user_id)
                        actual_unit.clear_exchange("webgui")
                        await ft.emit("SESSION_UPDATE", "done", {"action": "cleared"}, flow_id)
                    elif response:
                        actual_unit.save_exchange("webgui", user_message, response)
                        bot.unit_router.refresh_session("webgui", ctx.webgui_user_id)
                        await ft.emit("SESSION_UPDATE", "done", {"action": "saved"}, flow_id)
                    if response:
                        mode = "eco" if not bot.llm_router.ollama_available else "normal"
                        await bot.database.log_conversation("webgui", "assistant", response, mode=mode, unit=unit_name, user_id=ctx.webgui_user_id)
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
            ts = ctx.agent_restart_ts.get(aid)
            if ts is None:
                continue
            elapsed = now - ts
            if agent.get("alive") or elapsed >= ctx.restart_window_sec:
                stale_ids.append(aid)
            else:
                agent["status"] = "restarting"
                agent["restart_elapsed"] = int(elapsed)
        for aid in stale_ids:
            ctx.agent_restart_ts.pop(aid, None)
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
