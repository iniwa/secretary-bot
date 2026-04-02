"""ハートビート・コンテキスト圧縮・リマインダースケジュール。"""

import uuid
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.database import JST, jst_now
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
            now = jst_now()
            await self.bot.database.execute(
                "INSERT INTO conversation_summary (summary, created_at) VALUES (?, ?)",
                (summary, now),
            )

            # ChromaDBのconversation_logにも書き込み
            doc_id = uuid.uuid4().hex[:16]
            self.bot.chroma.add(
                "conversation_log", doc_id, summary,
                {"created_at": now.isoformat(), "message_count": len(messages)},
            )
            log.info("Context compacted (SQLite + ChromaDB)")

            # ai_memory抽出（Ollama稼働中のみ）
            conversation_text = "\n".join(texts)
            try:
                from src.memory.ai_memory import AIMemory
                ai_mem = AIMemory(self.bot)
                await ai_mem.extract_and_save(conversation_text)
            except Exception as e:
                log.debug("ai_memory extraction during compact skipped: %s", e)
        except Exception as e:
            log.warning("Context compaction failed: %s", e)

    async def sync_summaries_to_chroma(self) -> None:
        """SQLiteのconversation_summaryをChromaDBに同期（起動時・差分のみ）。"""
        rows = await self.bot.database.fetchall(
            "SELECT id, summary, created_at FROM conversation_summary ORDER BY id"
        )
        if not rows:
            return

        # 既存IDを取得して差分だけ追加
        existing = self.bot.chroma.get_all("conversation_log", limit=10000)
        existing_ids = {item["id"] for item in existing}

        added = 0
        for row in rows:
            doc_id = f"summary_{row['id']}"
            if doc_id not in existing_ids:
                self.bot.chroma.add(
                    "conversation_log", doc_id, row["summary"],
                    {"created_at": str(row["created_at"]), "source": "sqlite_sync"},
                )
                added += 1
        if added:
            log.info("Synced %d new summaries to ChromaDB conversation_log", added)

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

    # --- リマインダースケジュール ---

    def _reminder_job_id(self, reminder_id: int) -> str:
        return f"reminder_{reminder_id}"

    async def _fire_reminder(self, reminder_id: int, message: str, user_id: str) -> None:
        """リマインダー時刻に発火するコールバック。"""
        log.info("Reminder fired: #%d", reminder_id)
        try:
            unit = self.bot.unit_manager.get("reminder")
            if unit:
                actual_unit = getattr(unit, "unit", unit)
                await actual_unit.notify_user(
                    f"リマインド: {message}\n"
                    f"完了したら「リマインダー{reminder_id}番を完了にして」と教えてください。",
                    user_id=user_id,
                )
            await self.bot.database.execute(
                "UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,)
            )
        except Exception as e:
            log.error("Reminder fire failed for #%d: %s", reminder_id, e)

    def schedule_reminder(self, reminder_id: int, remind_at: datetime, message: str, user_id: str) -> None:
        """リマインダーをスケジューラに登録する。過去時刻の場合は即時実行。"""
        job_id = self._reminder_job_id(reminder_id)
        now = datetime.now(JST)

        # タイムゾーン情報がなければJSTとして扱う
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=JST)

        if remind_at <= now:
            # 過去のリマインダーは即時実行
            self.scheduler.add_job(
                self._fire_reminder,
                "date",
                run_date=now,
                args=[reminder_id, message, user_id],
                id=job_id,
                replace_existing=True,
            )
        else:
            self.scheduler.add_job(
                self._fire_reminder,
                "date",
                run_date=remind_at,
                args=[reminder_id, message, user_id],
                id=job_id,
                replace_existing=True,
            )
        log.info("Scheduled reminder #%d at %s", reminder_id, remind_at)

    def cancel_reminder(self, reminder_id: int) -> None:
        """リマインダージョブをキャンセルする。"""
        job_id = self._reminder_job_id(reminder_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            log.info("Cancelled reminder job #%d", reminder_id)

    async def restore_reminders(self) -> None:
        """Bot起動時にDBからアクティブなリマインダーのジョブを復元する。"""
        rows = await self.bot.database.fetchall(
            "SELECT * FROM reminders WHERE active = 1 AND notified = 0"
        )
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["remind_at"])
                self.schedule_reminder(r["id"], dt, r["message"], r.get("user_id", ""))
            except Exception as e:
                log.warning("Failed to restore reminder #%d: %s", r["id"], e)
        if rows:
            log.info("Restored %d reminder jobs", len(rows))

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        log.info("Heartbeat stopped")
