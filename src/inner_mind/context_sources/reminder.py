"""ReminderSource — 直近のリマインダー。"""

from src.inner_mind.context_sources.base import ContextSource


class ReminderSource(ContextSource):
    name = "リマインダー"
    priority = 30

    async def collect(self, shared: dict) -> dict | None:
        upcoming = await self.bot.database.fetchall(
            "SELECT message, remind_at FROM reminders "
            "WHERE active = 1 AND notified = 0 ORDER BY remind_at LIMIT 5"
        )
        if not upcoming:
            return None
        return {"reminders": upcoming}

    def format_for_prompt(self, data: dict) -> str:
        lines = []
        for r in data["reminders"]:
            lines.append(f"- {r['remind_at']}: {r['message']}")
        return "\n".join(lines)
