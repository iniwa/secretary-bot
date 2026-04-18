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

    async def salience(self, data: dict, shared: dict) -> float:
        """好奇心モードでは高く、静かなモードでは情報を遮断する。

        興味トピックが記事タイトルに現れるとさらに上がる。
        """
        articles = data.get("articles", [])
        if not articles:
            return 0.0
        mood = shared.get("mood", "")
        base = {
            "curious": 0.75,
            "talkative": 0.55,
            "calm": 0.3,
            "concerned": 0.2,
            "idle": 0.15,
        }.get(mood, 0.35)

        interest = (shared.get("interest_topic") or "").strip()
        if interest:
            for a in articles[:10]:
                title = a.get("title", "") or ""
                if interest in title:
                    base = min(1.0, base + 0.2)
                    break
        return base
