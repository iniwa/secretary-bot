"""MemorySource — ChromaDB の ai_memory / people_memory。"""

from src.inner_mind.context_sources.base import ContextSource


class MemorySource(ContextSource):
    name = "記憶"
    priority = 40

    async def collect(self, shared: dict) -> dict | None:
        query = (
            shared.get("last_monologue")
            or shared.get("recent_summary")
            or "最近の出来事"
        )
        ai = self.bot.chroma.search("ai_memory", query=query, n_results=5)
        people = self.bot.chroma.search("people_memory", query=query, n_results=5)
        if not ai and not people:
            return None
        return {"ai_memory": ai, "people_memory": people}

    def format_for_prompt(self, data: dict) -> str:
        lines = []
        ai = data.get("ai_memory", [])
        people = data.get("people_memory", [])
        if ai:
            lines.append("AI記憶:")
            for item in ai:
                lines.append(f"  - {item['text']}")
        if people:
            lines.append("ユーザー記憶:")
            for item in people:
                lines.append(f"  - {item['text']}")
        return "\n".join(lines)
