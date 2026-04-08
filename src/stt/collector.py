"""Main PC Agent から transcript を定期収集する。"""

from datetime import datetime

from src.database import JST, jst_now
from src.logger import get_logger

log = get_logger(__name__)


class STTCollector:
    """Main PC の /stt/transcripts から新規テキストを収集し、DBに保存する。"""

    def __init__(self, bot):
        self.bot = bot
        self._last_ts: str | None = None

    async def collect(self) -> int:
        """新規 transcript を収集し保存する。保存件数を返す。"""
        pool = self.bot.agent_pool
        agent = pool.get_agent_by_role("main") if pool else None
        if not agent:
            return 0

        params = {}
        if self._last_ts:
            params["since"] = self._last_ts

        try:
            data = await agent.get("/stt/transcripts", params=params)
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
