"""メモの保存・キーワード検索ユニット。"""

from src.database import jst_now
from src.units.base_unit import BaseUnit

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- save: メモを保存（content が必要、tags は任意）
- search: メモをキーワード検索（keyword が必要）
- list: メモを一覧表示
- delete: メモを削除（id が必要。「全部削除」なら id="all"）

## 出力形式（厳守）
{{"action": "アクション名", "content": "メモ内容", "tags": "タグ", "keyword": "検索キーワード", "id": "メモID", "ids": ["ID1", "ID2"]}}

- 不要なフィールドは省略してください。
- 複数IDの操作（例:「1と2を削除」）は ids フィールドに配列で指定してください。単一IDの場合は id を使用。
- JSON1つだけを返してください。複数のJSONを返さないでください。

## ユーザー入力
{user_input}
"""


class MemoUnit(BaseUnit):
    UNIT_NAME = "memo"
    UNIT_DESCRIPTION = "メモの保存やキーワード検索。「〜をメモして」「〜のメモある？」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        message = parsed.get("message", "")
        channel = parsed.get("channel", "")
        try:
            # LLMでパラメータ抽出
            extracted = await self._extract_params(message, channel)
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
            # リスト・検索結果は整形済みなので personalize しない
            if action not in ("list", "search"):
                result = await self.personalize(result, message)
            self.breaker.record_success()
            return result
        except Exception:
            self.breaker.record_failure()
            raise

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        """ユーザー入力からLLMでパラメータを抽出する。"""
        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    async def _save(self, extracted: dict) -> str:
        content = extracted.get("content", "")
        tags = extracted.get("tags", "")
        if not content:
            return "メモする内容を教えてください。"
        await self.bot.database.execute(
            "INSERT INTO memos (content, tags, created_at) VALUES (?, ?, ?)",
            (content, tags, jst_now()),
        )
        return f"メモしました: {content}"

    async def _list(self) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM memos ORDER BY created_at DESC LIMIT 20",
        )
        if not rows:
            return "メモはありません。"
        lines = [f"📄 メモ一覧（{len(rows)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            tags = f"  [{r['tags']}]" if r.get("tags") else ""
            lines.append(f"  #{r['id']}  {r['content']}{tags}")
        return "\n".join(lines)

    async def _delete(self, extracted: dict) -> str:
        # 全削除
        memo_id = str(extracted.get("id", ""))
        if memo_id == "all":
            await self.bot.database.execute("DELETE FROM memos")
            return "メモを全件削除しました。"

        # 複数ID対応
        ids = extracted.get("ids", [])
        if ids:
            id_list = [str(i) for i in ids]
        elif memo_id:
            id_list = [memo_id]
        else:
            return "削除するメモのIDを指定してください。"

        results = []
        for mid_str in id_list:
            try:
                mid = int(mid_str)
            except ValueError:
                results.append(f"#{mid_str} はIDとして不正です")
                continue
            existing = await self.bot.database.fetchone(
                "SELECT * FROM memos WHERE id = ?", (mid,)
            )
            if not existing:
                results.append(f"#{mid} が見つかりません")
            else:
                await self.bot.database.execute("DELETE FROM memos WHERE id = ?", (mid,))
                results.append(f"#{mid}「{existing['content']}」を削除しました")
        return "\n".join(results)

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
        lines = [f"🔍 メモ検索「{keyword}」（{len(rows)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            tags = f"  [{r['tags']}]" if r.get("tags") else ""
            lines.append(f"  #{r['id']}  {r['content']}{tags}")
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(MemoUnit(bot))
