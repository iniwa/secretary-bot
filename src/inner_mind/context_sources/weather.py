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
