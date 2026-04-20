"""RSSプロセッサー — 未要約記事をLLMで要約。"""

import asyncio

from src.logger import get_logger

log = get_logger(__name__)

_SUMMARY_SYSTEM = (
    "あなたは記事要約係です。日本語で簡潔に2文以内で要約してください。"
    "主観や感想は書かず、事実ベースで書いてください。"
)

_SUMMARY_PROMPT_WITH_DESC = """\
以下の記事タイトルと本文抜粋から、日本語2文以内で要約してください。

タイトル: {title}
本文抜粋: {description}

要約:"""

_SUMMARY_PROMPT_TITLE_ONLY = """\
以下の記事タイトルから、日本語1文で内容を推測した要約を書いてください。
タイトルの言い換えで構いません。

タイトル: {title}

要約:"""

_MIN_DESC_LEN = 40


class RSSProcessor:
    def __init__(self, bot):
        self.bot = bot

    async def summarize_unsummarized(self, limit: int = 20) -> int:
        """未要約記事をLLMで要約。処理件数を返す。"""
        if await self.bot.activity_detector.is_blocked():
            log.debug("RSS summarization skipped: activity blocked")
            return 0
        articles = await self.bot.database.fetchall(
            """SELECT a.id, a.title, a.url, a.description FROM rss_articles a
               WHERE a.summary IS NULL
               ORDER BY a.fetched_at DESC LIMIT ?""",
            (limit,),
        )
        if not articles:
            return 0

        # 先頭1件をプローブとして逐次実行。Ollamaゾンビ状態での無駄な並列発射を回避
        first_summary = await self._summarize_article(articles[0])
        if first_summary is None and not self.bot.llm_router.ollama_available:
            log.info(
                "RSS summarize aborted: Ollama unavailable (skipped %d articles)",
                len(articles) - 1,
            )
            return 0

        # プローブ成功時は残りを並列実行（Ollamaマルチインスタンスで自動分配）
        rest_summaries = await asyncio.gather(
            *[self._summarize_article(a) for a in articles[1:]],
            return_exceptions=True,
        )
        summaries = [first_summary, *rest_summaries]

        count = 0
        for article, summary in zip(articles, summaries, strict=False):
            if isinstance(summary, Exception):
                log.warning("RSS summary failed for article %d: %s", article["id"], summary)
                continue
            if summary:
                await self.bot.database.execute(
                    "UPDATE rss_articles SET summary = ? WHERE id = ?",
                    (summary.strip(), article["id"]),
                )
                count += 1
        log.info("RSS processor: summarized %d/%d articles", count, len(articles))
        return count

    async def _summarize_article(self, article: dict) -> str | None:
        description = (article.get("description") or "").strip()
        title = article.get("title") or ""
        if len(description) >= _MIN_DESC_LEN:
            prompt = _SUMMARY_PROMPT_WITH_DESC.format(title=title, description=description)
        else:
            prompt = _SUMMARY_PROMPT_TITLE_ONLY.format(title=title)
        try:
            return await self.bot.llm_router.generate(
                prompt, system=_SUMMARY_SYSTEM,
                purpose="rss_summary", ollama_only=True,
            )
        except Exception:
            log.warning("RSS summary failed for article %d", article["id"], exc_info=True)
            return None
