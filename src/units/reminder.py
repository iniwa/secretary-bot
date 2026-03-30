"""リマインダー・ToDo管理ユニット。"""

from datetime import datetime

from src.database import JST, jst_now
from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

_EXTRACT_PROMPT = """\
現在日時: {now} ({weekday}曜日)

以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- add: リマインダー登録（message と time が必要）
- list: リマインダー一覧表示
- edit: リマインダー編集（id が必要、変更する message または time を含める）
- delete: リマインダー削除（id が必要）
- done: リマインダー完了（id が必要）
- todo_add: ToDo追加（title が必要）
- todo_list: ToDo一覧表示
- todo_done: ToDo完了（id が必要）
- todo_edit: ToDo編集（id と title が必要）
- todo_delete: ToDo削除（id が必要）

## 出力形式（厳守）
{{"action": "アクション名", "message": "内容", "time": "YYYY-MM-DD HH:MM", "title": "ToDo内容", "id": 数値, "ids": [数値]}}

- 不要なフィールドは省略してください。
- 日時表現は必ずISO形式に変換してください。
- 複数IDの操作（例:「1と2を削除」）は ids フィールドに配列で指定してください。単一IDの場合は id を使用。
- JSON1つだけを返してください。複数のJSONを返さないでください。

## ユーザー入力
{user_input}
"""


class ReminderUnit(BaseUnit):
    UNIT_NAME = "reminder"
    UNIT_DESCRIPTION = "リマインダーやToDoの登録・一覧・編集・削除・完了管理。「〜時に教えて」「やることリスト」など。"

    # --- メイン処理 ---

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
            extracted = await self._extract_params(message, channel)
            action = extracted.get("action", "add")

            # list系はセッション維持（後続のID指定操作に備える）、それ以外は完了
            if action == "add":
                result = await self._add_reminder(extracted, user_id)
                self.session_done = True
            elif action == "list":
                result = await self._list_reminders(user_id)
            elif action == "edit":
                result = await self._edit_reminder(extracted, user_id)
                self.session_done = True
            elif action == "delete":
                result = await self._delete_reminder(extracted, user_id)
                self.session_done = True
            elif action == "done":
                result = await self._done_reminder(extracted, user_id)
                self.session_done = True
            elif action == "todo_add":
                result = await self._add_todo(extracted, user_id)
                self.session_done = True
            elif action == "todo_list":
                result = await self._list_todos(user_id)
            elif action == "todo_done":
                result = await self._done_todo(extracted, user_id)
                self.session_done = True
            elif action == "todo_edit":
                result = await self._edit_todo(extracted, user_id)
                self.session_done = True
            elif action == "todo_delete":
                result = await self._delete_todo(extracted, user_id)
                self.session_done = True
            else:
                result = await self._add_reminder(extracted, user_id)
                self.session_done = True
            if action in ("list", "todo_list"):
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
        now = datetime.now(JST)
        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=_WEEKDAYS[now.weekday()],
            user_input=user_input,
        )
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    # --- リマインダー ---

    async def _add_reminder(self, extracted: dict, user_id: str = "") -> str:
        message = extracted.get("message", "")
        time_str = extracted.get("time", "")
        if not message:
            return "リマインドする内容を教えてください。"
        if not time_str:
            return "日時の解析ができませんでした。「明日の10時」「2025-01-01 08:00」のような形式で指定してください。"
        try:
            dt = datetime.fromisoformat(time_str)
        except ValueError:
            return "日時の解析ができませんでした。「明日の10時」「2025-01-01 08:00」のような形式で指定してください。"
        await self.bot.database.execute(
            "INSERT INTO reminders (message, remind_at, user_id) VALUES (?, ?, ?)",
            (message, dt.isoformat(), user_id),
        )
        return f"リマインダーを設定しました: {dt.strftime('%m/%d %H:%M')} に「{message}」"

    async def _list_reminders(self, user_id: str = "") -> str:
        if user_id:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 AND user_id = ? ORDER BY remind_at LIMIT 10",
                (user_id,),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 ORDER BY remind_at LIMIT 10"
            )
        if not rows:
            return "アクティブなリマインダーはありません。"
        lines = [f"📋 リマインダー一覧（{len(rows)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["remind_at"])
                time_str = dt.strftime("%m/%d %H:%M")
            except Exception:
                time_str = r["remind_at"]
            status = " ⚠️通知済" if r.get("notified") else ""
            lines.append(f"  #{r['id']}  {time_str}  {r['message']}{status}")
        return "\n".join(lines)

    async def _edit_reminder(self, extracted: dict, user_id: str = "") -> str:
        rid = extracted.get("id")
        if not rid:
            return "編集するリマインダーのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (rid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1", (rid,)
            )
        if not row:
            return f"リマインダー #{rid} が見つかりません。"
        new_message = extracted.get("message") or row["message"]
        new_time_str = row["remind_at"]
        if extracted.get("time"):
            try:
                dt = datetime.fromisoformat(extracted["time"])
                new_time_str = dt.isoformat()
            except ValueError:
                return "日時の解析ができませんでした。"
        await self.bot.database.execute(
            "UPDATE reminders SET message = ?, remind_at = ?, notified = 0 WHERE id = ?",
            (new_message, new_time_str, rid),
        )
        dt_display = datetime.fromisoformat(new_time_str)
        return f"リマインダー #{rid} を更新しました: {dt_display.strftime('%m/%d %H:%M')} に「{new_message}」"

    def _get_ids(self, extracted: dict) -> list[int]:
        """id または ids から対象IDリストを取得する。"""
        ids = extracted.get("ids", [])
        if ids:
            return [int(i) for i in ids]
        rid = extracted.get("id")
        return [int(rid)] if rid else []

    async def _delete_reminder(self, extracted: dict, user_id: str = "") -> str:
        ids = self._get_ids(extracted)
        if not ids:
            return "削除するリマインダーのIDを指定してください。"
        results = []
        for rid in ids:
            if user_id:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND user_id = ?", (rid, user_id)
                )
            else:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ?", (rid,)
                )
            if not row:
                results.append(f"#{rid} が見つかりません")
            else:
                await self.bot.database.execute("DELETE FROM reminders WHERE id = ?", (rid,))
                results.append(f"#{rid}「{row['message']}」を削除しました")
        return "\n".join(results)

    async def _done_reminder(self, extracted: dict, user_id: str = "") -> str:
        ids = self._get_ids(extracted)
        if not ids:
            return "完了にするリマインダーのIDを指定してください。"
        results = []
        for rid in ids:
            if user_id:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (rid, user_id)
                )
            else:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND active = 1", (rid,)
                )
            if not row:
                results.append(f"#{rid} が見つかりません")
            else:
                await self.bot.database.execute(
                    "UPDATE reminders SET active = 0, done_at = ? WHERE id = ?",
                    (jst_now(), rid),
                )
                results.append(f"#{rid}「{row['message']}」を完了にしました")
        return "\n".join(results)

    # --- ToDo ---

    async def _add_todo(self, extracted: dict, user_id: str = "") -> str:
        title = extracted.get("title") or extracted.get("message", "")
        if not title:
            return "ToDoの内容を教えてください。"
        await self.bot.database.execute(
            "INSERT INTO todos (title, user_id, created_at) VALUES (?, ?, ?)", (title, user_id, jst_now())
        )
        return f"ToDoに追加しました: {title}"

    async def _list_todos(self, user_id: str = "") -> str:
        if user_id:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM todos WHERE done = 0 AND user_id = ? ORDER BY created_at LIMIT 20",
                (user_id,),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM todos WHERE done = 0 ORDER BY created_at LIMIT 20"
            )
        if not rows:
            return "未完了のToDoはありません。"
        lines = [f"📝 ToDo一覧（{len(rows)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            lines.append(f"  #{r['id']}  {r['title']}")
        return "\n".join(lines)

    async def _done_todo(self, extracted: dict, user_id: str = "") -> str:
        todo_id = extracted.get("id")
        if not todo_id:
            return "完了するToDoのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0 AND user_id = ?", (todo_id, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0", (todo_id,)
            )
        if not row:
            return f"ToDo #{todo_id} が見つかりません。"
        await self.bot.database.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ?",
            (jst_now(), todo_id),
        )
        return f"ToDo #{todo_id}「{row['title']}」を完了にしました。"

    async def _edit_todo(self, extracted: dict, user_id: str = "") -> str:
        tid = extracted.get("id")
        title = extracted.get("title")
        if not tid:
            return "編集するToDoのIDを指定してください。"
        if not title:
            return "新しいタイトルを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0 AND user_id = ?", (tid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0", (tid,)
            )
        if not row:
            return f"ToDo #{tid} が見つかりません。"
        await self.bot.database.execute(
            "UPDATE todos SET title = ? WHERE id = ?", (title, tid)
        )
        return f"ToDo #{tid} を「{title}」に更新しました。"

    async def _delete_todo(self, extracted: dict, user_id: str = "") -> str:
        tid = extracted.get("id")
        if not tid:
            return "削除するToDoのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND user_id = ?", (tid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ?", (tid,)
            )
        if not row:
            return f"ToDo #{tid} が見つかりません。"
        await self.bot.database.execute("DELETE FROM todos WHERE id = ?", (tid,))
        return f"ToDo #{tid}「{row['title']}」を削除しました。"

    # --- ハートビート ---

    async def on_heartbeat(self) -> None:
        """期限切れリマインダーを通知（notified=0のみ）。完了はユーザーが明示的に行う。"""
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        rows = await self.bot.database.fetchall(
            "SELECT * FROM reminders WHERE active = 1 AND notified = 0 AND remind_at <= ?",
            (now,),
        )
        for r in rows:
            await self.notify_user(
                f"リマインド: {r['message']}\n"
                f"完了したら「リマインダー{r['id']}番を完了にして」と教えてください。",
                user_id=r.get("user_id", ""),
            )
            await self.bot.database.execute(
                "UPDATE reminders SET notified = 1 WHERE id = ?", (r["id"],)
            )


async def setup(bot) -> None:
    await bot.add_cog(ReminderUnit(bot))
