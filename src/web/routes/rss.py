"""RSS フィード管理 API: /api/rss/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    @app.get("/api/rss/feeds", dependencies=[Depends(ctx.verify)])
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
        if ctx.webgui_user_id:
            prefs = await bot.database.fetchall(
                "SELECT feed_id, category, enabled FROM rss_user_prefs "
                "WHERE user_id = ? AND enabled = 0",
                (ctx.webgui_user_id,),
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

    @app.post("/api/rss/feeds", dependencies=[Depends(ctx.verify)])
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

    @app.delete("/api/rss/feeds/{feed_id}", dependencies=[Depends(ctx.verify)])
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

    @app.get("/api/rss/articles", dependencies=[Depends(ctx.verify)])
    async def rss_articles(
        category: str | None = None, limit: int = 50, offset: int = 0
    ):
        uid = ctx.webgui_user_id or ""
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

    @app.post("/api/rss/fetch", dependencies=[Depends(ctx.verify)])
    async def rss_fetch_now():
        """手動で全フィードをフェッチ。"""
        from src.rss.fetcher import RSSFetcher
        fetcher = RSSFetcher(bot)
        result = await fetcher.fetch_all_feeds()
        return result

    @app.post("/api/rss/articles/{article_id}/feedback", dependencies=[Depends(ctx.verify)])
    async def rss_article_feedback(article_id: int, request: Request):
        """記事の 👍 / 👎 フィードバックを記録（rating=0 は取り消し）。"""
        if not ctx.webgui_user_id:
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
                (ctx.webgui_user_id, article_id),
            )
        else:
            from src.database import jst_now
            await bot.database.execute(
                """INSERT OR REPLACE INTO rss_feedback
                   (user_id, article_id, rating, created_at)
                   VALUES (?, ?, ?, ?)""",
                (ctx.webgui_user_id, article_id, rating, jst_now()),
            )
        return {"ok": True, "rating": rating}

    @app.post("/api/rss/feeds/{feed_id}/toggle", dependencies=[Depends(ctx.verify)])
    async def rss_feed_toggle(feed_id: int, request: Request):
        """フィードのユーザー単位有効/無効切り替え。"""
        if not ctx.webgui_user_id:
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
                (ctx.webgui_user_id, feed_id),
            )
        else:
            await bot.database.execute(
                """INSERT OR REPLACE INTO rss_user_prefs
                   (user_id, feed_id, enabled) VALUES (?, ?, 0)""",
                (ctx.webgui_user_id, feed_id),
            )
        return {"ok": True, "enabled": enabled}
