"""複数Windows PCの管理・フォールバック。"""

import os

import httpx

from src.logger import get_logger

log = get_logger(__name__)


class AgentPool:
    def __init__(self, config: dict):
        self._agents = sorted(
            config.get("windows_agents", []),
            key=lambda a: a.get("priority", 99),
        )
        self._delegation_config = config.get("delegation", {})
        self._metrics_url = config.get("metrics", {}).get("victoria_metrics_url", "")
        self._agent_token = os.environ.get("AGENT_SECRET_TOKEN", "")
        # 委託モード: per-agent override (WebGUIから動的変更される想定)
        self._modes: dict[str, str] = {}  # agent_id → "allow" | "deny" | "auto"
        # 共有httpxクライアント（毎回生成しない）
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=5)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    def set_mode(self, agent_id: str, mode: str) -> None:
        self._modes[agent_id] = mode

    def get_mode(self, agent_id: str) -> str:
        return self._modes.get(agent_id, "auto")

    async def select_agent(self, preferred: str | None = None) -> dict | None:
        agents = list(self._agents)
        if preferred:
            agents.sort(key=lambda a: 0 if a["id"] == preferred else 1)

        for agent in agents:
            mode = self.get_mode(agent["id"])
            if mode == "deny":
                continue
            if not await self._is_alive(agent):
                continue
            if mode == "auto" and not await self._is_idle(agent):
                continue
            return agent

        return None

    async def _is_alive(self, agent: dict) -> bool:
        url = f"http://{agent['host']}:{agent['port']}/health"
        try:
            resp = await self._get_http().get(url, headers={"X-Agent-Token": self._agent_token})
            return resp.status_code == 200
        except Exception:
            return False

    async def _is_idle(self, agent: dict) -> bool:
        """VictoriaMetrics APIでCPU・メモリ使用率を確認。"""
        if not self._metrics_url:
            return True  # メトリクスなしなら委託許可

        instance = agent.get("metrics_instance", "")
        if not instance:
            return True

        thresholds = self._delegation_config.get("thresholds", {})
        cpu_limit = thresholds.get("cpu_percent", 80)
        mem_limit = thresholds.get("memory_percent", 85)

        try:
            http = self._get_http()
            # CPU使用率
            cpu_query = f'100 - (avg(rate(windows_cpu_time_total{{instance="{instance}",mode="idle"}}[5m])) * 100)'
            resp = await http.get(
                f"{self._metrics_url}/api/v1/query",
                params={"query": cpu_query},
            )
            cpu_data = resp.json()
            cpu_val = float(cpu_data["data"]["result"][0]["value"][1])
            if cpu_val > cpu_limit:
                log.info("Agent %s CPU too high: %.1f%%", agent["id"], cpu_val)
                return False

            # メモリ使用率
            mem_query = f'100 - (windows_os_physical_memory_free_bytes{{instance="{instance}"}} / windows_cs_physical_memory_bytes{{instance="{instance}"}} * 100)'
            resp = await http.get(
                f"{self._metrics_url}/api/v1/query",
                params={"query": mem_query},
            )
            mem_data = resp.json()
            mem_val = float(mem_data["data"]["result"][0]["value"][1])
            if mem_val > mem_limit:
                log.info("Agent %s memory too high: %.1f%%", agent["id"], mem_val)
                return False

            return True
        except Exception as e:
            log.warning("Metrics check failed for %s: %s", agent["id"], e)
            return True  # メトリクス取得失敗時は委託許可

    async def check_version(self, agent: dict) -> bool:
        """バージョンチェック。不一致時は /update を呼ぶ。"""
        import subprocess
        try:
            from src.bot import BASE_DIR
            src_dir = os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR
            local_hash = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=src_dir, text=True
            ).strip()
        except Exception:
            return True

        url = f"http://{agent['host']}:{agent['port']}/version"
        try:
            http = self._get_http()
            resp = await http.get(url, headers={"X-Agent-Token": self._agent_token})
            remote_hash = resp.json().get("version", "")

            if remote_hash == local_hash:
                return True

            log.info("Version mismatch for %s (%s != %s), updating", agent["id"], remote_hash[:8], local_hash[:8])
            update_url = f"http://{agent['host']}:{agent['port']}/update"
            # updateは時間がかかる可能性があるため個別タイムアウト
            await http.post(update_url, headers={"X-Agent-Token": self._agent_token}, timeout=30)
            return True
        except Exception as e:
            log.warning("Version check failed for %s: %s", agent["id"], e)
            return False
