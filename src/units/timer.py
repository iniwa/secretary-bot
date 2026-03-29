"""タイマーユニット — N分後に通知。"""

import asyncio

from src.units.base_unit import BaseUnit

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、タイマーの設定をJSON形式で返してください。

## 出力形式（厳守）
{{"minutes": 数値, "message": "通知メッセージ"}}

- minutes: 分単位の数値（「30秒」→ 0.5、「1時間」→ 60）
- message: タイマー完了時の通知メッセージ（省略時は「タイマー完了」）

JSON以外は返さないでください。

## ユーザー入力
{user_input}
"""


class TimerUnit(BaseUnit):
    UNIT_NAME = "timer"
    UNIT_DESCRIPTION = "指定時間後に通知するタイマー。「30分後に教えて」「5分タイマー」など。"

    def __init__(self, bot):
        super().__init__(bot)
        self._active_timers: dict[int, asyncio.Task] = {}
        self._next_id = 1

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        user_message = parsed.get("message", "")
        try:
            # LLMでパラメータ抽出
            extracted = await self._extract_params(user_message)
            minutes = extracted.get("minutes", 0)
            message = extracted.get("message", "タイマー完了")

            if not minutes or minutes <= 0:
                return "タイマーの時間を指定してください。"

            timer_id = self._next_id
            self._next_id += 1
            channel_id = ctx.channel.id if ctx and hasattr(ctx, "channel") else None

            task = asyncio.create_task(
                self._wait_and_notify(timer_id, minutes, message, user_message, channel_id)
            )
            self._active_timers[timer_id] = task
            result = f"タイマー#{timer_id} を設定しました: {minutes}分後に「{message}」"
            result = await self.personalize(result, user_message)
            self.breaker.record_success()
            self.session_done = True
            return result
        except Exception:
            self.breaker.record_failure()
            raise

    async def _extract_params(self, user_input: str) -> dict:
        """ユーザー入力からLLMでパラメータを抽出する。"""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        return await self.llm.extract_json(prompt)

    async def _wait_and_notify(
        self, timer_id: int, minutes: float, message: str,
        user_message: str, channel_id: int | None,
    ) -> None:
        await asyncio.sleep(minutes * 60)
        self._active_timers.pop(timer_id, None)

        raw = f"タイマー#{timer_id} 完了: {message}"
        notify_text = await self.personalize(raw, user_message)
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(notify_text)
                return
        await self.notify(notify_text)


async def setup(bot) -> None:
    await bot.add_cog(TimerUnit(bot))
