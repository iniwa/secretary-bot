"""リマインダー・ToDo管理ユニット。"""

from datetime import datetime

from src.units.base_unit import BaseUnit

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

_EXTRACT_PROMPT = """\
現在日時: {now} ({weekday}曜日)

以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- add: リマインダー登録（message と time が必要）
- list: リマインダー一覧表示
- todo_add: ToDo追加（title が必要）
- todo_list: ToDo一覧表示
- todo_done: ToDo完了（id が必要）

## 出力形式（厳守）
{{"action": "アクション名", "message": "内容", "time": "YYYY-MM-DD HH:MM", "title": "ToDo内容", "id": 数値}}

不要なフィールドは省略してください。
日時表現は必ずISO形式に変換してください。
JSON以外は返さないでください。

## ユーザー入力
{user_input}
"""


class ReminderUnit(BaseUnit):
    UNIT_NAME = "reminder"
    UNIT_DESCRIPTION = "リマインダーやToDoの登録・一覧・完了管理。「〜時に教えて」「やることリスト」など。"

    # --- メイン処理 ---

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        message = parsed.get("message", "")
        try:
            # LLMでパラメータ抽出
            extracted = await self._extract_params(message)
            action = extracted.get("action", "add")

            if action == "add":
                result = await self._add_reminder(extracted)
            elif action == "list":
                result = await self._list_reminders()
            elif action == "todo_add":
                result = await self._add_todo(extracted)
            elif action == "todo_list":
                result = await self._list_todos()
            elif action == "todo_done":
                result = await self._done_todo(extracted)
            else:
                result = await self._add_reminder(extracted)
            self.breaker.record_success()
            return result
        except Exception:
            self.breaker.record_failure()
            raise

    async def _extract_params(self, user_input: str) -> dict:
        """ユーザー入力からLLMでパラメータを抽出する。"""
        now = datetime.now()
        prompt = _EXTRACT_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=_WEEKDAYS[now.weekday()],
            user_input=user_input,
        )
        return await self.llm.extract_json(prompt)

    # --- リマインダー ---

    async def _add_reminder(self, extracted: dict) -> str:
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
            "INSERT INTO reminders (message, remind_at) VALUES (?, ?)",
            (message, dt.isoformat()),
        )
        return f"リマインダーを設定しました: {dt.strftime('%m/%d %H:%M')} に「{message}」"

    async def _list_reminders(self) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM reminders WHERE active = 1 ORDER BY remind_at LIMIT 10"
        )
        if not rows:
            return "アクティブなリマインダーはありません。"
        lines = []
        for r in rows:
            lines.append(f"#{r['id']} {r['remind_at']} - {r['message']}")
        return "リマインダー一覧:\n" + "\n".join(lines)

    # --- ToDo ---

    async def _add_todo(self, extracted: dict) -> str:
        title = extracted.get("title", extracted.get("message", ""))
        if not title:
            return "ToDoの内容を教えてください。"
        await self.bot.database.execute(
            "INSERT INTO todos (title) VALUES (?)", (title,)
        )
        return f"ToDoに追加しました: {title}"

    async def _list_todos(self) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM todos WHERE done = 0 ORDER BY created_at LIMIT 20"
        )
        if not rows:
            return "未完了のToDoはありません。"
        lines = []
        for r in rows:
            lines.append(f"#{r['id']} {r['title']}")
        return "ToDo一覧:\n" + "\n".join(lines)

    async def _done_todo(self, extracted: dict) -> str:
        todo_id = extracted.get("id")
        if not todo_id:
            return "完了するToDoのIDを指定してください。"
        await self.bot.database.execute(
            "UPDATE todos SET done = 1, done_at = CURRENT_TIMESTAMP WHERE id = ?",
            (todo_id,),
        )
        return f"ToDo #{todo_id} を完了にしました。"

    # --- ハートビート ---

    async def on_heartbeat(self) -> None:
        """期限切れ間近のリマインダーを通知。"""
        now = datetime.now().isoformat()
        rows = await self.bot.database.fetchall(
            "SELECT * FROM reminders WHERE active = 1 AND remind_at <= ?",
            (now,),
        )
        for r in rows:
            await self.notify(f"リマインド: {r['message']}")
            await self.bot.database.execute(
                "UPDATE reminders SET active = 0 WHERE id = ?", (r["id"],)
            )


async def setup(bot) -> None:
    await bot.add_cog(ReminderUnit(bot))
