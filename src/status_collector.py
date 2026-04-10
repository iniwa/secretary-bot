"""ステータス収集の一元化。並列I/O + 短期キャッシュ。"""

import asyncio
import time

from src.logger import get_logger

log = get_logger(__name__)

# キャッシュTTL（秒）
_CACHE_TTL = 5.0


class StatusCollector:
    def __init__(self, bot):
        self.bot = bot
        self._cache: dict | None = None
        self._cache_time: float = 0.0
        self._lock = asyncio.Lock()

    async def collect(self, *, use_cache: bool = True) -> dict:
        """全ステータスを並列取得して返す。TTL内はキャッシュを返す。"""
        now = time.monotonic()
        if use_cache and self._cache and (now - self._cache_time) < _CACHE_TTL:
            return self._cache

        async with self._lock:
            # ロック取得後に再チェック（別リクエストがキャッシュ更新済みの場合）
            now = time.monotonic()
            if use_cache and self._cache and (now - self._cache_time) < _CACHE_TTL:
                return self._cache

            result = await self._collect_all()
            self._cache = result
            self._cache_time = time.monotonic()
            return result

    async def _collect_all(self) -> dict:
        from src.bot import get_commit_hash, get_uptime_seconds

        pool = self.bot.unit_manager.agent_pool

        # 全I/Oを並列実行
        agent_tasks = [
            self._check_agent(pool, agent) for agent in pool._agents
        ]
        results = await asyncio.gather(
            asyncio.gather(*agent_tasks),
            self._get_db_stats(),
            self._get_memory_stats(),
            return_exceptions=True,
        )

        agents_result, db_result, memory_result = results

        # エージェント結果
        agents_status = agents_result if not isinstance(agents_result, BaseException) else []

        # DB統計
        db_stats = db_result if not isinstance(db_result, BaseException) else {"conversation_log": -1}

        # メモリ統計
        memory_stats = memory_result if not isinstance(memory_result, BaseException) else {}

        return {
            "version": get_commit_hash(),
            "uptime": int(get_uptime_seconds()),
            "ollama": self.bot.llm_router.ollama_available,
            "agents": agents_status,
            "db": db_stats,
            "memory": memory_stats,
        }

    async def _check_agent(self, pool, agent: dict) -> dict:
        alive = await pool._is_alive(agent)
        aid = agent["id"]
        pause_remaining = pool.get_pause_remaining(aid)
        version = ""
        if alive:
            try:
                url = f"http://{agent['host']}:{agent['port']}/version"
                resp = await pool._get_http().get(
                    url, headers={"X-Agent-Token": pool._agent_token}
                )
                full = (resp.json().get("version") or "").strip()
                version = full[:7] if full else ""
            except Exception:
                pass
        return {
            "id": aid,
            "name": agent.get("name", aid),
            "alive": alive,
            "version": version,
            "mode": pool.get_mode(aid),
            "block_reasons": pool.get_block_reasons(aid),
            "paused": pool.is_paused(aid),
            "pause_remaining": pause_remaining,
        }

    async def _get_db_stats(self) -> dict:
        try:
            row = await self.bot.database.fetchone(
                "SELECT COUNT(*) as cnt FROM conversation_log"
            )
            return {"conversation_log": row["cnt"]}
        except Exception:
            return {"conversation_log": -1}

    async def _get_memory_stats(self) -> dict:
        stats = {}
        for col_name in ["ai_memory", "people_memory"]:
            try:
                stats[col_name] = self.bot.chroma.count(col_name)
            except Exception:
                stats[col_name] = -1
        return stats

    def format_discord(self, status: dict) -> str:
        """Discord向けにフォーマットされたステータス文字列を返す。"""
        lines = ["🖥️ システム状態", "━━━━━━━━━━━━━━━━━━━━"]

        # Ollama
        ollama_ok = status["ollama"]
        lines.append(f"  Ollama:  {'🟢 稼働中' if ollama_ok else '🔴 停止中'}")

        # Windows Agents
        for agent in status["agents"]:
            icon = "🟢 稼働中" if agent["alive"] else "🔴 停止中"
            lines.append(f"  {agent['name']}:  {icon}（{agent['mode']}）")

        lines.append("━━━━━━━━━━━━━━━━━━━━")

        # DB
        db = status.get("db", {})
        log_count = db.get("conversation_log", -1)
        lines.append(f"  会話ログ:      {log_count}件" if log_count >= 0 else "  会話ログ:      取得失敗")

        # Memory
        memory = status.get("memory", {})
        for col_name in ["ai_memory", "people_memory"]:
            count = memory.get(col_name, -1)
            lines.append(f"  {col_name}:  {count}件" if count >= 0 else f"  {col_name}:  取得失敗")

        return "\n".join(lines)
