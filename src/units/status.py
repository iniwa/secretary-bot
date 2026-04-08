"""PC・サーバー状態確認ユニット。"""

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit


class StatusUnit(BaseUnit):
    UNIT_NAME = "status"
    UNIT_DESCRIPTION = "PCやサーバーの稼働状況を確認。「PCは起きてる？」「ステータス確認」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            status = await self.bot.status_collector.collect()
            result = self.bot.status_collector.format_discord(status)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise


async def setup(bot) -> None:
    await bot.add_cog(StatusUnit(bot))
