"""複数Windows PCの管理・フォールバック。"""

import asyncio
import os
import time

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
        # ActivityDetector への参照
        self._activity_detector = None
        # 一時停止: agent_id → Unix timestamp（終了時刻）
        self._paused_until: dict[str, float] = {}
        # 最後の select_agent 時のブロック理由キャッシュ
        self._block_reasons: dict[str, list[str]] = {}

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

    def set_activity_detector(self, detector) -> None:
        """ActivityDetector への参照をセット（bot.py 起動時に呼ばれる）。"""
        self._activity_detector = detector

    def pause_agent(self, agent_id: str, seconds: int) -> None:
        """指定エージェントを seconds 秒間一時停止する。"""
        self._paused_until[agent_id] = time.time() + seconds

    def unpause_agent(self, agent_id: str) -> None:
        """一時停止を解除する。"""
        self._paused_until.pop(agent_id, None)

    def is_paused(self, agent_id: str) -> bool:
        """一時停止中か判定。期限切れなら自動クリーンアップ。"""
        until = self._paused_until.get(agent_id)
        if until is None:
            return False
        if time.time() >= until:
            self._paused_until.pop(agent_id, None)
            return False
        return True

    def get_pause_remaining(self, agent_id: str) -> int | None:
        """一時停止の残り秒数を返す。停止中でなければ None。"""
        until = self._paused_until.get(agent_id)
        if until is None:
            return None
        remaining = int(until - time.time())
        if remaining <= 0:
            self._paused_until.pop(agent_id, None)
            return None
        return remaining

    def get_block_reasons(self, agent_id: str) -> list[str]:
        """最後の select_agent 時に記録されたブロック理由を返す。"""
        return list(self._block_reasons.get(agent_id, []))

    async def select_agent(self, preferred: str | None = None) -> dict | None:
        agents = list(self._agents)
        if preferred:
            agents.sort(key=lambda a: 0 if a["id"] == preferred else 1)

        self._block_reasons = {}
        for agent in agents:
            aid = agent["id"]
            reasons = []
            mode = self.get_mode(aid)
            if mode == "deny":
                continue
            if self.is_paused(aid):
                reasons.append("一時停止中")
                self._block_reasons[aid] = reasons
                continue
            if not await self._is_alive(agent):
                reasons.append("オフライン")
                self._block_reasons[aid] = reasons
                continue
            if mode == "auto":
                idle, idle_reason = await self._is_idle_detailed(agent)
                if not idle:
                    reasons.append(idle_reason)
                activity_ok, activity_reason = await self._is_activity_ok(agent)
                if not activity_ok:
                    reasons.append(activity_reason)
                if reasons:
                    self._block_reasons[aid] = reasons
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
        """後方互換ラッパー。"""
        result, _ = await self._is_idle_detailed(agent)
        return result

    async def _is_idle_detailed(self, agent: dict) -> tuple[bool, str | None]:
        """VictoriaMetrics APIでCPU・メモリ・GPU使用率を確認。"""
        if not self._metrics_url:
            return True, None  # メトリクスなしなら委託許可

        instance = agent.get("metrics_instance", "")
        if not instance:
            return True, None

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
                return False, "CPU高負荷"

            # メモリ使用率
            mem_query = f'100 - (windows_memory_physical_free_bytes{{instance="{instance}"}} / windows_memory_physical_total_bytes{{instance="{instance}"}} * 100)'
            resp = await http.get(
                f"{self._metrics_url}/api/v1/query",
                params={"query": mem_query},
            )
            mem_data = resp.json()
            mem_val = float(mem_data["data"]["result"][0]["value"][1])
            if mem_val > mem_limit:
                log.info("Agent %s memory too high: %.1f%%", agent["id"], mem_val)
                return False, "メモリ高負荷"

            # GPU使用率（nvidia_smi_exporter 想定）
            gpu_limit = thresholds.get("gpu_percent", 80)
            if gpu_limit > 0:
                try:
                    gpu_query = f'nvidia_smi_utilization_gpu_ratio{{instance="{instance}"}} * 100'
                    resp = await http.get(
                        f"{self._metrics_url}/api/v1/query",
                        params={"query": gpu_query},
                    )
                    gpu_data = resp.json()
                    results = gpu_data.get("data", {}).get("result", [])
                    if results:
                        gpu_val = float(results[0]["value"][1])
                        if gpu_val > gpu_limit:
                            log.info("Agent %s GPU too high: %.1f%%", agent["id"], gpu_val)
                            return False, "GPU高負荷"
                except Exception:
                    pass  # GPU メトリクス未設定時は無視

            return True, None
        except Exception as e:
            log.warning("Metrics check failed for %s: %s", agent["id"], e)
            return True, None  # メトリクス取得失敗時は委託許可

    async def _is_activity_ok(self, agent: dict) -> tuple[bool, str | None]:
        """アクティビティ検出によるブロック判定。"""
        if not self._activity_detector:
            return True, None

        try:
            status = await self._activity_detector.get_status()
        except Exception:
            return True, None  # 取得失敗時は許可

        role = agent.get("role", "")
        rules = self._activity_detector._block_rules

        if role == "main":
            gaming = status.get("gaming", {})
            if gaming.get("active") and rules.get("gaming_on_main", False):
                game_name = gaming.get("game", "不明")
                return False, f"ゲーム中({game_name})"
        elif role == "sub":
            if status.get("obs_streaming") and rules.get("obs_streaming", True):
                return False, "OBS配信中"
            if status.get("obs_recording") and rules.get("obs_recording", True):
                return False, "OBS録画中"
            if status.get("obs_replay_buffer") and rules.get("obs_replay_buffer", False):
                return False, "OBSリプレイバッファ"

        if status.get("discord_vc") and rules.get("discord_vc", False):
            return False, "Discord VC接続中"

        return True, None

    async def get_checks(self, agent: dict) -> list[dict]:
        """エージェントの全委託条件を個別に評価し、結果リストを返す。"""
        aid = agent["id"]
        checks = []

        # 1. オンライン
        alive = await self._is_alive(agent)
        checks.append({"name": "オンライン", "ok": alive, "detail": ""})
        if not alive:
            return checks  # オフラインなら以降のチェック不要

        # 2. 一時停止
        paused = self.is_paused(aid)
        remain = self.get_pause_remaining(aid)
        detail = f"残り{remain // 60}分" if remain else ""
        checks.append({"name": "一時停止", "ok": not paused, "detail": detail})

        # 3. モード
        mode = self.get_mode(aid)
        checks.append({"name": "委託モード", "ok": mode != "deny", "detail": mode})

        # 4-6. メトリクス（CPU/メモリ/GPU）— 並列取得
        instance = agent.get("metrics_instance", "")
        if self._metrics_url and instance:
            thresholds = self._delegation_config.get("thresholds", {})
            http = self._get_http()
            cpu_limit = thresholds.get("cpu_percent", 80)
            mem_limit = thresholds.get("memory_percent", 85)
            gpu_limit = thresholds.get("gpu_percent", 80)

            async def _query_cpu():
                try:
                    q = f'100 - (avg(rate(windows_cpu_time_total{{instance="{instance}",mode="idle"}}[5m])) * 100)'
                    resp = await http.get(f"{self._metrics_url}/api/v1/query", params={"query": q})
                    val = float(resp.json()["data"]["result"][0]["value"][1])
                    return {"name": "CPU使用率", "ok": val <= cpu_limit, "detail": f"{val:.1f}% (上限{cpu_limit}%)"}
                except Exception:
                    return {"name": "CPU使用率", "ok": True, "detail": "取得失敗"}

            async def _query_mem():
                try:
                    q = f'100 - (windows_memory_physical_free_bytes{{instance="{instance}"}} / windows_memory_physical_total_bytes{{instance="{instance}"}} * 100)'
                    resp = await http.get(f"{self._metrics_url}/api/v1/query", params={"query": q})
                    val = float(resp.json()["data"]["result"][0]["value"][1])
                    return {"name": "メモリ使用率", "ok": val <= mem_limit, "detail": f"{val:.1f}% (上限{mem_limit}%)"}
                except Exception:
                    return {"name": "メモリ使用率", "ok": True, "detail": "取得失敗"}

            async def _query_gpu():
                if gpu_limit <= 0:
                    return {"name": "GPU使用率", "ok": True, "detail": "チェック無効"}
                try:
                    q = f'nvidia_smi_utilization_gpu_ratio{{instance="{instance}"}} * 100'
                    resp = await http.get(f"{self._metrics_url}/api/v1/query", params={"query": q})
                    results = resp.json().get("data", {}).get("result", [])
                    if results:
                        val = float(results[0]["value"][1])
                        return {"name": "GPU使用率", "ok": val <= gpu_limit, "detail": f"{val:.1f}% (上限{gpu_limit}%)"}
                    return {"name": "GPU使用率", "ok": True, "detail": "exporter未導入"}
                except Exception:
                    return {"name": "GPU使用率", "ok": True, "detail": "取得失敗"}

            cpu_r, mem_r, gpu_r = await asyncio.gather(_query_cpu(), _query_mem(), _query_gpu())
            checks.extend([cpu_r, mem_r, gpu_r])
        else:
            checks.append({"name": "CPU使用率", "ok": True, "detail": "メトリクス未設定"})
            checks.append({"name": "メモリ使用率", "ok": True, "detail": "メトリクス未設定"})
            checks.append({"name": "GPU使用率", "ok": True, "detail": "メトリクス未設定"})

        # 7-8. アクティビティ
        if self._activity_detector:
            try:
                status = await self._activity_detector.get_status()
                role = agent.get("role", "")
                rules = self._activity_detector._block_rules

                if role == "main" and rules.get("gaming_on_main", False):
                    gaming = status.get("gaming", {})
                    game_active = gaming.get("active", False)
                    detail = gaming.get("game", "") if game_active else ""
                    checks.append({"name": "ゲーム検出", "ok": not game_active, "detail": detail})
                elif role == "sub":
                    if rules.get("obs_streaming", True) or rules.get("obs_recording", True) or rules.get("obs_replay_buffer", False):
                        obs_stream = status.get("obs_streaming", False) and rules.get("obs_streaming", True)
                        obs_rec = status.get("obs_recording", False) and rules.get("obs_recording", True)
                        obs_replay = status.get("obs_replay_buffer", False) and rules.get("obs_replay_buffer", False)
                        obs_ng = obs_stream or obs_rec or obs_replay
                        obs_detail = ""
                        if obs_stream: obs_detail = "配信中"
                        elif obs_rec: obs_detail = "録画中"
                        elif obs_replay: obs_detail = "リプレイバッファ"
                        checks.append({"name": "OBS", "ok": not obs_ng, "detail": obs_detail})

                if rules.get("discord_vc", False):
                    vc = status.get("discord_vc", False)
                    checks.append({"name": "Discord VC", "ok": not vc, "detail": ""})
            except Exception:
                checks.append({"name": "アクティビティ", "ok": True, "detail": "取得失敗"})

        return checks

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
