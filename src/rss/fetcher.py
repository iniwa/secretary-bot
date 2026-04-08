"""RSSフェッチャー — feedparserで全フィードを巡回し新規記事をDBに保存。"""

import asyncio
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from src.database import JST, jst_now
from src.logger import get_logger

log = get_logger(__name__)

_HTTP_TIMEOUT = 15


class RSSFetcher:
    def __init__(self, bot):
        self.bot = bot

    async def fetch_all_feeds(self) -> dict:
        """全フィードを巡回し新規記事をINSERT。古い記事をパージ。"""
        await self.ensure_preset_feeds()
        feeds = await self.bot.database.fetchall("SELECT * FROM rss_feeds")
        if not feeds:
            return {"fetched": 0, "new_articles": 0}

        total_new = 0
        for feed in feeds:
            try:
                count = await self._fetch_feed(feed)
                total_new += count
            except Exception:
                log.warning("RSS fetch failed for %s", feed["url"], exc_info=True)

        # 古い記事をパージ
        retention = self.bot.config.get("rss", {}).get("article_retention_days", 30)
        await self._purge_old_articles(retention)

        log.info("RSS fetch complete: %d feeds, %d new articles", len(feeds), total_new)
        return {"fetched": len(feeds), "new_articles": total_new}

    async def _fetch_feed(self, feed: dict) -> int:
        """単一フィードを取得し新規記事をINSERT。新規件数を返す。"""
        url = feed["url"]
        feed_id = feed["id"]

        raw = await self._download_feed(url)
        if not raw:
            return 0

        parsed = await asyncio.get_event_loop().run_in_executor(
            None, feedparser.parse, raw,
        )

        new_count = 0
        for entry in parsed.entries:
            article_url = getattr(entry, "link", "")
            if not article_url:
                continue
            title = getattr(entry, "title", "")[:500]
            published_at = self._parse_date(entry)

            inserted = await self.bot.database.execute_returning_rowcount(
                """INSERT OR IGNORE INTO rss_articles
                   (feed_id, title, url, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (feed_id, title, article_url, published_at, jst_now()),
            )
            if inserted:
                new_count += 1

        return new_count

    async def _download_feed(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception:
            log.warning("RSS download failed: %s", url, exc_info=True)
            return None

    @staticmethod
    def _parse_date(entry) -> str:
        """feedparser entry から published_at を抽出。"""
        for attr in ("published_parsed", "updated_parsed"):
            tp = getattr(entry, attr, None)
            if tp:
                try:
                    dt = datetime(*tp[:6], tzinfo=JST)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
        raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        return jst_now()

    async def _purge_old_articles(self, retention_days: int) -> None:
        cutoff = (datetime.now(JST) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        await self.bot.database.execute(
            "DELETE FROM rss_articles WHERE fetched_at < ?", (cutoff,),
        )

    async def ensure_preset_feeds(self) -> None:
        """config.yaml のプリセットフィードをDBに同期する。"""
        presets = self.bot.config.get("rss", {}).get("presets", {})
        for category, cat_data in presets.items():
            feeds = cat_data.get("feeds", [])
            for f in feeds:
                url = f.get("url", "")
                title = f.get("title", "")
                if not url:
                    continue
                await self.bot.database.execute(
                    """INSERT OR IGNORE INTO rss_feeds (url, title, category, is_preset)
                       VALUES (?, ?, ?, 1)""",
                    (url, title, category),
                )
