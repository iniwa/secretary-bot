"""Unit Manager — ユニットの自動ロード・管理。"""

import importlib

from src.logger import get_logger
from src.units.agent_pool import AgentPool
from src.units.remote_proxy import RemoteUnitProxy

log = get_logger(__name__)

_UNIT_MODULES = {
    "reminder": "src.units.reminder",
    "memo": "src.units.memo",
    "timer": "src.units.timer",
    "status": "src.units.status",
    "chat": "src.units.chat",
    "web_search": "src.units.web_search",
    "rakuten_search": "src.units.rakuten_search",
    "weather": "src.units.weather",
    "calendar": "src.units.calendar",
    "power": "src.units.power",
}


class UnitManager:
    def __init__(self, bot):
        self.bot = bot
        self.units: dict = {}
        self.agent_pool = AgentPool(bot.config)

    async def load_units(self) -> None:
        units_config = self.bot.config.get("units", {})
        for name, module_path in _UNIT_MODULES.items():
            cfg = units_config.get(name, {})
            if isinstance(cfg, dict) and not cfg.get("enabled", True):
                log.info("Unit %s disabled, skipping", name)
                continue

            try:
                module = importlib.import_module(module_path)
                await module.setup(self.bot)
                # Cog から取得
                for cog in self.bot.cogs.values():
                    if hasattr(cog, "UNIT_NAME") and cog.UNIT_NAME == name:
                        self.units[name] = cog
                        # DELEGATE_TO がある場合はプロキシでラップ
                        if cog.DELEGATE_TO:
                            self.units[name] = RemoteUnitProxy(self.bot, cog)
                        break
                log.info("Loaded unit: %s", name)
            except Exception as e:
                log.error("Failed to load unit %s: %s", name, e)

    def get(self, name: str):
        return self.units.get(name)
