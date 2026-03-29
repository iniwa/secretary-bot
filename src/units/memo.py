"""メモの保存・キーワード検索ユニット。"""

from src.units.base_unit import BaseUnit

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- save: メモを保存（content が必要、tags は任意）
- search: メモをキーワード検索（keyword が必要）
- list: メモを一覧表示
- delete: メモを削除（id が必要。「全部削除」なら id="all"）

## 出力形式（厳守）
{{"action": "アクション名", "content": "メモ内容", "tags": "タグ", "keyword": "検索キーワード", "id": "メモID"}}

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
                self.session_done = True
            elif action == "list":
                result = await self._list()
                # listの後はIDで削除等の操作が続く可能性があるのでセッション維持
            elif action == "delete":
                result = await self._delete(extracted)
                self.session_done = True
            else:
                result = await self._save(extracted)
                self.session_done = True
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

    async def _list(self) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM memos ORDER BY created_at DESC LIMIT 20",
        )
        if not rows:
            return "メモはありません。"
        lines = []
        for r in rows:
            lines.append(f"#{r['id']} {r['content']}")
        return "メモ一覧:\n" + "\n".join(lines)

    async def _delete(self, extracted: dict) -> str:
        memo_id = str(extracted.get("id", ""))
        if not memo_id:
            return "削除するメモのIDを指定してください。"
        if memo_id == "all":
            await self.bot.database.execute("DELETE FROM memos")
            return "メモを全件削除しました。"
        try:
            mid = int(memo_id)
        except ValueError:
            return "メモIDは数値で指定してください。"
        existing = await self.bot.database.fetchone(
            "SELECT id FROM memos WHERE id = ?", (mid,)
        )
        if not existing:
            return f"ID#{mid} のメモは見つかりませんでした。"
        await self.bot.database.execute(
            "DELETE FROM memos WHERE id = ?", (mid,)
        )
        return f"メモ#{mid} を削除しました。"

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
