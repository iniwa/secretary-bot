"""タイマーユニット — N分後に通知。"""

import asyncio

from src.units.base_unit import BaseUnit


class TimerUnit(BaseUnit):
    SKILL_NAME = "timer"
    SKILL_DESCRIPTION = "指定時間後に通知するタイマー。「30分後に教えて」「5分タイマー」など。"

    def __init__(self, bot):
        super().__init__(bot)
        self._active_timers: dict[int, asyncio.Task] = {}
        self._next_id = 1

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        minutes = parsed.get("minutes", 0)
        message = parsed.get("message", "タイマー完了")

        if not minutes or minutes <= 0:
            return "タイマーの分数を指定してください。"

        timer_id = self._next_id
        self._next_id += 1
        channel_id = ctx.channel.id if ctx and hasattr(ctx, "channel") else None

        task = asyncio.create_task(self._wait_and_notify(timer_id, minutes, message, channel_id))
        self._active_timers[timer_id] = task
        self.breaker.record_success()
        return f"タイマー#{timer_id} を設定しました: {minutes}分後に「{message}」"

    async def _wait_and_notify(self, timer_id: int, minutes: float, message: str, channel_id: int | None) -> None:
        await asyncio.sleep(minutes * 60)
        self._active_timers.pop(timer_id, None)

        notify_text = f"タイマー#{timer_id} 完了: {message}"
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(notify_text)
                return
        await self.notify(notify_text)


async def setup(bot) -> None:
    await bot.add_cog(TimerUnit(bot))
