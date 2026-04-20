"""Docker ログ監視 API: /api/docker-monitor/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    @app.get("/api/docker-monitor/errors", dependencies=[Depends(ctx.verify)])
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

    @app.post("/api/docker-monitor/errors/{error_id}/dismiss", dependencies=[Depends(ctx.verify)])
    async def dismiss_docker_error(error_id: int):
        await bot.database.execute(
            "UPDATE docker_error_log SET dismissed = 1 WHERE id = ?", (error_id,)
        )
        return {"ok": True}

    @app.post("/api/docker-monitor/errors/dismiss-all", dependencies=[Depends(ctx.verify)])
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

    @app.delete("/api/docker-monitor/errors/{error_id}", dependencies=[Depends(ctx.verify)])
    async def delete_docker_error(error_id: int):
        await bot.database.execute("DELETE FROM docker_error_log WHERE id = ?", (error_id,))
        return {"ok": True}

    @app.get("/api/docker-monitor/exclusions", dependencies=[Depends(ctx.verify)])
    async def get_docker_exclusions():
        rows = await bot.database.fetchall(
            "SELECT * FROM docker_log_exclusions ORDER BY created_at DESC"
        )
        return {"items": rows}

    @app.post("/api/docker-monitor/exclusions", dependencies=[Depends(ctx.verify)])
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

    @app.delete("/api/docker-monitor/exclusions/{exc_id}", dependencies=[Depends(ctx.verify)])
    async def delete_docker_exclusion(exc_id: int):
        await bot.database.execute("DELETE FROM docker_log_exclusions WHERE id = ?", (exc_id,))
        return {"ok": True}

    # /api/docker-monitor/settings は廃止（エラー通知は常時有効化されたため）
