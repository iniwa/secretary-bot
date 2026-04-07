"""ハートビート・コンテキスト圧縮・リマインダースケジュール。"""

import asyncio
import uuid
from collections import deque
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.database import JST, jst_now
from src.inner_mind.core import InnerMind
from src.logger import get_logger

log = get_logger(__name__)

_MAX_DEBUG_LOGS = 50


class Heartbeat:
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")
        self._job_id = "heartbeat"
        self.debug_logs: deque[dict] = deque(maxlen=_MAX_DEBUG_LOGS)
        self.inner_mind = InnerMind(bot)
        self._think_tick = 0
        self._think_running = False

    @property
    def _config(self) -> dict:
        return self.bot.config.get("heartbeat", {})

    def _get_interval_minutes(self) -> int:
        if self.bot.llm_router.ollama_available:
            return self._config.get("interval_with_ollama_minutes", 15)
        return self._config.get("interval_without_ollama_minutes", 180)

    async def _run_think(self) -> None:
        """inner_mind.think() をバックグラウンドで実行。ハートビートをブロックしない。"""
        try:
            await self.inner_mind.think()
        except Exception as e:
            log.error("InnerMind think failed: %s", e)
        finally:
            self._think_running = False

    async def _tick(self) -> None:
        log.info("Heartbeat tick")
        tick_log = {
            "timestamp": jst_now(),
            "units": [],
            "compact": None,
            "ollama": None,
            "inner_mind": None,
            "next_minutes": None,
            "error": None,
        }
        try:
            # InnerMind 思考サイクル
            self._think_tick += 1
            im_cfg = self.bot.config.get("inner_mind", {})
            if im_cfg.get("enabled", False):
                interval = im_cfg.get("thinking_interval_ticks", 2)
                if self._think_tick % interval == 0:
                    if not self._think_running:
                        self._think_running = True
                        asyncio.create_task(self._run_think())
                        tick_log["inner_mind"] = "launched"
                    else:
                        tick_log["inner_mind"] = "already_running"
                else:
                    tick_log["inner_mind"] = f"waiting ({self._think_tick % interval}/{interval})"
            else:
                tick_log["inner_mind"] = "disabled"

            # 各ユニットの on_heartbeat 呼び出し
            for unit in self.bot.unit_manager.units.values():
                name = getattr(getattr(unit, "unit", unit), "UNIT_NAME", "?")
                try:
                    await unit.on_heartbeat()
                    tick_log["units"].append({"name": name, "ok": True})
                except Exception as e:
                    log.error("Heartbeat error in %s: %s", name, e)
                    tick_log["units"].append({"name": name, "ok": False, "error": str(e)})

            # コンテキスト圧縮チェック
            compact_result = await self._check_compact()
            tick_log["compact"] = compact_result

            # Ollama状態を再チェックして次回間隔を調整
            available = await self.bot.llm_router.check_ollama()
            tick_log["ollama"] = available
        except Exception as e:
            log.error("Heartbeat tick failed: %s", e)
            tick_log["error"] = str(e)
        finally:
            self._reschedule()
            tick_log["next_minutes"] = self._get_interval_minutes()
            self.debug_logs.append(tick_log)

    async def _check_compact(self) -> dict:
        threshold = self._config.get("compact_threshold_messages", 20)

        # 前回コンパクション以降の新規メッセージのみカウント
        last_id_str = await self.bot.database.get_setting("last_compact_msg_id")
        last_id = int(last_id_str) if last_id_str else 0

        if last_id > 0:
            rows = await self.bot.database.fetchall(
                "SELECT id FROM conversation_log WHERE id > ? ORDER BY id DESC LIMIT ?",
                (last_id, threshold + 1),
            )
            new_count = len(rows)
            if new_count < threshold:
                return {"skipped": True, "new_messages": new_count, "threshold": threshold, "reason": "not_enough_new"}

        messages = await self.bot.database.get_recent_messages(limit=threshold + 1)
        msg_count = len(messages)
        if msg_count <= threshold:
            return {"skipped": True, "messages": msg_count, "threshold": threshold}

        log.info("Compacting conversation context (%d messages)", msg_count)
        texts = [f"{m['role']}: {m['content']}" for m in reversed(messages)]
        summary_prompt = (
            "以下の会話履歴を日本語で簡潔に要約してください。重要な情報は残してください。\n"
            "※必ず日本語で出力すること。中国語や英語で書かないこと。\n\n"
            + "\n".join(texts)
        )
        result = {"skipped": False, "messages": msg_count, "threshold": threshold}
        try:
            summary = await self.bot.llm_router.generate(summary_prompt, purpose="memory_extraction")
            result["summary"] = summary[:500]
            now = jst_now()
            await self.bot.database.execute(
                "INSERT INTO conversation_summary (summary, created_at) VALUES (?, ?)",
                (summary, now),
            )

            # ChromaDBのconversation_logにも書き込み
            doc_id = uuid.uuid4().hex[:16]
            self.bot.chroma.add(
                "conversation_log", doc_id, summary,
                {"created_at": now, "message_count": msg_count},
            )
            log.info("Context compacted (SQLite + ChromaDB)")
            result["saved"] = True

            # 処理済みの最新メッセージIDを記録（次回の重複防止）
            max_id = max(m["id"] for m in messages)
            await self.bot.database.set_setting("last_compact_msg_id", str(max_id))

            # ai_memory抽出（Ollama稼働中のみ）
            conversation_text = "\n".join(texts)
            try:
                from src.memory.ai_memory import AIMemory
                ai_mem = AIMemory(self.bot)
                await ai_mem.extract_and_save(conversation_text)
                result["ai_memory"] = True
            except Exception as e:
                log.debug("ai_memory extraction during compact skipped: %s", e)
                result["ai_memory"] = False
                result["ai_memory_error"] = str(e)
        except Exception as e:
            log.warning("Context compaction failed: %s", e)
            result["error"] = str(e)
        return result

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

    # --- 天気通知スケジュール ---

    def _weather_job_id(self, sub_id: int) -> str:
        return f"weather_{sub_id}"

    async def _fire_daily_weather(self, sub_id: int, user_id: str, lat: float, lon: float, location: str) -> None:
        """毎朝の天気通知を発火するコールバック。"""
        log.info("Daily weather fired: #%d (%s)", sub_id, location)
        try:
            unit = self.bot.unit_manager.get("weather")
            if unit:
                actual_unit = getattr(unit, "unit", unit)
                message = await actual_unit.build_daily_notification(lat, lon, location)
                await actual_unit.notify_user(message, user_id=user_id)
        except Exception as e:
            log.error("Daily weather fire failed for #%d: %s", sub_id, e)

    def schedule_weather_daily(self, sub_id: int, hour: int, minute: int, user_id: str, lat: float, lon: float, location: str) -> None:
        """天気通知をcronジョブとしてスケジューラに登録する。"""
        job_id = self._weather_job_id(sub_id)
        self.scheduler.add_job(
            self._fire_daily_weather,
            "cron",
            hour=hour,
            minute=minute,
            args=[sub_id, user_id, lat, lon, location],
            id=job_id,
            replace_existing=True,
        )
        log.info("Scheduled daily weather #%d at %02d:%02d for %s", sub_id, hour, minute, location)

    def cancel_weather_daily(self, sub_id: int) -> None:
        """天気通知ジョブをキャンセルする。"""
        job_id = self._weather_job_id(sub_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            log.info("Cancelled weather job #%d", sub_id)

    async def restore_weather_subscriptions(self) -> None:
        """Bot起動時にDBからアクティブな天気通知のジョブを復元する。"""
        rows = await self.bot.database.fetchall(
            "SELECT * FROM weather_subscriptions WHERE active = 1"
        )
        for r in rows:
            try:
                self.schedule_weather_daily(
                    r["id"], r["notify_hour"], r["notify_minute"],
                    r["user_id"], r["latitude"], r["longitude"], r["location"],
                )
            except Exception as e:
                log.warning("Failed to restore weather sub #%d: %s", r["id"], e)
        if rows:
            log.info("Restored %d weather subscription jobs", len(rows))

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        log.info("Heartbeat stopped")
