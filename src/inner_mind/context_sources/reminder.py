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

    async def salience(self, data: dict, shared: dict) -> float:
        """期限が近いほど高い。concerned mood ならさらに上がる。"""
        from datetime import datetime

        from src.database import JST

        reminders = data.get("reminders", [])
        if not reminders:
            return 0.0

        now = datetime.now(JST)
        min_hours: float | None = None
        for r in reminders:
            raw = r.get("remind_at")
            if not raw:
                continue
            try:
                dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=JST)
                hours = (dt - now).total_seconds() / 3600
                if min_hours is None or hours < min_hours:
                    min_hours = hours
            except Exception:
                continue

        if min_hours is None:
            base = 0.3
        elif min_hours <= 1:
            base = 1.0
        elif min_hours <= 6:
            base = 0.75
        elif min_hours <= 24:
            base = 0.55
        else:
            base = 0.3

        # 気がかりモードならリマインダーへの注意が増す
        if shared.get("mood") == "concerned":
            base = min(1.0, base + 0.15)
        return base
