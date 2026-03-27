"""メモの保存・キーワード検索ユニット。"""

from src.units.base_unit import BaseUnit

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- save: メモを保存（content が必要、tags は任意）
- search: メモをキーワード検索（keyword が必要）

## 出力形式（厳守）
{{"action": "アクション名", "content": "メモ内容", "tags": "タグ", "keyword": "検索キーワード"}}

不要なフィールドは省略してください。
JSON以外は返さないでください。

## ユーザー入力
{user_input}
"""


class MemoUnit(BaseUnit):
    UNIT_NAME = "memo"
    UNIT_DESCRIPTION = "メモの保存やキーワード検索。「〜をメモして」「〜のメモある？」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        message = parsed.get("message", "")
        try:
            # LLMでパラメータ抽出
            extracted = await self._extract_params(message)
            action = extracted.get("action", "save")

            if action == "search":
                result = await self._search(extracted)
            else:
                result = await self._save(extracted)
            result = await self.personalize(result, message)
            self.breaker.record_success()
            return result
        except Exception:
            self.breaker.record_failure()
            raise

    async def _extract_params(self, user_input: str) -> dict:
        """ユーザー入力からLLMでパラメータを抽出する。"""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        return await self.llm.extract_json(prompt)

    async def _save(self, extracted: dict) -> str:
        content = extracted.get("content", "")
        tags = extracted.get("tags", "")
        if not content:
            return "メモする内容を教えてください。"
        await self.bot.database.execute(
            "INSERT INTO memos (content, tags) VALUES (?, ?)",
            (content, tags),
        )
        return f"メモしました: {content}"

    async def _search(self, extracted: dict) -> str:
        keyword = extracted.get("keyword", "")
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
