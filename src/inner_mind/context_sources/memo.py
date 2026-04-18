"""MemoSource — ユーザーのメモ。"""

from src.inner_mind.context_sources.base import ContextSource


class MemoSource(ContextSource):
    name = "メモ"
    priority = 20

    async def collect(self, shared: dict) -> dict | None:
        memos = await self.bot.database.fetchall(
            "SELECT content, tags, created_at FROM memos ORDER BY created_at DESC LIMIT 10"
        )
        if not memos:
            return None
        return {"memos": memos}

    def format_for_prompt(self, data: dict) -> str:
        lines = []
        for m in data["memos"]:
            tags = f"[{m['tags']}] " if m.get("tags") else ""
            lines.append(f"- {tags}{m['content']}（{m['created_at']}）")
        return "\n".join(lines)

    async def salience(self, data: dict, shared: dict) -> float:
        """最近追加したメモほど注目に値する。古いだけなら中立以下。"""
        from datetime import datetime

        from src.database import JST

        memos = data.get("memos", [])
        if not memos:
            return 0.0
        latest = memos[0].get("created_at")
        if not latest:
            return 0.3
        try:
            dt = latest if isinstance(latest, datetime) else datetime.fromisoformat(str(latest))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            age_min = (datetime.now(JST) - dt).total_seconds() / 60
        except Exception:
            return 0.3

        if age_min <= 30:
            return 0.8
        if age_min <= 180:
            return 0.55
        if age_min <= 60 * 24:
            return 0.4
        return 0.2
