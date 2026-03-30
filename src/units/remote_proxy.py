"""透過的な委託ラッパー — DELEGATE_TO="windows" のユニットをラップ。"""

import httpx

from src.errors import DelegationError
from src.flow_tracker import get_flow_tracker
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
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("DELEGATE", "active", {"unit": self.unit.UNIT_NAME}, flow_id)

        agent = await self.bot.unit_manager.agent_pool.select_agent(
            preferred=self.unit.PREFERRED_AGENT
        )
        if agent is None:
            log.warning("No agent available for %s, executing locally", self.unit.UNIT_NAME)
            await ft.emit("DELEGATE", "done", {"mode": "local", "reason": "no_agent"}, flow_id)
            return await self.unit.execute(ctx, parsed)

        await ft.emit("AGENT_SELECT", "done", {"agent": agent.get("id", "")}, flow_id)
        await ft.emit("AGENT_HEALTH", "active", {"agent": agent.get("id", "")}, flow_id)

        url = f"http://{agent['host']}:{agent['port']}/execute/{self.unit.UNIT_NAME}"
        headers = {"X-Agent-Token": self.agent_token}

        try:
            await ft.emit("AGENT_HEALTH", "done", {"agent": agent.get("id", "")}, flow_id)
            await ft.emit("REMOTE_EXEC", "active", {"agent": agent.get("id", ""), "unit": self.unit.UNIT_NAME}, flow_id)
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=parsed, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                await ft.emit("REMOTE_EXEC", "done", {"agent": agent.get("id", "")}, flow_id)
                await ft.emit("DELEGATE", "done", {"mode": "remote"}, flow_id)
                return data.get("result", "")
        except Exception as e:
            log.error("Remote execution failed for %s: %s", self.unit.UNIT_NAME, e)
            await ft.emit("REMOTE_EXEC", "error", {"error": str(e)}, flow_id)
            await ft.emit("DELEGATE", "error", {"error": str(e)}, flow_id)
            raise DelegationError(f"Remote execution failed: {e}") from e
