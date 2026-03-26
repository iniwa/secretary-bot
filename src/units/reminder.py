"""リマインダー・ToDo管理ユニット。"""

from datetime import datetime

from discord.ext import commands

from src.units.base_unit import BaseUnit


class ReminderUnit(BaseUnit):
    SKILL_NAME = "reminder"
    SKILL_DESCRIPTION = "リマインダーやToDoの登録・一覧・完了管理。「〜時に教えて」「やることリスト」など。"

    # --- リマインダー ---

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        action = parsed.get("action", "add")
        try:
            if action == "add":
                return await self._add_reminder(parsed)
            elif action == "list":
                return await self._list_reminders()
            elif action == "todo_add":
                return await self._add_todo(parsed)
            elif action == "todo_list":
                return await self._list_todos()
            elif action == "todo_done":
                return await self._done_todo(parsed)
            else:
                return await self._add_reminder(parsed)
        except Exception as e:
            self.breaker.record_failure()
            raise
        else:
            self.breaker.record_success()

    async def _add_reminder(self, parsed: dict) -> str:
        message = parsed.get("message", "")
        remind_at = parsed.get("time", "")
        if not message:
            return "リマインドする内容を教えてください。"

        # 日時パースは簡易実装（デバッグ時に調整）
        try:
            dt = datetime.fromisoformat(remind_at) if remind_at else None
        except ValueError:
            dt = None

        if dt is None:
            return "日時の解析ができませんでした。「2025-01-01 08:00」のような形式で指定してください。"

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

    async def _add_todo(self, parsed: dict) -> str:
        title = parsed.get("title", parsed.get("message", ""))
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

    async def _done_todo(self, parsed: dict) -> str:
        todo_id = parsed.get("id")
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
