"""Main/Sub PC の /activity を定期取得し、生サンプルとセッションを記録する。

3層のセッション管理:
- game_sessions: psutil 由来の game フィールドで継続。フォアグラウンドが裏ブラウザに切り替わっても壊れない（Main のみ）
- foreground_sessions: GetForegroundWindow 由来で切替ごとに区切る。during_game=1 ならゲーム中の寄り道（Main/Sub 両方）
- obs_sessions: Sub PC の OBS WebSocket 状態（streaming/recording/replay_buffer）で区切る

STT flush トリガー:
- ゲーム切り替わり（開始 / 別ゲーム / 3分遅延後の確定終了）時に未要約 transcript を強制要約
- 長時間同一 FG からの切り替わりも同様

サンプリング方針:
- 独立したasyncioタスクで poll_interval_seconds（デフォルト60秒）ごとに /activity を Main/Sub 並列取得
- agent 未応答時はスキップ（ログ抑制、連続失敗時のみINFO）。Main/Sub 独立に失敗カウント
- activity_samples は監査用、sample_retention_days（デフォルト7）で定期削除
- game_sessions / foreground_sessions / obs_sessions は永続（retention なし）
"""

import asyncio
from datetime import datetime, timedelta, timezone

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))

# foreground/activity を記録する PC 識別子
_PCS = ("main", "sub")

# OBS セッション種別（Sub PC の /activity レスポンスのキーと対応）
_OBS_KINDS = ("streaming", "recording", "replay_buffer")


class ActivityCollector:
    def __init__(self, bot):
        self.bot = bot
        # PC 別セッション状態（game は Main のみ実用）
        self._cur_game: dict[str, str | None] = {pc: None for pc in _PCS}
        self._cur_game_session_id: dict[str, int | None] = {pc: None for pc in _PCS}
        self._cur_fg: dict[str, str | None] = {pc: None for pc in _PCS}
        self._cur_fg_session_id: dict[str, int | None] = {pc: None for pc in _PCS}
        # OBS セッション状態（Sub PC 側）
        self._cur_obs: dict[str, bool] = {k: False for k in _OBS_KINDS}
        self._cur_obs_session_id: dict[str, int | None] = {k: None for k in _OBS_KINDS}
        # ゲーム終了の遅延クローズ（Main のみ）
        self._pending_close_task: asyncio.Task | None = None
        self._pending_close_game: str | None = None

        cfg = bot.config.get("activity", {})
        self._poll_interval = max(10, int(cfg.get("poll_interval_seconds", 60)))
        self._retention_days = int(cfg.get("sample_retention_days", 7))
        # 連続失敗がこの回数に達したら進行中セッションを _last_alive_ts で close する
        self._unreachable_close_polls = max(2, int(cfg.get("unreachable_close_polls", 3)))
        # ゲーム終了→正式クローズまでの猶予秒数（再起動吸収用）
        self._game_end_close_delay = max(0, int(cfg.get("game_end_close_delay_seconds", 180)))
        # FG 切り替わり時、前 FG の滞在秒数がこの値以上なら STT flush
        self._fg_flush_threshold_sec = max(0, int(
            cfg.get("stt_flush_on_fg_change_min_duration_seconds", 1800)
        ))

        self._poll_task: asyncio.Task | None = None
        self._poll_stop = asyncio.Event()
        # PC 別失敗カウント・最終生存時刻（Main/Sub 独立に管理）
        self._consecutive_failures: dict[str, int] = {pc: 0 for pc in _PCS}
        self._last_alive_ts: dict[str, str | None] = {pc: None for pc in _PCS}

    async def poll(self) -> dict:
        """1tickの取得→記録。Main/Sub を並列取得。戻り値は Heartbeat のデバッグログ用。"""
        result = {"sample": False, "game_change": None, "fg_change": None, "alive": False}

        monitor = self.bot.activity_detector._agent_monitor
        agents = self.bot.config.get("windows_agents", [])
        main_agent = next((a for a in agents if a.get("role") == "main"), None)
        sub_agent = next((a for a in agents if a.get("role") == "sub"), None)

        async def _noop():
            return None

        main_data, sub_data = await asyncio.gather(
            monitor.fetch(main_agent) if main_agent else _noop(),
            monitor.fetch(sub_agent) if sub_agent else _noop(),
            return_exceptions=True,
        )
        if isinstance(main_data, Exception):
            main_data = None
        if isinstance(sub_data, Exception):
            sub_data = None

        ts = jst_now()
        per_pc = {"main": main_data, "sub": sub_data}

        # Main の情報は戻り値サマリに入れる（既存互換）
        for pc in _PCS:
            agent = main_agent if pc == "main" else sub_agent
            if not agent:
                continue
            data = per_pc[pc]
            if data is None:
                self._consecutive_failures[pc] += 1
                if (
                    self._consecutive_failures[pc] == self._unreachable_close_polls
                    and self._last_alive_ts[pc] is not None
                    and (
                        self._cur_game_session_id[pc] is not None
                        or self._cur_fg_session_id[pc] is not None
                    )
                ):
                    await self._close_on_unreachable(pc)
                if self._consecutive_failures[pc] in (1, 10, 60):
                    log.info(
                        "activity poll: %s PC agent unreachable (%d consecutive)",
                        pc, self._consecutive_failures[pc],
                    )
                continue
            if self._consecutive_failures[pc] >= 10:
                log.info(
                    "activity poll: %s PC agent recovered after %d failures",
                    pc, self._consecutive_failures[pc],
                )
            self._consecutive_failures[pc] = 0
            self._last_alive_ts[pc] = ts
            if pc == "main":
                result["alive"] = True

            # Sub の game は信用しない（想定外）。game は Main のみ。
            game = (data.get("game") or None) if pc == "main" else None
            fg = data.get("foreground_process") or None
            is_fullscreen = 1 if data.get("is_fullscreen") else 0

            # active_pcs は Main 側の Input-Relay 状態から算出し、Main サンプルに CSV で保存する
            # （一次情報源は Main sender。Sub サンプル側は NULL のまま）。
            active_pcs_csv: str | None = None
            if pc == "main":
                try:
                    pcs_list = self.bot.activity_detector._evaluate_active_pcs(
                        {"input_relay": data.get("input_relay")},
                        timeout_sec=self._poll_interval * 2,
                    )
                    active_pcs_csv = ",".join(pcs_list) if pcs_list else None
                except Exception as e:
                    log.debug("active_pcs evaluation failed: %s", e)

            # 生サンプル記録（pc / active_pcs カラム付き）
            try:
                await self.bot.database.execute(
                    "INSERT INTO activity_samples (ts, game, foreground_process, is_fullscreen, pc, active_pcs) VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, game, fg, is_fullscreen, pc, active_pcs_csv),
                )
                if pc == "main":
                    result["sample"] = True
            except Exception as e:
                log.warning("activity_samples insert failed (pc=%s): %s", pc, e)

            # ゲームセッション管理（Main のみ）
            if pc == "main":
                await self._handle_game_change(game, ts, result)

            # フォアグラウンドセッション管理（during_game は「今 game がセットされているか」で判定）
            await self._handle_fg_change(pc, fg, ts, result)

            # OBS セッション管理（Sub PC 側のみ状態を持つ）
            if pc == "sub":
                await self._handle_obs_change(data, ts)

        return result

    # --- ゲームセッション管理（遅延クローズ付き） ---

    async def _handle_game_change(self, game: str | None, ts: str, result: dict) -> None:
        pc = "main"
        prev = self._cur_game[pc]

        if game == prev:
            return

        if prev is None and game is not None:
            # None → X: 新規開始
            # pending_close が同ゲームなら「再起動吸収」としてキャンセル（開始扱いにしない）
            if self._pending_close_task is not None and self._pending_close_game == game:
                self._cancel_pending_close(reason="same-game restart within delay")
                self._cur_game[pc] = game
                # セッション ID はそのまま（restart として扱う）
                return
            # pending_close が異ゲームなら、先にそれを確定クローズ（STT flush も発火）
            if self._pending_close_task is not None:
                await self._finalize_pending_close(ts, trigger_flush=True)
            await self._open_game_session(pc, game, ts)
            result["game_change"] = {"from": prev, "to": game}
            self._cur_game[pc] = game
            await self._trigger_stt_flush(ts, reason=f"game_start:{game}")
            return

        if prev is not None and game is None:
            # X → None: 遅延クローズをスケジュール（即 close しない）
            if self._game_end_close_delay <= 0:
                # 遅延無効なら即 close
                await self._close_game_session(pc, ts)
                result["game_change"] = {"from": prev, "to": None}
                self._cur_game[pc] = None
                await self._trigger_stt_flush(ts, reason=f"game_end:{prev}")
                return
            # 既存 pending があれば置き換え（通常ありえない）
            if self._pending_close_task is not None:
                self._cancel_pending_close(reason="replaced by newer pending")
            self._pending_close_game = prev
            self._pending_close_task = asyncio.create_task(self._pending_close_worker(ts))
            # _cur_game は None に落とす（外部 API は「ゲーム停止」として見える）
            result["game_change"] = {"from": prev, "to": None}
            self._cur_game[pc] = None
            return

        # X → Y: 別ゲームに直接切り替え
        # pending_close があれば先に確定（再起動ではない＝別ゲーム遷移）
        if self._pending_close_task is not None:
            await self._finalize_pending_close(ts, trigger_flush=True)
        # 既存セッションを即 close
        if self._cur_game_session_id[pc] is not None:
            await self._close_game_session(pc, ts)
        await self._open_game_session(pc, game, ts)
        result["game_change"] = {"from": prev, "to": game}
        self._cur_game[pc] = game
        await self._trigger_stt_flush(ts, reason=f"game_switch:{prev}->{game}")

    async def _pending_close_worker(self, scheduled_ts: str) -> None:
        """ゲーム終了の遅延クローズ。delay 後に確定 close + STT flush。
        キャンセルされた場合（再起動吸収/別ゲーム遷移）はここには到達しない。"""
        try:
            await asyncio.sleep(self._game_end_close_delay)
        except asyncio.CancelledError:
            return
        try:
            close_ts = jst_now()
            closing_game = self._pending_close_game
            if self._cur_game_session_id["main"] is not None:
                await self._close_game_session("main", close_ts)
            log.info(
                "activity: game end confirmed for '%s' after %ds delay",
                closing_game, self._game_end_close_delay,
            )
            await self._trigger_stt_flush(close_ts, reason=f"game_end_confirmed:{closing_game}")
        except Exception as e:
            log.warning("pending_close_worker failed: %s", e)
        finally:
            self._pending_close_task = None
            self._pending_close_game = None

    def _cancel_pending_close(self, reason: str = "") -> None:
        if self._pending_close_task is None:
            return
        log.debug("cancel pending_close (game=%s, reason=%s)",
                  self._pending_close_game, reason)
        self._pending_close_task.cancel()
        self._pending_close_task = None
        self._pending_close_game = None

    async def _finalize_pending_close(self, ts: str, trigger_flush: bool) -> None:
        """pending_close をタイマー待たずに即確定する（別ゲーム遷移時など）。"""
        if self._pending_close_task is None:
            return
        closing_game = self._pending_close_game
        self._pending_close_task.cancel()
        self._pending_close_task = None
        self._pending_close_game = None
        if self._cur_game_session_id["main"] is not None:
            await self._close_game_session("main", ts)
        log.info("activity: finalize pending_close for '%s' immediately", closing_game)
        if trigger_flush:
            await self._trigger_stt_flush(ts, reason=f"game_end_finalized:{closing_game}")

    # --- フォアグラウンドセッション管理 ---

    async def _handle_fg_change(self, pc: str, fg: str | None, ts: str, result: dict) -> None:
        if fg == self._cur_fg[pc]:
            return
        prev_duration: int | None = None
        if self._cur_fg_session_id[pc] is not None:
            # close 前に start_at を取得し、移行先判定のために duration を算出
            row = await self.bot.database.fetchone(
                "SELECT start_at FROM foreground_sessions WHERE id = ?",
                (self._cur_fg_session_id[pc],),
            )
            if row:
                prev_duration = _duration_sec(row["start_at"], ts)
            await self._close_fg_session(pc, ts)
        if fg:
            await self._open_fg_session(pc, fg, ts, during_game=self._cur_game[pc])
        if pc == "main":
            result["fg_change"] = {"from": self._cur_fg[pc], "to": fg}
        self._cur_fg[pc] = fg

        # 長時間 FG → 別 FG の切り替えで STT flush
        if (
            prev_duration is not None
            and self._fg_flush_threshold_sec > 0
            and prev_duration >= self._fg_flush_threshold_sec
        ):
            await self._trigger_stt_flush(ts, reason=f"fg_change({pc}):duration={prev_duration}s")

    async def _open_game_session(self, pc: str, game: str, ts: str) -> None:
        # game_sessions には pc カラムがない（Main のみの想定）。pc 引数は将来拡張用。
        cursor = await self.bot.database.execute(
            "INSERT INTO game_sessions (game_name, start_at) VALUES (?, ?)",
            (game, ts),
        )
        self._cur_game_session_id[pc] = cursor.lastrowid

    async def _close_game_session(self, pc: str, ts: str) -> None:
        sid = self._cur_game_session_id[pc]
        row = await self.bot.database.fetchone(
            "SELECT start_at FROM game_sessions WHERE id = ?",
            (sid,),
        )
        dur = _duration_sec(row["start_at"], ts) if row else None
        await self.bot.database.execute(
            "UPDATE game_sessions SET end_at = ?, duration_sec = ? WHERE id = ?",
            (ts, dur, sid),
        )
        self._cur_game_session_id[pc] = None

    async def _open_fg_session(self, pc: str, fg: str, ts: str, during_game: str | None) -> None:
        cursor = await self.bot.database.execute(
            "INSERT INTO foreground_sessions (process_name, start_at, during_game, game_name, pc) VALUES (?, ?, ?, ?, ?)",
            (fg, ts, 1 if during_game else 0, during_game, pc),
        )
        self._cur_fg_session_id[pc] = cursor.lastrowid

    async def _close_fg_session(self, pc: str, ts: str) -> None:
        sid = self._cur_fg_session_id[pc]
        row = await self.bot.database.fetchone(
            "SELECT start_at FROM foreground_sessions WHERE id = ?",
            (sid,),
        )
        dur = _duration_sec(row["start_at"], ts) if row else None
        await self.bot.database.execute(
            "UPDATE foreground_sessions SET end_at = ?, duration_sec = ? WHERE id = ?",
            (ts, dur, sid),
        )
        self._cur_fg_session_id[pc] = None

    # --- OBS セッション管理（Sub PC のみ） ---

    async def _handle_obs_change(self, sub_data: dict, ts: str) -> None:
        """Sub /activity の obs_streaming/recording/replay_buffer の状態変化を obs_sessions に反映。"""
        # OBS 未接続時は状態を信用しない（誤 close 防止）。
        # Sub PC agent が接続切れ → どこかで _close_on_unreachable が走る。
        if not sub_data.get("obs_connected", False):
            return
        for kind in _OBS_KINDS:
            key = f"obs_{kind}"
            now_on = bool(sub_data.get(key, False))
            prev_on = self._cur_obs[kind]
            if now_on == prev_on:
                continue
            if now_on:
                await self._open_obs_session(kind, ts)
            else:
                await self._close_obs_session(kind, ts)
            self._cur_obs[kind] = now_on

    async def _open_obs_session(self, kind: str, ts: str) -> None:
        cursor = await self.bot.database.execute(
            "INSERT INTO obs_sessions (kind, start_at) VALUES (?, ?)",
            (kind, ts),
        )
        self._cur_obs_session_id[kind] = cursor.lastrowid
        log.info("OBS session opened: kind=%s", kind)

    async def _close_obs_session(self, kind: str, ts: str) -> None:
        sid = self._cur_obs_session_id[kind]
        if sid is None:
            return
        row = await self.bot.database.fetchone(
            "SELECT start_at FROM obs_sessions WHERE id = ?",
            (sid,),
        )
        dur = _duration_sec(row["start_at"], ts) if row else None
        await self.bot.database.execute(
            "UPDATE obs_sessions SET end_at = ?, duration_sec = ? WHERE id = ?",
            (ts, dur, sid),
        )
        self._cur_obs_session_id[kind] = None
        log.info("OBS session closed: kind=%s duration=%ss", kind, dur)

    # --- STT flush トリガー ---

    async def _trigger_stt_flush(self, until: str, reason: str) -> None:
        """STT processor に未要約分の強制要約を依頼。Fire-and-forget（poll をブロックしない）。
        STT 無効 / stt モジュール未存在なら静かにスキップ。"""
        if not self.bot.config.get("stt", {}).get("enabled", False):
            return
        try:
            from src.stt.processor import STTProcessor
        except Exception:
            return
        processor = STTProcessor(self.bot)

        async def _run():
            try:
                await processor.flush(until=until, reason=reason)
            except Exception as e:
                log.warning("STT flush failed (reason=%s): %s", reason, e)

        asyncio.create_task(_run())

    async def _close_on_unreachable(self, pc: str) -> None:
        """指定 PC の未応答が続いた場合、進行中セッションを直近生存時刻で close する。
        カレント状態もリセットし、復旧後は新しいセッションが開く。"""
        close_ts = self._last_alive_ts[pc]
        if not close_ts:
            return
        log.info(
            "activity poll: closing open %s sessions at last-alive %s (agent unreachable %d polls)",
            pc, close_ts, self._consecutive_failures[pc],
        )
        if self._cur_game_session_id[pc] is not None:
            await self._close_game_session(pc, close_ts)
            self._cur_game[pc] = None
        if self._cur_fg_session_id[pc] is not None:
            await self._close_fg_session(pc, close_ts)
            self._cur_fg[pc] = None
        # Sub 未応答時は OBS セッションも閉じる
        if pc == "sub":
            for kind in _OBS_KINDS:
                if self._cur_obs_session_id[kind] is not None:
                    await self._close_obs_session(kind, close_ts)
                    self._cur_obs[kind] = False

    async def start_polling(self) -> None:
        """独立した asyncio タスクで poll を定期実行する。"""
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_stop.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("ActivityCollector polling started (interval=%ds)", self._poll_interval)

    async def stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_task:
            try:
                await asyncio.wait_for(self._poll_task, timeout=5)
            except TimeoutError:
                self._poll_task.cancel()
            self._poll_task = None
        # 終了時に pending_close も破棄（DB 上は終了時点で閉じたいので、last alive 時刻で close 試行）
        if self._pending_close_task is not None:
            self._cancel_pending_close(reason="shutdown")
            if self._cur_game_session_id["main"] is not None:
                close_ts = self._last_alive_ts["main"] or jst_now()
                try:
                    await self._close_game_session("main", close_ts)
                    self._cur_game["main"] = None
                except Exception as e:
                    log.warning("shutdown close_game_session failed: %s", e)

    async def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            try:
                await self.poll()
            except Exception as e:
                log.warning("activity poll loop error: %s", e)
            try:
                await asyncio.wait_for(self._poll_stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass

    async def cleanup_old_samples(self) -> int:
        """retention_days より古い activity_samples を削除。戻り値は参考。"""
        if self._retention_days <= 0:
            return 0
        cutoff_dt = datetime.now(tz=_JST) - timedelta(days=self._retention_days)
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            await self.bot.database.execute(
                "DELETE FROM activity_samples WHERE ts < ?", (cutoff,),
            )
            log.info("activity_samples cleanup executed (cutoff=%s)", cutoff)
        except Exception as e:
            log.warning("activity_samples cleanup failed: %s", e)
        return 0

    async def restore_open_sessions(self) -> None:
        """起動時: DBに end_at=NULL のセッションが残っていたら PC 別に拾う。
        直近の activity_samples.ts（最後に該当 PC agent が alive だった時刻）を基点に判断:
        - 基点が新しい（RESTORE_MAX_AGE_SEC 以内）→ 継続し、_last_alive_ts[pc] を基点で初期化
        - 基点が古い／サンプルなし → その基点（or start_at）で close して破棄（実プレイ分を保持）
        """
        RESTORE_MAX_AGE_SEC = 15 * 60
        now = datetime.now(tz=_JST)

        for pc in _PCS:
            sample_row = await self.bot.database.fetchone(
                "SELECT MAX(ts) AS ts FROM activity_samples WHERE pc = ?",
                (pc,),
            )
            last_sample_ts = sample_row["ts"] if sample_row else None
            sample_age = _age_seconds(last_sample_ts, now) if last_sample_ts else None
            stale = sample_age is None or sample_age > RESTORE_MAX_AGE_SEC

            async def _handle(table: str, name_col: str, id_key: str, name_key: str, has_pc: bool) -> None:
                if has_pc:
                    row = await self.bot.database.fetchone(
                        f"SELECT id, {name_col}, start_at FROM {table} WHERE end_at IS NULL AND pc = ? ORDER BY id DESC LIMIT 1",
                        (pc,),
                    )
                else:
                    # game_sessions は pc カラムがない → Main のみ対象
                    if pc != "main":
                        return
                    row = await self.bot.database.fetchone(
                        f"SELECT id, {name_col}, start_at FROM {table} WHERE end_at IS NULL ORDER BY id DESC LIMIT 1"
                    )
                if not row:
                    return
                if stale:
                    close_ts = last_sample_ts or row["start_at"]
                    dur = _duration_sec(row["start_at"], close_ts) or 0
                    await self.bot.database.execute(
                        f"UPDATE {table} SET end_at = ?, duration_sec = ? WHERE id = ?",
                        (close_ts, max(dur, 0), row["id"]),
                    )
                    log.info(
                        "activity restore: closed stale %s id=%s pc=%s (sample_age=%s, dur=%ss)",
                        table, row["id"], pc,
                        f"{int(sample_age)}s" if sample_age is not None else "no-samples",
                        max(dur, 0),
                    )
                    return
                # 継続: 該当 PC のカレントにセット
                if id_key == "_cur_game_session_id":
                    self._cur_game_session_id[pc] = row["id"]
                    self._cur_game[pc] = row[name_col]
                elif id_key == "_cur_fg_session_id":
                    self._cur_fg_session_id[pc] = row["id"]
                    self._cur_fg[pc] = row[name_col]

            await _handle("game_sessions", "game_name", "_cur_game_session_id", "_cur_game", has_pc=False)
            await _handle("foreground_sessions", "process_name", "_cur_fg_session_id", "_cur_fg", has_pc=True)

            # 継続するセッションがあれば _last_alive_ts[pc] を直近生存時刻で初期化
            if not stale and (
                self._cur_game_session_id[pc] is not None or self._cur_fg_session_id[pc] is not None
            ):
                self._last_alive_ts[pc] = last_sample_ts

        # OBS セッションの復元（Sub PC）: end_at=NULL の開きっぱなしを last-alive で閉じる
        open_obs = await self.bot.database.fetchall(
            "SELECT id, kind, start_at FROM obs_sessions WHERE end_at IS NULL"
        )
        sub_last = self._last_alive_ts.get("sub")
        for row in open_obs:
            close_ts = sub_last or row["start_at"]
            dur = _duration_sec(row["start_at"], close_ts) or 0
            await self.bot.database.execute(
                "UPDATE obs_sessions SET end_at = ?, duration_sec = ? WHERE id = ?",
                (close_ts, max(dur, 0), row["id"]),
            )
            log.info("activity restore: closed stale obs_sessions id=%s kind=%s", row["id"], row["kind"])


def _age_seconds(start: str, now: datetime) -> float | None:
    try:
        s = datetime.fromisoformat(start)
        if s.tzinfo is None:
            s = s.replace(tzinfo=_JST)
        return (now - s).total_seconds()
    except Exception:
        return None


def _duration_sec(start: str, end: str) -> int | None:
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        if s.tzinfo is None:
            s = s.replace(tzinfo=_JST)
        if e.tzinfo is None:
            e = e.replace(tzinfo=_JST)
        return int((e - s).total_seconds())
    except Exception:
        return None
