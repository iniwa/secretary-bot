"""OBS 関連 API: /api/obs/*。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from src.web._agent_helpers import agent_request, agent_request_json
from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    # --- OBS: ゲームプロセス管理 ---
    # NOTE: ゲームは Main PC で実行されるため game_processes.json は Main PC が
    # マスター。Sub PC の OBS Manager は /activity 経由で Main PC に問い合わせる。
    # そのため /api/obs/games の編集は Main PC に向ける。

    @app.get("/api/obs/games", )
    async def obs_games():
        results = await agent_request(bot, "GET", "/obs/games", role="main")
        if not results:
            return {"games": [], "groups": []}
        return results[0]

    @app.post("/api/obs/games", )
    async def obs_games_save(request: Request):
        body = await request.json()
        results = await agent_request_json(bot, "POST", "/obs/games", role="main", json_body=body)
        if not results:
            raise HTTPException(502, "Main PC agent unreachable")
        return results[0]

    # OBS 本体（WebSocket / ファイル整理 / ログ）は Sub PC 上で動作
    @app.get("/api/obs/status", )
    async def obs_status():
        results = await agent_request(bot, "GET", "/obs/status", role="sub")
        if not results:
            return {"obs_connected": False}
        return results[0]

    @app.get("/api/obs/logs", )
    async def obs_logs(lines: int = 100):
        results = await agent_request(bot, "GET", f"/obs/logs?lines={lines}", role="sub")
        if not results:
            return {"logs": []}
        return results[0]
