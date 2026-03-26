"""メモの保存・キーワード検索ユニット。"""

from src.units.base_unit import BaseUnit


class MemoUnit(BaseUnit):
    SKILL_NAME = "memo"
    SKILL_DESCRIPTION = "メモの保存やキーワード検索。「〜をメモして」「〜のメモある？」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        action = parsed.get("action", "save")
        try:
            if action == "search":
                return await self._search(parsed)
            else:
                return await self._save(parsed)
        except Exception:
            self.breaker.record_failure()
            raise
        else:
            self.breaker.record_success()

    async def _save(self, parsed: dict) -> str:
        content = parsed.get("content", parsed.get("message", ""))
        tags = parsed.get("tags", "")
        if not content:
            return "メモする内容を教えてください。"
        await self.bot.database.execute(
            "INSERT INTO memos (content, tags) VALUES (?, ?)",
            (content, tags),
        )
        return f"メモしました: {content}"

    async def _search(self, parsed: dict) -> str:
        keyword = parsed.get("keyword", parsed.get("message", ""))
        if not keyword:
            return "検索キーワードを教えてください。"
        rows = await self.bot.database.fetchall(
            "SELECT * FROM memos WHERE content LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT 10",
            (f"%{keyword}%", f"%{keyword}%"),
        )
        if not rows:
            return f"「{keyword}」に一致するメモは見つかりませんでした。"
        lines = []
        for r in rows:
            lines.append(f"#{r['id']} {r['content']}")
        return "メモ検索結果:\n" + "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(MemoUnit(bot))
