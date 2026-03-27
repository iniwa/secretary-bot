"""透過的な委託ラッパー — DELEGATE_TO="windows" のユニットをラップ。"""

import httpx

from src.errors import DelegationError
from src.logger import get_logger

log = get_logger(__name__)


class RemoteUnitProxy:
    """Windows Agent へ処理を委託するプロキシ。"""

    def __init__(self, bot, unit):
        self.bot = bot
        self.unit = unit
        self._agent_token = None

    @property
    def agent_token(self) -> str:
        if self._agent_token is None:
            import os
            self._agent_token = os.environ.get("AGENT_SECRET_TOKEN", "")
        return self._agent_token

    async def execute(self, ctx, parsed: dict) -> str | None:
        agent = await self.bot.unit_manager.agent_pool.select_agent(
            preferred=self.unit.PREFERRED_AGENT
        )
        if agent is None:
            log.warning("No agent available for %s, executing locally", self.unit.UNIT_NAME)
            return await self.unit.execute(ctx, parsed)

        url = f"http://{agent['host']}:{agent['port']}/execute/{self.unit.UNIT_NAME}"
        headers = {"X-Agent-Token": self.agent_token}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=parsed, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data.get("result", "")
        except Exception as e:
            log.error("Remote execution failed for %s: %s", self.unit.UNIT_NAME, e)
            raise DelegationError(f"Remote execution failed: {e}") from e
