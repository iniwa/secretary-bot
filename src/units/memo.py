"""メモの保存・キーワード検索ユニット。"""

from src.database import jst_now
from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- save: メモを保存（content が必要、tags は任意）
- edit: メモを編集（id が必要、content や tags を変更）
- append: メモに追記（id が必要、content に追記内容）
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
    AUTONOMY_TIER = 2
    AUTONOMOUS_ACTIONS = ["add"]

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)

        message = parsed.get("message", "")
        channel = parsed.get("channel", "")
        user_id = parsed.get("user_id", "")
        try:
            # LLMでパラメータ抽出
            extracted = await self._extract_params(message, channel)
            action = extracted.get("action", "save")

            if action == "search":
                result = await self._search(extracted, user_id)
                self.session_done = True
            elif action == "list":
                result = await self._list(user_id)
                # listの後はIDで削除等の操作が続く可能性があるのでセッション維持
            elif action == "delete":
                result = await self._delete(extracted, user_id)
                self.session_done = True
            elif action == "edit":
                result = await self._edit(extracted, user_id)
                self.session_done = True
            elif action == "append":
                result = await self._append(extracted, user_id)
                self.session_done = True
            else:
                result = await self._save(extracted, user_id)
                self.session_done = True
            if action in ("list", "search"):
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
        """ユーザー入力からLLMでパラメータを抽出する。"""
        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    async def _save(self, extracted: dict, user_id: str = "") -> str:
        content = extracted.get("content", "")
        tags = extracted.get("tags", "")
        if not content:
            return "メモする内容を教えてください。"
        await self.bot.database.execute(
            "INSERT INTO memos (content, tags, user_id, created_at) VALUES (?, ?, ?, ?)",
            (content, tags, user_id, jst_now()),
        )
        return f"メモしました: {content}"

    async def _list(self, user_id: str = "") -> str:
        if user_id:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM memos WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                (user_id,),
            )
        else:
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

    async def _delete(self, extracted: dict, user_id: str = "") -> str:
        # 全削除
        memo_id = str(extracted.get("id", ""))
        if memo_id == "all":
            if user_id:
                await self.bot.database.execute("DELETE FROM memos WHERE user_id = ?", (user_id,))
            else:
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
            if user_id:
                existing = await self.bot.database.fetchone(
                    "SELECT * FROM memos WHERE id = ? AND user_id = ?", (mid, user_id)
                )
            else:
                existing = await self.bot.database.fetchone(
                    "SELECT * FROM memos WHERE id = ?", (mid,)
                )
            if not existing:
                results.append(f"#{mid} が見つかりません")
            else:
                await self.bot.database.execute("DELETE FROM memos WHERE id = ?", (mid,))
                results.append(f"#{mid}「{existing['content']}」を削除しました")
        return "\n".join(results)

    async def _search(self, extracted: dict, user_id: str = "") -> str:
        keyword = extracted.get("keyword", "")
        if not keyword:
            return "検索キーワードを教えてください。"
        if user_id:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM memos WHERE user_id = ? AND (content LIKE ? OR tags LIKE ?) ORDER BY created_at DESC LIMIT 10",
                (user_id, f"%{keyword}%", f"%{keyword}%"),
            )
        else:
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


    async def _edit(self, extracted: dict, user_id: str = "") -> str:
        memo_id = extracted.get("id")
        if not memo_id:
            return "編集するメモのIDを指定してください。"
        try:
            mid = int(memo_id)
        except ValueError:
            return f"#{memo_id} はIDとして不正です。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM memos WHERE id = ? AND user_id = ?", (mid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM memos WHERE id = ?", (mid,)
            )
        if not row:
            return f"メモ #{mid} が見つかりません。"
        new_content = extracted.get("content") or row["content"]
        new_tags = extracted.get("tags") if "tags" in extracted else row.get("tags", "")
        await self.bot.database.execute(
            "UPDATE memos SET content = ?, tags = ? WHERE id = ?",
            (new_content, new_tags, mid),
        )
        return f"メモ #{mid} を更新しました: {new_content}"

    async def _append(self, extracted: dict, user_id: str = "") -> str:
        memo_id = extracted.get("id")
        if not memo_id:
            return "追記するメモのIDを指定してください。"
        append_content = extracted.get("content", "")
        if not append_content:
            return "追記する内容を教えてください。"
        try:
            mid = int(memo_id)
        except ValueError:
            return f"#{memo_id} はIDとして不正です。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM memos WHERE id = ? AND user_id = ?", (mid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM memos WHERE id = ?", (mid,)
            )
        if not row:
            return f"メモ #{mid} が見つかりません。"
        updated = row["content"] + "\n" + append_content
        await self.bot.database.execute(
            "UPDATE memos SET content = ? WHERE id = ?", (updated, mid)
        )
        return f"メモ #{mid} に追記しました: {append_content}"


async def setup(bot) -> None:
    await bot.add_cog(MemoUnit(bot))
