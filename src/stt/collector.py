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
        except Exception as e:
            log.warning("STT transcript collection failed: %s", e)
            return 0

        transcripts = data.get("transcripts", [])
        if not transcripts:
            return 0

        saved = 0
        for t in transcripts:
            text = t.get("text", "").strip()
            if not text:
                continue
            await self.bot.database.execute(
                "INSERT INTO stt_transcripts (raw_text, started_at, ended_at, duration_seconds, collected_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (text, t.get("started_at", ""), t.get("ended_at", ""),
                 t.get("duration_seconds"), jst_now()),
            )
            saved += 1
            ts = t.get("created_at", "")
            if ts and (not self._last_ts or ts > self._last_ts):
                self._last_ts = ts

        if saved:
            log.info("Collected %d STT transcripts", saved)
        return saved
