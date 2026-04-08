"""RSSSource — InnerMind に最近の記事トピックを供給。"""

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)


class RSSSource(ContextSource):
    name = "最近のニュース・記事"
    priority = 70

    async def collect(self, shared: dict) -> dict | None:
        articles = await self.bot.database.fetchall(
            """SELECT a.title, a.summary, a.url, f.category, f.title AS feed_title
               FROM rss_articles a
               JOIN rss_feeds f ON a.feed_id = f.id
               WHERE a.summary IS NOT NULL
               ORDER BY a.published_at DESC
               LIMIT 10""",
        )
        if not articles:
            return None
        return {"articles": articles}

    def format_for_prompt(self, data: dict) -> str:
        presets = self.bot.config.get("rss", {}).get("presets", {})
        cat_labels = {k: v.get("label", k) for k, v in presets.items()}

        lines = []
        for a in data["articles"]:
            cat = cat_labels.get(a["category"], a["category"])
            lines.append(f"- [{cat}] {a['title']}: {a['summary'][:100]}")
        return "\n".join(lines)
