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
        self._rss_last_fetch: datetime | None = None
        self._rss_digest_sent_today: str | None = None
        self._stt_collector = None  # 遅延初期化（_last_tsを保持するため）
        self._stt_cleanup_last_date: str | None = None  # 1日1回のretention実行を制御
        self._activity_daily_last_date: str | None = None  # 1日1回の活動要約実行を制御
        self._memory_sweep_last_date: str | None = None  # 1日1回のChromaDBメモリsweepを制御
        self._calendar_last_sync: datetime | None = None  # カレンダー同期の間隔制御

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

    _TICK_TIMEOUT = 600  # 10分でタイムアウト

    async def _tick(self) -> None:
        try:
            await asyncio.wait_for(self._tick_inner(), timeout=self._TICK_TIMEOUT)
        except asyncio.TimeoutError:
            log.error("Heartbeat tick timed out after %ds", self._TICK_TIMEOUT)
        except Exception as e:
            log.error("Heartbeat tick unexpected error: %s", e)

    async def _tick_inner(self) -> None:
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

            # スヌーズ済みリマインダーの再通知
            await self._check_snooze_reminders()

            # 各ユニットの on_heartbeat + STT + RSS を並列実行
            units = list(self.bot.unit_manager.units.values())
            unit_names = [
                getattr(getattr(u, "unit", u), "UNIT_NAME", "?") for u in units
            ]

            async def _safe_heartbeat(unit, name):
                try:
                    await unit.on_heartbeat()
                    return {"name": name, "ok": True}
                except Exception as e:
                    log.error("Heartbeat error in %s: %s", name, e)
                    return {"name": name, "ok": False, "error": str(e)}

            # 全タスクを並列実行（ユニット群 + STT + RSS + コンテキスト圧縮）
            tasks = [_safe_heartbeat(u, n) for u, n in zip(units, unit_names)]
            tasks.append(self._run_stt())
            tasks.append(self._run_rss())
            tasks.append(self._check_compact())
            tasks.append(self._run_activity())
            tasks.append(self._run_memory_sweep())
            tasks.append(self._run_calendar_sync())
            tasks.append(self._run_context_sources_update())

            all_results = await asyncio.gather(*tasks, return_exceptions=True)

            # 結果を分解: ユニット結果 | STT | RSS | Compact | Activity | MemorySweep | CalendarSync | SourcesUpdate
            n_units = len(units)
            for i, r in enumerate(all_results[:n_units]):
                if isinstance(r, Exception):
                    tick_log["units"].append({"name": unit_names[i], "ok": False, "error": str(r)})
                else:
                    tick_log["units"].append(r)

            stt_result = all_results[n_units] if not isinstance(all_results[n_units], Exception) else {"error": str(all_results[n_units])}
            rss_result = all_results[n_units + 1] if not isinstance(all_results[n_units + 1], Exception) else {"error": str(all_results[n_units + 1])}
            compact_result = all_results[n_units + 2] if not isinstance(all_results[n_units + 2], Exception) else {"error": str(all_results[n_units + 2])}
            activity_result = all_results[n_units + 3] if not isinstance(all_results[n_units + 3], Exception) else {"error": str(all_results[n_units + 3])}
            memory_sweep_result = all_results[n_units + 4] if not isinstance(all_results[n_units + 4], Exception) else {"error": str(all_results[n_units + 4])}
            calendar_result = all_results[n_units + 5] if not isinstance(all_results[n_units + 5], Exception) else {"error": str(all_results[n_units + 5])}
            sources_update_result = all_results[n_units + 6] if not isinstance(all_results[n_units + 6], Exception) else {"error": str(all_results[n_units + 6])}
            tick_log["stt"] = stt_result
            tick_log["rss"] = rss_result
            tick_log["compact"] = compact_result
            tick_log["activity"] = activity_result
            tick_log["memory_sweep"] = memory_sweep_result
            tick_log["calendar"] = calendar_result
            tick_log["sources_update"] = sources_update_result

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

    # --- STT 収集・要約 ---

    async def _run_stt(self) -> dict:
        """Sub PC から transcript を収集し、閾値を超えたら LLM 要約する。"""
        result = {"collected": 0, "summarized": False}
        stt_cfg = self.bot.config.get("stt", {})
        if not stt_cfg.get("enabled", False):
            return result
        try:
            if self._stt_collector is None:
                from src.stt.collector import STTCollector
                self._stt_collector = STTCollector(self.bot)
            count = await self._stt_collector.collect()
            result["collected"] = count
        except Exception as e:
            log.warning("STT collection failed: %s", e)
            result["error"] = str(e)
            return result

        try:
            from src.stt.processor import STTProcessor
            processor = STTProcessor(self.bot)
            did_process = await processor.process()
            result["summarized"] = did_process
        except Exception as e:
            log.warning("STT processing failed: %s", e)
            result["summary_error"] = str(e)

        # 1日1回のretentionクリーンアップ（要約済みで古いtranscriptsを削除）
        today_str = datetime.now(tz=JST).strftime("%Y-%m-%d")
        if self._stt_cleanup_last_date != today_str:
            try:
                from src.stt.processor import STTProcessor as _P
                await _P(self.bot).cleanup_old_transcripts()
                self._stt_cleanup_last_date = today_str
            except Exception as e:
                log.warning("STT cleanup failed: %s", e)
        return result

    # --- Main PC アクティビティ取得・日次要約 ---

    async def _run_activity(self) -> dict:
        """ポーリングは独立タスクなのでここでは実行しない。
        1日1回のサンプルretentionと日次要約だけを担当。"""
        result: dict = {}
        collector = getattr(self.bot, "activity_collector", None)
        if collector is None:
            return result

        today_str = datetime.now(tz=JST).strftime("%Y-%m-%d")
        if self._activity_daily_last_date != today_str:
            try:
                await collector.cleanup_old_samples()
                from src.activity.daily_summary import run_daily_summary
                did = await run_daily_summary(self.bot)
                result["daily_summary"] = did
                self._activity_daily_last_date = today_str
            except Exception as e:
                log.warning("activity daily job failed: %s", e)
                result["daily_error"] = str(e)
        return result

    # --- ChromaDBメモリ日次sweep ---

    async def _run_memory_sweep(self) -> dict:
        """1日1回、古くてヒットのないメモリエントリをsweepする。"""
        result: dict = {}
        mem_cfg = self.bot.config.get("memory", {})
        if not mem_cfg.get("sweep_enabled", True):
            return {"skipped": "disabled"}

        today_str = datetime.now(tz=JST).strftime("%Y-%m-%d")
        if self._memory_sweep_last_date == today_str:
            return result
        try:
            from src.memory.sweeper import run_memory_sweep
            deleted = await run_memory_sweep(self.bot)
            result["deleted"] = deleted
            self._memory_sweep_last_date = today_str
        except Exception as e:
            log.warning("memory sweep failed: %s", e)
            result["error"] = str(e)
        return result

    # --- InnerMind ContextSource 背景更新 ---

    async def _run_context_sources_update(self) -> dict:
        """各ContextSourceの背景更新（LLM要約等の重い処理）を実行。
        InnerMind.think()時にはキャッシュから軽量に読むだけにする。"""
        if not self.bot.llm_router.ollama_available:
            return {"skipped": "ollama_unavailable"}
        try:
            await self.inner_mind.registry.update_all()
            return {"ok": True}
        except Exception as e:
            log.warning("ContextSource update failed: %s", e)
            return {"error": str(e)}

    # --- カレンダー同期 ---

    async def _run_calendar_sync(self) -> dict:
        """読み取り対象カレンダーから予定を取得し calendar_events にキャッシュ。"""
        cal_cfg = self.bot.config.get("calendar", {}).get("read_sync", {})
        if not cal_cfg.get("enabled", False):
            return {}

        interval = int(cal_cfg.get("sync_interval_minutes", 10))
        now = datetime.now(JST)
        if (
            self._calendar_last_sync is not None
            and (now - self._calendar_last_sync).total_seconds() < interval * 60
        ):
            return {}

        try:
            from src.gcal.sync import sync_all_sources
            lookahead = int(cal_cfg.get("lookahead_days", 60))
            result = await sync_all_sources(self.bot, lookahead_days=lookahead)
            self._calendar_last_sync = now
            return result
        except Exception as e:
            log.warning("Calendar sync failed: %s", e)
            return {"error": str(e)}

    # --- RSS 定期フェッチ・要約・ダイジェスト通知 ---

    async def _run_rss(self) -> dict:
        """RSS フェッチ（間隔制御）、記事要約、ダイジェスト通知を実行する。"""
        result = {}
        rss_cfg = self.bot.config.get("rss", {})
        if not rss_cfg:
            return result

        now = datetime.now(JST)

        # 定期フェッチ（fetch_interval_minutes ごと）
        interval = rss_cfg.get("fetch_interval_minutes", 60)
        should_fetch = (
            self._rss_last_fetch is None
            or (now - self._rss_last_fetch).total_seconds() >= interval * 60
        )
        if should_fetch:
            try:
                from src.rss.fetcher import RSSFetcher
                fetcher = RSSFetcher(self.bot)
                fetch_result = await fetcher.fetch_all_feeds()
                result["fetch"] = fetch_result
                self._rss_last_fetch = now
            except Exception as e:
                log.warning("RSS fetch failed: %s", e)
                result["fetch_error"] = str(e)

        # 記事要約（Ollama利用可能時のみ）
        if self.bot.llm_router.ollama_available:
            try:
                from src.rss.processor import RSSProcessor
                processor = RSSProcessor(self.bot)
                count = await processor.summarize_unsummarized(limit=10)
                if count:
                    result["summarized"] = count
            except Exception as e:
                log.warning("RSS summarize failed: %s", e)

        # ダイジェスト通知（毎日 digest_hour 時に1回）
        digest_hour = rss_cfg.get("digest_hour", 9)
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == digest_hour and self._rss_digest_sent_today != today_str:
            try:
                import os
                from src.rss.recommender import RSSRecommender
                from src.rss.notify import RSSNotifier
                admin_user_id = os.environ.get("WEBGUI_USER_ID", "")
                recommender = RSSRecommender(self.bot)
                digest = await recommender.get_digest(admin_user_id)
                if digest and any(b["articles"] for b in digest):
                    notifier = RSSNotifier(self.bot)
                    sent = await notifier.send_digest(digest, admin_user_id)
                    result["digest_sent"] = sent
                    if sent:
                        self._rss_digest_sent_today = today_str
            except Exception as e:
                log.warning("RSS digest failed: %s", e)
                result["digest_error"] = str(e)

        return result

    _SNOOZE_INTERVALS_MINUTES = [30, 60, 180, 360]

    async def _check_snooze_reminders(self) -> None:
        """通知済み未完了リマインダーのスヌーズ再通知を処理する。"""
        rows = await self.bot.database.fetchall(
            "SELECT * FROM reminders WHERE active = 1 AND notified = 1"
        )
        now = datetime.now(JST)
        for r in rows:
            try:
                snoozed_until = r.get("snoozed_until")
                if snoozed_until:
                    until_dt = datetime.fromisoformat(snoozed_until)
                    if until_dt.tzinfo is None:
                        until_dt = until_dt.replace(tzinfo=JST)
                    if now < until_dt:
                        continue
                    # 明示的スヌーズ期限到達 → 再通知してクリア
                    await self._send_snooze_notification(r)
                    await self.bot.database.execute(
                        "UPDATE reminders SET snoozed_until = NULL WHERE id = ?",
                        (r["id"],),
                    )
                    continue

                # エスカレーション間隔によるスヌーズ
                snooze_count = r.get("snooze_count", 0)
                last_snoozed = r.get("last_snoozed_at")
                if last_snoozed:
                    last_dt = datetime.fromisoformat(last_snoozed)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=JST)
                else:
                    # 初回スヌーズ: notified直後 → remind_at を基準に
                    last_dt = datetime.fromisoformat(r["remind_at"])
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=JST)

                idx = min(snooze_count, len(self._SNOOZE_INTERVALS_MINUTES) - 1)
                interval_minutes = self._SNOOZE_INTERVALS_MINUTES[idx]
                if (now - last_dt).total_seconds() < interval_minutes * 60:
                    continue

                await self._send_snooze_notification(r)
                await self.bot.database.execute(
                    "UPDATE reminders SET snooze_count = ?, last_snoozed_at = ? WHERE id = ?",
                    (snooze_count + 1, now.isoformat(), r["id"]),
                )
            except Exception as e:
                log.error("Snooze check failed for reminder #%d: %s", r["id"], e)

    async def _send_snooze_notification(self, reminder: dict) -> None:
        """スヌーズ再通知を送信する。"""
        unit = self.bot.unit_manager.get("reminder")
        if unit:
            actual_unit = getattr(unit, "unit", unit)
            await actual_unit.notify_user(
                f"まだ未完了: {reminder['message']}\n終わったら教えてね！",
                user_id=reminder.get("user_id", ""),
            )

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

            # ai_memory + people_memory を並列抽出（両者は独立）
            conversation_text = "\n".join(texts)

            async def _extract_ai_memory():
                from src.memory.ai_memory import AIMemory
                ai_mem = AIMemory(self.bot)
                await ai_mem.extract_and_save(conversation_text)

            async def _extract_people_memory():
                from src.memory.people_memory import PeopleMemory
                people_mem = PeopleMemory(self.bot)
                user_messages = [m for m in messages if m["role"] == "user"]
                user_ids = {m.get("user_id", "") for m in user_messages if m.get("user_id")}
                for uid in user_ids:
                    user_texts = [f"user: {m['content']}" for m in user_messages if m.get("user_id") == uid]
                    if user_texts:
                        await people_mem.extract_and_save("\n".join(user_texts), user_id=uid)

            ai_res, ppl_res = await asyncio.gather(
                _extract_ai_memory(),
                _extract_people_memory(),
                return_exceptions=True,
            )
            if isinstance(ai_res, Exception):
                log.debug("ai_memory extraction during compact skipped: %s", ai_res)
                result["ai_memory"] = False
                result["ai_memory_error"] = str(ai_res)
            else:
                result["ai_memory"] = True
            if isinstance(ppl_res, Exception):
                log.debug("people_memory extraction during compact skipped: %s", ppl_res)
                result["people_memory"] = False
            else:
                result["people_memory"] = True
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
                    f"リマインド: {message}\n終わったら教えてね！",
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
