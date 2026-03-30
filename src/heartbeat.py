"""ハートビート・コンテキスト圧縮。"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)


class Heartbeat:
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")
        self._job_id = "heartbeat"

    @property
    def _config(self) -> dict:
        return self.bot.config.get("heartbeat", {})

    def _get_interval_minutes(self) -> int:
        if self.bot.llm_router.ollama_available:
            return self._config.get("interval_with_ollama_minutes", 15)
        return self._config.get("interval_without_ollama_minutes", 180)

    async def _tick(self) -> None:
        log.info("Heartbeat tick")
        try:
            # 各ユニットの on_heartbeat 呼び出し
            for unit in self.bot.unit_manager.units.values():
                try:
                    await unit.on_heartbeat()
                except Exception as e:
                    log.error("Heartbeat error in %s: %s", unit.UNIT_NAME, e)

            # コンテキスト圧縮チェック
            await self._check_compact()

            # Ollama状態を再チェックして次回間隔を調整
            await self.bot.llm_router.check_ollama()
        except Exception as e:
            log.error("Heartbeat tick failed: %s", e)
        finally:
            self._reschedule()

    async def _check_compact(self) -> None:
        threshold = self._config.get("compact_threshold_messages", 20)
        messages = await self.bot.database.get_recent_messages(limit=threshold + 1)
        if len(messages) <= threshold:
            return

        log.info("Compacting conversation context (%d messages)", len(messages))
        texts = [f"{m['role']}: {m['content']}" for m in reversed(messages)]
        summary_prompt = (
            "以下の会話履歴を簡潔に要約してください。重要な情報は残してください。\n\n"
            + "\n".join(texts)
        )
        try:
            summary = await self.bot.llm_router.generate(summary_prompt, purpose="memory_extraction")
            await self.bot.database.execute(
                "INSERT INTO conversation_summary (summary, created_at) VALUES (?, ?)",
                (summary, jst_now()),
            )
            log.info("Context compacted")
        except Exception as e:
            log.warning("Context compaction failed: %s", e)

    def _reschedule(self) -> None:
        minutes = self._get_interval_minutes()
        if self.scheduler.get_job(self._job_id):
            self.scheduler.remove_job(self._job_id)
        self.scheduler.add_job(
            self._tick,
            "interval",
            minutes=minutes,
            id=self._job_id,
            replace_existing=True,
        )
        log.info("Next heartbeat in %d minutes", minutes)

    def start(self) -> None:
        self._reschedule()
        if not self.scheduler.running:
            self.scheduler.start()
        log.info("Heartbeat started")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        log.info("Heartbeat stopped")
