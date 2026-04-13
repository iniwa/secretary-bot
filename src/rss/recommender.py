"""RSSレコメンダー — フィードバック履歴 + 興味タグベースのスコアリング・ランキング。

スコアリング方針:
    total = _score * feedback_weight + _interest_score * interest_weight
    - _score: フィードバック履歴（feed単位の評価合計）
    - _interest_score: people_memoryの【タグ】行から抽出した興味とのマッチスコア
    重み付けは config.yaml の rss.feedback_weight / rss.interest_weight で調整可能。
"""

from datetime import datetime, timedelta

from src.database import JST
from src.logger import get_logger
from src.memory.interest_extractor import extract_interest_tags, score_text_by_interests

log = get_logger(__name__)


class RSSRecommender:
    def __init__(self, bot):
        self.bot = bot

    async def get_digest(self, user_id: str = "") -> list[dict]:
        """カテゴリ別に記事をランキングしてダイジェスト用リストを返す。

        Returns:
            [{"category": str, "label": str, "articles": [dict]}]

        記事にはソート用の以下のフィールドが付与される:
            _score            : フィードバック由来のスコア
            _interest_score   : 興味タグベースのスコア
            _matched_tags     : ヒットした興味タグのリスト
        """
        rss_cfg = self.bot.config.get("rss", {})
        max_per_cat = rss_cfg.get("max_articles_per_category", 5)
        # 重み: 既存config構造を壊さず追加のみ
        feedback_weight = float(rss_cfg.get("feedback_weight", 1.0))
        interest_weight = float(rss_cfg.get("interest_weight", 2.0))

        # ユーザーのフィードバック集計（feed_id → score合計）
        feedback_scores = await self._get_feedback_scores(user_id)

        # ユーザーの購読設定（無効カテゴリ/フィードを除外）
        disabled_cats, disabled_feeds = await self._get_disabled(user_id)

        # カテゴリラベルマップ
        presets = rss_cfg.get("presets", {})
        cat_labels = {k: v.get("label", k) for k, v in presets.items()}

        # 興味タグは一度だけ取得（全記事で共用）
        try:
            interest_tags = await extract_interest_tags(self.bot)
        except Exception as e:
            log.debug("interest_tags extraction failed: %s", e)
            interest_tags = []

        # 直近24時間 + 要約済みの記事を取得（JST基準でカットオフを計算）
        cutoff = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        articles = await self.bot.database.fetchall(
            """SELECT a.*, f.category, f.title AS feed_title, f.id AS feed_id
               FROM rss_articles a
               JOIN rss_feeds f ON a.feed_id = f.id
               WHERE a.summary IS NOT NULL
                 AND a.fetched_at >= ?
               ORDER BY a.published_at DESC""",
            (cutoff,),
        )

        # カテゴリ別に分類 + スコアソート
        buckets: dict[str, list[dict]] = {}
        for a in articles:
            cat = a["category"]
            if cat in disabled_cats:
                continue
            if a["feed_id"] in disabled_feeds:
                continue
            a["_score"] = feedback_scores.get(a["feed_id"], 0)

            # 興味スコア: title + summary で判定
            text = f"{a.get('title') or ''} {a.get('summary') or ''}"
            try:
                interest_score, matched = await score_text_by_interests(
                    self.bot, text, interest_tags=interest_tags,
                )
            except Exception as e:
                log.debug("interest scoring failed: %s", e)
                interest_score, matched = 0.0, []
            a["_interest_score"] = interest_score
            a["_matched_tags"] = matched

            buckets.setdefault(cat, []).append(a)

        result = []
        for cat in sorted(buckets.keys()):
            items = buckets[cat]
            # 合計スコア: feedback * feedback_weight + interest * interest_weight
            # Pythonの安定ソート: 第二キー(published_at降順) → 第一キー(total降順)
            items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
            items.sort(
                key=lambda x: (
                    x.get("_score", 0) * feedback_weight
                    + x.get("_interest_score", 0.0) * interest_weight
                ),
                reverse=True,
            )
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
