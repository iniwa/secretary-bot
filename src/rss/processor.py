"""RSSプロセッサー — 未要約記事をLLMで要約。"""

from src.logger import get_logger

log = get_logger(__name__)

_SUMMARY_SYSTEM = "あなたは記事要約係です。日本語で簡潔に要約してください。"

_SUMMARY_PROMPT = """\
以下の記事タイトルとURLから、1-2行で要約してください。
タイトルだけで内容が十分わかる場合はタイトルの言い換えで構いません。

タイトル: {title}
URL: {url}

要約:"""


class RSSProcessor:
    def __init__(self, bot):
        self.bot = bot

    async def summarize_unsummarized(self, limit: int = 20) -> int:
        """未要約記事をLLMで要約。処理件数を返す。"""
        # TODO: activity_detector.is_blocked() — 非アクティブ時のみ実行
        articles = await self.bot.database.fetchall(
            """SELECT a.id, a.title, a.url FROM rss_articles a
               WHERE a.summary IS NULL
               ORDER BY a.fetched_at DESC LIMIT ?""",
            (limit,),
        )
        if not articles:
            return 0

        count = 0
        for article in articles:
            summary = await self._summarize_article(article)
            if summary:
                await self.bot.database.execute(
                    "UPDATE rss_articles SET summary = ? WHERE id = ?",
                    (summary, article["id"]),
                )
                count += 1
        log.info("RSS processor: summarized %d/%d articles", count, len(articles))
        return count

    async def _summarize_article(self, article: dict) -> str | None:
        prompt = _SUMMARY_PROMPT.format(title=article["title"], url=article["url"])
        try:
            return await self.bot.llm_router.generate(
                prompt, system=_SUMMARY_SYSTEM,
                purpose="rss_summary", ollama_only=True,
            )
        except Exception:
            log.warning("RSS summary failed for article %d", article["id"], exc_info=True)
            return None
