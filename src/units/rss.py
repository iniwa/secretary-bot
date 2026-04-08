"""RSSユニット — フィード管理・ダイジェスト表示（SkillRouter対応）。"""

from src.database import jst_now
from src.flow_tracker import get_flow_tracker
from src.rss.notify import RSSNotifier
from src.rss.recommender import RSSRecommender
from src.units.base_unit import BaseUnit

_EXTRACT_PROMPT = """\
以下のユーザー入力をRSS操作として分析し、JSON形式で返してください。

## アクション一覧
- digest: 最新のおすすめ記事を表示
- list: 購読中のフィード一覧
- add: RSSフィードを追加（url が必要、category は任意）
- remove: RSSフィードを削除（url または feed_id が必要）
- enable_category: カテゴリの購読を有効化（category が必要）
- disable_category: カテゴリの購読を無効化（category が必要）

## カテゴリ
gaming, tech, pc, vr, news

## 出力形式（厳守）
{{"action": "アクション名", "url": "フィードURL", "category": "カテゴリ", "feed_id": "フィードID", "title": "フィード名"}}

- 不要なフィールドは省略してください。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""


class RSSUnit(BaseUnit):
    UNIT_NAME = "rss"
    UNIT_DESCRIPTION = "RSS購読管理、ニュース・おすすめ記事の表示。「最新ニュース」「RSSの一覧」「このRSS追加して」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)

        message = parsed.get("message", "")
        user_id = parsed.get("user_id", "")
        try:
            extracted = await self._extract_params(message, parsed.get("channel", ""))
            action = extracted.get("action", "digest")

            if action == "add":
                result = await self._add_feed(extracted, user_id)
            elif action == "remove":
                result = await self._remove_feed(extracted, user_id)
            elif action == "list":
                result = await self._list_feeds(user_id)
            elif action == "enable_category":
                result = await self._toggle_category(extracted, user_id, enable=True)
            elif action == "disable_category":
                result = await self._toggle_category(extracted, user_id, enable=False)
            else:
                result = await self._show_digest(user_id)

            self.session_done = True
            if action in ("list", "digest"):
                result = await self.personalize_list(result, message, flow_id)
            else:
                result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": action}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    async def _show_digest(self, user_id: str) -> str:
        recommender = RSSRecommender(self.bot)
        digest = await recommender.get_digest(user_id)
        if not digest or not any(b["articles"] for b in digest):
            return "まだおすすめできる記事がないよ。フィードが登録されているか確認してみて。"
        notifier = RSSNotifier(self.bot)
        return notifier.format_digest(digest)

    async def _list_feeds(self, user_id: str) -> str:
        feeds = await self.bot.database.fetchall(
            "SELECT * FROM rss_feeds ORDER BY category, title",
        )
        if not feeds:
            return "登録されているRSSフィードはありません。"

        # ユーザーの無効設定を取得
        disabled_cats = set()
        disabled_feeds = set()
        if user_id:
            prefs = await self.bot.database.fetchall(
                "SELECT * FROM rss_user_prefs WHERE user_id = ? AND enabled = 0",
                (user_id,),
            )
            for p in prefs:
                if p.get("category"):
                    disabled_cats.add(p["category"])
                if p.get("feed_id"):
                    disabled_feeds.add(p["feed_id"])

        presets = self.bot.config.get("rss", {}).get("presets", {})
        cat_labels = {k: v.get("label", k) for k, v in presets.items()}

        lines = [f"RSS フィード一覧（{len(feeds)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        current_cat = None
        for f in feeds:
            cat = f["category"]
            if cat != current_cat:
                label = cat_labels.get(cat, cat)
                status = " [無効]" if cat in disabled_cats else ""
                lines.append(f"\n**{label}**{status}")
                current_cat = cat
            feed_status = " [無効]" if f["id"] in disabled_feeds else ""
            preset = " (プリセット)" if f["is_preset"] else ""
            lines.append(f"  #{f['id']}  {f['title']}{preset}{feed_status}")
            lines.append(f"       {f['url']}")
        return "\n".join(lines)

    async def _add_feed(self, extracted: dict, user_id: str) -> str:
        url = extracted.get("url", "").strip()
        if not url:
            return "追加するRSSのURLを教えてください。"
        title = extracted.get("title", url[:50])
        category = extracted.get("category", "other")

        existing = await self.bot.database.fetchone(
            "SELECT id FROM rss_feeds WHERE url = ?", (url,),
        )
        if existing:
            return f"そのフィードは既に登録されています（#{existing['id']}）。"

        await self.bot.database.execute(
            """INSERT INTO rss_feeds (url, title, category, is_preset, added_by, created_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (url, title, category, user_id, jst_now()),
        )
        return f"RSSフィードを追加しました: {title}（{category}）"

    async def _remove_feed(self, extracted: dict, user_id: str) -> str:
        feed_id = extracted.get("feed_id")
        url = extracted.get("url", "")

        if feed_id:
            try:
                fid = int(feed_id)
            except ValueError:
                return f"#{feed_id} はIDとして不正です。"
            feed = await self.bot.database.fetchone(
                "SELECT * FROM rss_feeds WHERE id = ?", (fid,),
            )
        elif url:
            feed = await self.bot.database.fetchone(
                "SELECT * FROM rss_feeds WHERE url = ?", (url,),
            )
        else:
            return "削除するフィードのIDかURLを指定してください。"

        if not feed:
            return "そのフィードは見つかりません。"
        if feed["is_preset"]:
            return f"プリセットフィード「{feed['title']}」は削除できません。無効化はできます。"

        await self.bot.database.execute("DELETE FROM rss_articles WHERE feed_id = ?", (feed["id"],))
        await self.bot.database.execute("DELETE FROM rss_feeds WHERE id = ?", (feed["id"],))
        return f"フィード「{feed['title']}」を削除しました。"

    async def _toggle_category(self, extracted: dict, user_id: str, enable: bool) -> str:
        category = extracted.get("category", "")
        if not category:
            return "カテゴリを指定してください（gaming, tech, pc, vr, news）。"
        if not user_id:
            return "ユーザー情報が必要です。"

        if enable:
            await self.bot.database.execute(
                "DELETE FROM rss_user_prefs WHERE user_id = ? AND category = ?",
                (user_id, category),
            )
            return f"カテゴリ「{category}」の購読を有効にしました。"
        else:
            await self.bot.database.execute(
                """INSERT OR REPLACE INTO rss_user_prefs (user_id, category, enabled)
                   VALUES (?, ?, 0)""",
                (user_id, category),
            )
            return f"カテゴリ「{category}」の購読を無効にしました。"


async def setup(bot) -> None:
    await bot.add_cog(RSSUnit(bot))
