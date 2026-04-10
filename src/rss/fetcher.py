"""RSSフェッチャー — feedparserで全フィードを巡回し新規記事をDBに保存。"""

import asyncio
import calendar
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import feedparser
import httpx

from src.database import JST, jst_now
from src.logger import get_logger

log = get_logger(__name__)

_HTTP_TIMEOUT = 15
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_DESC_LEN = 800

# 一部サイト (Cloudflare 保護など) はデフォルト httpx UA を弾くため、ブラウザ UA を送る
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "ja,en;q=0.7",
}


def _strip_html(text: str) -> str:
    """HTMLタグ除去・エンティティデコード・空白正規化。"""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:_MAX_DESC_LEN]


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
            description = self._extract_description(entry)

            inserted = await self.bot.database.execute_returning_rowcount(
                """INSERT OR IGNORE INTO rss_articles
                   (feed_id, title, url, description, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (feed_id, title, article_url, description, published_at, jst_now()),
            )
            if inserted:
                new_count += 1

        return new_count

    @staticmethod
    def _extract_description(entry) -> str:
        """feedparser entry から本文抜粋を抽出。content > summary > description の順に優先。"""
        # entry.content は list[dict]（Atom の本文）
        content = getattr(entry, "content", None)
        if content and isinstance(content, list):
            value = content[0].get("value", "") if isinstance(content[0], dict) else ""
            if value:
                return _strip_html(value)
        # RSS 2.0 の summary / description
        for attr in ("summary", "description", "subtitle"):
            raw = getattr(entry, attr, "")
            if raw:
                return _strip_html(raw)
        return ""

    async def _download_feed(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, follow_redirects=True, headers=_HTTP_HEADERS
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception:
            log.warning("RSS download failed: %s", url, exc_info=True)
            return None

    @staticmethod
    def _parse_date(entry) -> str:
        """feedparser entry から published_at を JST 文字列で返す。

        feedparser の *_parsed は UTC の time.struct_time なので、
        calendar.timegm で epoch に変換してから JST に直す。
        """
        for attr in ("published_parsed", "updated_parsed"):
            tp = getattr(entry, attr, None)
            if tp:
                try:
                    epoch = calendar.timegm(tp)
                    dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(JST)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
        raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        return jst_now()

    async def _purge_old_articles(self, retention_days: int) -> None:
        """published_at 優先でパージ。published_at が無い行は fetched_at で判定。"""
        cutoff = (datetime.now(JST) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        await self.bot.database.execute(
            "DELETE FROM rss_articles "
            "WHERE COALESCE(NULLIF(published_at, ''), fetched_at) < ?",
            (cutoff,),
        )

    async def ensure_preset_feeds(self) -> None:
        """config.yaml のプリセットフィードをDBに同期する。

        config.yaml の preset 集合を真のソースとして扱い、差分を適用する:
        - config にあって DB にない URL → INSERT
        - 同 URL で title/category が違う → UPDATE
        - 以前 preset だったが config から消えた URL → 関連記事ごと削除
        """
        presets = self.bot.config.get("rss", {}).get("presets", {})
        preset_urls: set[str] = set()
        for category, cat_data in presets.items():
            for f in cat_data.get("feeds", []):
                url = f.get("url", "")
                title = f.get("title", "")
                if not url:
                    continue
                preset_urls.add(url)
                await self.bot.database.execute(
                    """INSERT INTO rss_feeds (url, title, category, is_preset)
                       VALUES (?, ?, ?, 1)
                       ON CONFLICT(url) DO UPDATE SET
                           title    = excluded.title,
                           category = excluded.category,
                           is_preset = 1""",
                    (url, title, category),
                )
        # config.yaml から消えた preset を掃除（関連記事も一緒に）
        stale = await self.bot.database.fetchall(
            "SELECT id, url FROM rss_feeds WHERE is_preset = 1"
        )
        for row in stale:
            if row["url"] in preset_urls:
                continue
            await self.bot.database.execute(
                "DELETE FROM rss_articles WHERE feed_id = ?", (row["id"],)
            )
            await self.bot.database.execute(
                "DELETE FROM rss_feeds WHERE id = ?", (row["id"],)
            )
            log.info("Removed stale preset feed: %s", row["url"])
