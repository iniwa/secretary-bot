"""ConversationSource — 直近の会話履歴。"""

from src.inner_mind.context_sources.base import ContextSource


class ConversationSource(ContextSource):
    name = "最近の会話"
    priority = 10

    async def collect(self, shared: dict) -> dict | None:
        messages = await self.bot.database.get_recent_messages(limit=20)
        if not messages:
            return None
        return {"messages": messages}

    def format_for_prompt(self, data: dict) -> str:
        lines = []
        for m in reversed(data["messages"]):
            role = "ミミ" if m["role"] == "assistant" else "ユーザー"
            lines.append(f"{role}: {m['content']}")
        return "\n".join(lines[-20:])
