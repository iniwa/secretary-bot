"""BaseUnit — 全ユニットの基底クラス。discord.py の Cog を継承。"""

import os

from discord.ext import commands

from src.circuit_breaker import CircuitBreaker
from src.logger import get_logger

log = get_logger(__name__)


class BaseUnit(commands.Cog):
    SKILL_NAME: str = ""
    SKILL_DESCRIPTION: str = ""
    DELEGATE_TO: str | None = None
    PREFERRED_AGENT: str | None = None

    def __init__(self, bot):
        self.bot = bot
        self._breaker = CircuitBreaker(name=self.SKILL_NAME)
        self._admin_channel_id = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0"))

    async def execute(self, ctx, parsed: dict) -> str | None:
        """ユニットの主処理。サブクラスでオーバーライドする。"""
        raise NotImplementedError

    async def on_heartbeat(self) -> None:
        """ハートビート時に呼ばれる。必要に応じてオーバーライド。"""
        pass

    # --- Discord通知ヘルパー ---

    async def notify(self, message: str) -> None:
        if self._admin_channel_id:
            channel = self.bot.get_channel(self._admin_channel_id)
            if channel:
                await channel.send(message)

    async def notify_error(self, message: str) -> None:
        await self.notify(f"[Error] {message}")

    # --- サーキットブレーカー ---

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker
