"""RSSレコメンダー — フィードバック履歴ベースのスコアリング・ランキング。"""

from src.logger import get_logger

log = get_logger(__name__)


class RSSRecommender:
    def __init__(self, bot):
        self.bot = bot

    async def get_digest(self, user_id: str = "") -> list[dict]:
        """カテゴリ別に記事をランキングしてダイジェスト用リストを返す。

        Returns:
            [{"category": str, "label": str, "articles": [dict]}]
        """
        max_per_cat = self.bot.config.get("rss", {}).get("max_articles_per_category", 5)

        # ユーザーのフィードバック集計（feed_id → score合計）
        feedback_scores = await self._get_feedback_scores(user_id)

        # ユーザーの購読設定（無効カテゴリ/フィードを除外）
        disabled_cats, disabled_feeds = await self._get_disabled(user_id)

        # カテゴリラベルマップ
        presets = self.bot.config.get("rss", {}).get("presets", {})
        cat_labels = {k: v.get("label", k) for k, v in presets.items()}

        # 直近24時間 + 要約済みの記事を取得
        articles = await self.bot.database.fetchall(
            """SELECT a.*, f.category, f.title AS feed_title, f.id AS feed_id
               FROM rss_articles a
               JOIN rss_feeds f ON a.feed_id = f.id
               WHERE a.summary IS NOT NULL
                 AND a.fetched_at >= datetime('now', '-1 day')
               ORDER BY a.published_at DESC""",
        )

        # カテゴリ別に分類 + スコアソート
        buckets: dict[str, list[dict]] = {}
        for a in articles:
            cat = a["category"]
            if cat in disabled_cats:
                continue
            if a["feed_id"] in disabled_feeds:
                continue
            score = feedback_scores.get(a["feed_id"], 0)
            a["_score"] = score
            buckets.setdefault(cat, []).append(a)

        result = []
        for cat in sorted(buckets.keys()):
            items = buckets[cat]
            # スコア降順 → 日時降順
            items.sort(key=lambda x: (-x["_score"], x.get("published_at", "") or ""), reverse=False)
            items.sort(key=lambda x: -x["_score"])
            result.append({
                "category": cat,
                "label": cat_labels.get(cat, cat),
                "articles": items[:max_per_cat],
            })
        return result

    async def _get_feedback_scores(self, user_id: str) -> dict[int, int]:
        """フィードバック履歴からfeed_idごとのスコア合計を返す。"""
        if not user_id:
            return {}
        rows = await self.bot.database.fetchall(
            """SELECT a.feed_id, SUM(fb.rating) as total
               FROM rss_feedback fb
               JOIN rss_articles a ON fb.article_id = a.id
               WHERE fb.user_id = ?
               GROUP BY a.feed_id""",
            (user_id,),
        )
        return {r["feed_id"]: r["total"] for r in rows}

    async def _get_disabled(self, user_id: str) -> tuple[set[str], set[int]]:
        """ユーザーが無効にしたカテゴリとフィードIDを返す。"""
        disabled_cats: set[str] = set()
        disabled_feeds: set[int] = set()
        if not user_id:
            return disabled_cats, disabled_feeds
        prefs = await self.bot.database.fetchall(
            "SELECT * FROM rss_user_prefs WHERE user_id = ? AND enabled = 0",
            (user_id,),
        )
        for p in prefs:
            if p.get("category"):
                disabled_cats.add(p["category"])
            if p.get("feed_id"):
                disabled_feeds.add(p["feed_id"])
        return disabled_cats, disabled_feeds
