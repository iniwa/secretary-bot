"""Windows Agent の /activity エンドポイントを定期取得。"""

import os

import httpx

from src.logger import get_logger

log = get_logger(__name__)


class AgentActivityMonitor:
    """AgentPool の接続情報を使い、各 Agent の /activity を取得する。"""

    def __init__(self, agents: list[dict]):
        self._agents = agents
        self._token = os.environ.get("AGENT_SECRET_TOKEN", "")
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=5)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def fetch(self, agent: dict) -> dict | None:
        """1台の Agent から /activity を取得。失敗時は None。"""
        url = f"http://{agent['host']}:{agent['port']}/activity"
        headers = {"X-Agent-Token": self._token} if self._token else {}
        try:
            resp = await self._get_http().get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            log.debug("Agent %s /activity returned %d", agent.get("id"), resp.status_code)
        except Exception as e:
            log.debug("Agent %s /activity failed: %s", agent.get("id"), e)
        return None

    async def fetch_all(self) -> dict[str, dict]:
        """全 Agent の /activity を取得。{role: response} で返す。"""
        results: dict[str, dict] = {}
        for agent in self._agents:
            data = await self.fetch(agent)
            if data:
                role = data.get("role", agent.get("role", "unknown"))
                results[role] = data
        return results
