"""WeatherSource — 天気サブスクリプション地域情報。"""

from src.inner_mind.context_sources.base import ContextSource


class WeatherSource(ContextSource):
    name = "天気"
    priority = 50

    async def collect(self, shared: dict) -> dict | None:
        subs = await self.bot.database.fetchall(
            "SELECT location FROM weather_subscriptions WHERE active = 1"
        )
        if not subs:
            return None
        return {"locations": [s["location"] for s in subs]}

    def format_for_prompt(self, data: dict) -> str:
        locations = data.get("locations", [])
        return "登録地域: " + "、".join(locations)

    async def salience(self, data: dict, shared: dict) -> float:
        """天気は朝夕に高く、深夜は低い。外出予定の気配があれば上がる。"""
        time_ctx = shared.get("time_context", "")
        if time_ctx in ("朝", "夕方"):
            return 0.75
        if time_ctx == "深夜":
            return 0.15
        if time_ctx in ("昼", "午前", "午後"):
            return 0.45
        return 0.35
