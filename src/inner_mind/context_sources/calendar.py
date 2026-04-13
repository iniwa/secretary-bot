"""CalendarSource — 直近の Googleカレンダー予定を InnerMind に供給。

is_private=1 の予定は時刻のみ（タイトルは一切含まない）。
"""

from datetime import datetime, timedelta

from src.database import JST
from src.gcal.sync import parse_event_datetime
from src.inner_mind.context_sources.base import ContextSource


class CalendarSource(ContextSource):
    name = "カレンダー"
    priority = 35

    async def collect(self, shared: dict) -> dict | None:
        cfg = self.bot.config.get("calendar", {}).get("read_sync", {})
        if not cfg.get("enabled", False):
            return None

        # 直近 N 時間の予定を注入（デフォルト 24h）— プロンプト肥大防止
        inject_hours = int(cfg.get("inject_hours", 24))
        now = datetime.now(JST)
        horizon = now + timedelta(hours=inject_hours)

        rows = await self.bot.database.fetchall(
            "SELECT title, start_at, end_at, is_all_day, is_private "
            "FROM calendar_events "
            "ORDER BY start_at LIMIT 100"
        )
        if not rows:
            return None

        events = []
        for r in rows:
            start_dt = parse_event_datetime(r["start_at"])
            if start_dt is None:
                continue
            # JST 統一
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=JST)
            start_jst = start_dt.astimezone(JST)
            if start_jst < now - timedelta(hours=1):
                continue
            if start_jst > horizon:
                continue
            events.append({
                "start": start_jst,
                "title": r["title"],
                "is_all_day": bool(r["is_all_day"]),
                "is_private": bool(r["is_private"]),
            })

        if not events:
            return None
        return {"events": events}

    def format_for_prompt(self, data: dict) -> str:
        lines = []
        for ev in data["events"]:
            ts = ev["start"]
            when = ts.strftime("%m/%d") if ev["is_all_day"] else ts.strftime("%m/%d %H:%M")
            if ev["is_private"]:
                # タイトル・場所は一切出さない。時間帯のみ。
                lines.append(f"- {when}: （非公開の予定）")
            else:
                title = ev["title"] or "（タイトルなし）"
                lines.append(f"- {when}: {title}")
        return "\n".join(lines)
