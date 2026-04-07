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
