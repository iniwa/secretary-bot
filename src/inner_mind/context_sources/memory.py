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

    async def salience(self, data: dict, shared: dict) -> float:
        """振り返り/休息レンズでは高め。それ以外は中立。"""
        ai = data.get("ai_memory", []) or []
        people = data.get("people_memory", []) or []
        if not ai and not people:
            return 0.0

        lens = shared.get("lens", "")
        if lens in ("reflection", "rest"):
            return 0.7
        if lens == "empathy":
            # 相手のことを想像するときは人物記憶が効く
            return 0.6 if people else 0.35
        mood = shared.get("mood", "")
        if mood in ("concerned", "calm"):
            return 0.5
        return 0.35
