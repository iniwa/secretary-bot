"""Sub PC Agent から transcript を定期収集する。"""

import os

import httpx

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)


class STTCollector:
    """Sub PC の /stt/transcripts から新規テキストを収集し、DBに保存する。"""

    def __init__(self, bot):
        self.bot = bot
        self._last_ts: str | None = None
        self._consecutive_failures: int = 0

    async def collect(self) -> int:
        """新規 transcript を収集し保存する。保存件数を返す。"""
        pool = getattr(getattr(self.bot, "unit_manager", None), "agent_pool", None)
        if not pool:
            return 0

        # Sub PC エージェントを探す
        agent = None
        for a in pool._agents:
            if a.get("role") == "sub":
                agent = a
                break
        if not agent:
            return 0

        url = f"http://{agent['host']}:{agent['port']}/stt/transcripts"
        if self._last_ts:
            url += f"?since={self._last_ts}"
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                data = resp.json()
            if self._consecutive_failures > 0:
                log.info("STT collector recovered after %d failures", self._consecutive_failures)
                self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            if self._consecutive_failures == 1:
                log.warning("STT transcript collection failed: %s", e)
            else:
                log.debug("STT transcript collection still failing (%d): %s", self._consecutive_failures, e)
            return 0

        transcripts = data.get("transcripts", [])
        if not transcripts:
            return 0

        saved = 0
        for t in transcripts:
            text = t.get("text", "").strip()
            if len(text) < 2:
                continue
            started = t.get("started_at", "")
            # 重複チェック（同じ started_at が既にあればスキップ）
            if started:
                existing = await self.bot.database.fetchone(
                    "SELECT id FROM stt_transcripts WHERE started_at = ?", (started,)
                )
                if existing:
                    continue
            await self.bot.database.execute(
                "INSERT INTO stt_transcripts (raw_text, started_at, ended_at, duration_seconds, collected_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (text, started, t.get("ended_at", ""),
                 t.get("duration_seconds"), jst_now()),
            )
            saved += 1
            ts = t.get("created_at", "")
            if ts and (not self._last_ts or ts > self._last_ts):
                self._last_ts = ts

        if saved:
            log.info("Collected %d STT transcripts", saved)
        return saved
