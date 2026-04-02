"""天気予報ユニット — Open-Meteo API を使った天気予報取得・毎朝通知。"""

from datetime import datetime, timedelta

import httpx

from src.database import JST, jst_now
from src.flow_tracker import get_flow_tracker
from src.memory.people_memory import PeopleMemory
from src.units.base_unit import BaseUnit

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

# WMO Weather Interpretation Codes → 日本語 + 絵文字
_WMO_CODES = {
    0: ("快晴", "☀️"),
    1: ("晴れ", "🌤"),
    2: ("くもり時々晴れ", "⛅"),
    3: ("くもり", "☁️"),
    45: ("霧", "🌫"),
    48: ("着氷性の霧", "🌫"),
    51: ("弱い霧雨", "🌦"),
    53: ("霧雨", "🌧"),
    55: ("強い霧雨", "🌧"),
    56: ("着氷性の弱い霧雨", "🌧"),
    57: ("着氷性の霧雨", "🌧"),
    61: ("小雨", "🌦"),
    63: ("雨", "🌧"),
    65: ("大雨", "🌧"),
    66: ("着氷性の小雨", "🌧"),
    67: ("着氷性の雨", "🌧"),
    71: ("小雪", "🌨"),
    73: ("雪", "❄️"),
    75: ("大雪", "❄️"),
    77: ("霧雪", "🌨"),
    80: ("にわか雨", "🌦"),
    81: ("強いにわか雨", "🌧"),
    82: ("激しいにわか雨", "🌧"),
    85: ("にわか雪", "🌨"),
    86: ("強いにわか雪", "❄️"),
    95: ("雷雨", "⛈"),
    96: ("雹を伴う雷雨", "⛈"),
    99: ("強い雹を伴う雷雨", "⛈"),
}

_EXTRACT_PROMPT = """\
現在日時: {now} ({weekday}曜日)

以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- get_weather: 特定の日の天気を取得（location と date が必要）
- weekly: 週間天気を取得（location が必要）
- subscribe: 毎朝の天気通知を登録（location, hour, minute は任意。デフォルト: 朝7時）
- unsubscribe: 天気通知の解除（id が必要）
- list: 天気通知の登録一覧

## 出力形式（厳守）
{{"action": "アクション名", "location": "地名", "date": "YYYY-MM-DD", "hour": 7, "minute": 0, "id": 数値}}

- 不要なフィールドは省略してください。
- 「今日」「明日」「明後日」等の相対日付は必ずYYYY-MM-DD形式に変換してください。
- 地名が指定されていない場合は location を省略してください。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""

_LOCATION_EXTRACT_PROMPT = """\
以下の情報から、この人が住んでいる地域・都市名を1つだけ抽出してください。
都市名だけを返してください。情報がなければ「不明」と返してください。

{memory_text}
"""


class WeatherUnit(BaseUnit):
    UNIT_NAME = "weather"
    UNIT_DESCRIPTION = "天気予報の取得や毎朝の天気通知登録。「東京の天気」「明日の天気」「毎朝教えて」など。"

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("weather", {})
        self._geocoding_url = cfg.get("geocoding_url", "https://geocoding-api.open-meteo.com/v1/search")
        self._forecast_url = cfg.get("forecast_url", "https://api.open-meteo.com/v1/forecast")
        self._default_location = cfg.get("default_location", "東京")
        self._http_timeout = cfg.get("http_timeout", 10)
        self._umbrella_threshold = cfg.get("umbrella_threshold", 50)

    # --- メイン処理 ---

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)

        message = parsed.get("message", "")
        channel = parsed.get("channel", "")
        user_id = parsed.get("user_id", "")
        try:
            extracted = await self._extract_params(message, channel)
            action = extracted.get("action", "get_weather")

            if action == "subscribe":
                result = await self._subscribe(extracted, user_id)
            elif action == "unsubscribe":
                result = await self._unsubscribe(extracted, user_id)
            elif action == "list":
                result = await self._list(user_id)
            elif action == "weekly":
                result = await self._get_weekly(extracted, user_id)
            else:
                result = await self._get_weather(extracted, user_id)

            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
            result = await self.personalize(result, message, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    # --- LLMパラメータ抽出 ---

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        now = datetime.now(JST)
        prompt = _EXTRACT_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=_WEEKDAYS[now.weekday()],
            user_input=user_input,
        )
        context = self.get_context(channel) if channel else ""
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    # --- 地名解決 ---

    async def _resolve_location(self, location: str | None, user_id: str) -> str:
        """地名を解決する。未指定時はpeople_memoryから検索、それでもなければデフォルト。"""
        if location:
            return location

        # people_memoryから住まい情報を検索
        try:
            people = PeopleMemory(self.bot)
            results = people.recall("住まい 住所 地域 住んでいる", n_results=3, user_id=user_id)
            if results:
                memory_text = "\n".join(r.get("document", "") for r in results)
                city = await self.llm.generate(_LOCATION_EXTRACT_PROMPT.format(memory_text=memory_text))
                city = city.strip()
                if city and "不明" not in city:
                    return city
        except Exception:
            pass

        return self._default_location

    # --- API呼び出し ---

    async def _geocode(self, location: str) -> dict | None:
        """地名から緯度経度を取得する。見つからない場合は「市」「県」付きでリトライ。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # 候補リスト: そのまま → 「市」付き → 「県」付き
            candidates = [location]
            if not location.endswith(("市", "区", "町", "村", "県", "都", "府", "道")):
                candidates.append(f"{location}市")
                candidates.append(f"{location}県")

            for name in candidates:
                resp = await client.get(
                    self._geocoding_url,
                    params={"name": name, "count": 1, "language": "ja", "format": "json"},
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results")
                if results:
                    r = results[0]
                    return {
                        "name": r.get("name", location),
                        "latitude": r["latitude"],
                        "longitude": r["longitude"],
                    }
            return None

    async def _fetch_forecast(self, lat: float, lon: float, days: int = 3) -> dict | None:
        """天気予報データを取得する。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(
                self._forecast_url,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                    "timezone": "Asia/Tokyo",
                    "forecast_days": min(days, 16),
                },
            )
            resp.raise_for_status()
            return resp.json()

    # --- フォーマット ---

    def _weather_label(self, code: int) -> str:
        name, emoji = _WMO_CODES.get(code, ("不明", "❓"))
        return f"{emoji} {name}"

    def _clothing_advice(self, temp_max: float, temp_min: float) -> str:
        diff = temp_max - temp_min
        parts = []
        if temp_max >= 30:
            parts.append("暑くなります。涼しい服装で水分補給を忘れずに")
        elif temp_max >= 25:
            parts.append("半袖で過ごせる陽気です")
        elif temp_max >= 20:
            parts.append("薄手の上着があると安心です")
        elif temp_max >= 15:
            parts.append("上着が必要です")
        elif temp_max >= 10:
            parts.append("暖かい服装で")
        else:
            parts.append("しっかり防寒してください")
        if diff >= 10:
            parts.append(f"寒暖差{diff:.0f}℃、脱ぎ着しやすい服装がおすすめ")
        return "。".join(parts)

    def _format_daily(self, forecast: dict, target_date: str, location: str) -> str:
        daily = forecast.get("daily", {})
        dates = daily.get("time", [])
        if target_date not in dates:
            return f"{location}の{target_date}の天気予報は取得できませんでした。予報範囲外の可能性があります。"

        idx = dates.index(target_date)
        code = daily["weather_code"][idx]
        temp_max = daily["temperature_2m_max"][idx]
        temp_min = daily["temperature_2m_min"][idx]
        precip = daily["precipitation_probability_max"][idx]
        wind = daily["wind_speed_10m_max"][idx]

        dt = datetime.strptime(target_date, "%Y-%m-%d")
        weekday = _WEEKDAYS[dt.weekday()]

        lines = [
            f"**{location}** {target_date}（{weekday}）の天気",
            f"天気: {self._weather_label(code)}",
            f"気温: {temp_min:.0f}℃ 〜 {temp_max:.0f}℃",
            f"降水確率: {precip}%",
            f"最大風速: {wind:.0f} km/h",
        ]

        # 傘リマインド
        if precip is not None and precip >= self._umbrella_threshold:
            lines.append(f"☂ 降水確率が{precip}%です。傘を持っていきましょう！")

        # 服装アドバイス
        if temp_max is not None and temp_min is not None:
            lines.append(f"👔 {self._clothing_advice(temp_max, temp_min)}")

        return "\n".join(lines)

    def _format_weekly(self, forecast: dict, location: str) -> str:
        daily = forecast.get("daily", {})
        dates = daily.get("time", [])
        lines = [f"**{location}** の週間天気予報"]
        for i, date in enumerate(dates[:7]):
            dt = datetime.strptime(date, "%Y-%m-%d")
            weekday = _WEEKDAYS[dt.weekday()]
            code = daily["weather_code"][i]
            temp_max = daily["temperature_2m_max"][i]
            temp_min = daily["temperature_2m_min"][i]
            precip = daily["precipitation_probability_max"][i]
            _, emoji = _WMO_CODES.get(code, ("不明", "❓"))
            umbrella = " ☂" if precip is not None and precip >= self._umbrella_threshold else ""
            lines.append(
                f"{date}（{weekday}）{emoji} {temp_min:.0f}〜{temp_max:.0f}℃ 降水{precip}%{umbrella}"
            )
        return "\n".join(lines)

    # --- 公開メソッド（Heartbeatから呼ばれる） ---

    async def build_daily_notification(self, lat: float, lon: float, location: str) -> str:
        """毎朝通知用のメッセージを組み立てる。"""
        try:
            forecast = await self._fetch_forecast(lat, lon, days=1)
            if not forecast:
                return f"{location}の天気情報を取得できませんでした。"
            today = datetime.now(JST).strftime("%Y-%m-%d")
            return self._format_daily(forecast, today, location)
        except Exception as e:
            return f"{location}の天気情報の取得に失敗しました: {e}"

    # --- アクション実装 ---

    async def _get_weather(self, extracted: dict, user_id: str) -> str:
        location_input = extracted.get("location")
        location = await self._resolve_location(location_input, user_id)
        target_date = extracted.get("date", datetime.now(JST).strftime("%Y-%m-%d"))

        geo = await self._geocode(location)
        if not geo:
            self.session_done = True
            return f"「{location}」の位置情報が見つかりませんでした。別の地名で試してください。"

        # 日数を計算
        today = datetime.now(JST).date()
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
        days_ahead = (target - today).days + 1
        if days_ahead < 1:
            days_ahead = 1
        if days_ahead > 16:
            self.session_done = True
            return "16日先までの予報しか取得できません。"

        forecast = await self._fetch_forecast(geo["latitude"], geo["longitude"], days=max(days_ahead, 3))
        if not forecast:
            self.session_done = True
            return f"{geo['name']}の天気情報を取得できませんでした。"

        self.session_done = True
        return self._format_daily(forecast, target_date, geo["name"])

    async def _get_weekly(self, extracted: dict, user_id: str) -> str:
        location_input = extracted.get("location")
        location = await self._resolve_location(location_input, user_id)

        geo = await self._geocode(location)
        if not geo:
            self.session_done = True
            return f"「{location}」の位置情報が見つかりませんでした。"

        forecast = await self._fetch_forecast(geo["latitude"], geo["longitude"], days=7)
        if not forecast:
            self.session_done = True
            return f"{geo['name']}の天気情報を取得できませんでした。"

        self.session_done = True
        return self._format_weekly(forecast, geo["name"])

    async def _subscribe(self, extracted: dict, user_id: str) -> str:
        location_input = extracted.get("location")
        location = await self._resolve_location(location_input, user_id)
        hour = extracted.get("hour", 7)
        minute = extracted.get("minute", 0)

        geo = await self._geocode(location)
        if not geo:
            self.session_done = True
            return f"「{location}」の位置情報が見つかりませんでした。通知を登録できません。"

        # DB登録
        cursor = await self.bot.database.execute(
            "INSERT INTO weather_subscriptions (user_id, location, latitude, longitude, notify_hour, notify_minute) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, geo["name"], geo["latitude"], geo["longitude"], hour, minute),
        )
        sub_id = cursor.lastrowid

        # スケジューラ登録
        self.bot.heartbeat.schedule_weather_daily(
            sub_id, hour, minute, user_id,
            geo["latitude"], geo["longitude"], geo["name"],
        )

        self.session_done = True
        return (
            f"{geo['name']}の天気予報を毎朝{hour}:{minute:02d}にお届けします。"
            f"（登録ID: {sub_id}）\n解除するときは「天気通知やめて」と言ってください。"
        )

    async def _unsubscribe(self, extracted: dict, user_id: str) -> str:
        sub_id = extracted.get("id")
        if sub_id is None:
            # IDなしの場合、ユーザーのアクティブな通知を全解除
            rows = await self.bot.database.fetchall(
                "SELECT id FROM weather_subscriptions WHERE user_id = ? AND active = 1",
                (user_id,),
            )
            if not rows:
                self.session_done = True
                return "登録されている天気通知はありません。"
            for row in rows:
                await self.bot.database.execute(
                    "UPDATE weather_subscriptions SET active = 0 WHERE id = ?", (row["id"],)
                )
                self.bot.heartbeat.cancel_weather_daily(row["id"])
            self.session_done = True
            return f"{len(rows)}件の天気通知を解除しました。"

        # 特定ID解除
        row = await self.bot.database.fetchone(
            "SELECT * FROM weather_subscriptions WHERE id = ? AND user_id = ? AND active = 1",
            (sub_id, user_id),
        )
        if not row:
            self.session_done = True
            return f"ID {sub_id} の天気通知が見つかりません。"

        await self.bot.database.execute(
            "UPDATE weather_subscriptions SET active = 0 WHERE id = ?", (sub_id,)
        )
        self.bot.heartbeat.cancel_weather_daily(sub_id)
        self.session_done = True
        return f"{row['location']}の天気通知（ID: {sub_id}）を解除しました。"

    async def _list(self, user_id: str) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM weather_subscriptions WHERE user_id = ? AND active = 1 ORDER BY id",
            (user_id,),
        )
        if not rows:
            self.session_done = True
            return "登録されている天気通知はありません。"

        lines = ["**天気通知の登録一覧**"]
        for r in rows:
            lines.append(
                f"  ID:{r['id']} | {r['location']} | 毎朝 {r['notify_hour']}:{r['notify_minute']:02d}"
            )
        lines.append("\n解除するときは「天気通知 ID○ をやめて」と言ってください。")
        # セッション維持（IDでunsubscribeの継続操作用）
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(WeatherUnit(bot))
