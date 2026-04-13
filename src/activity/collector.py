"""Main PC の /activity を定期取得し、生サンプルとセッションを記録する。

2層のセッション管理:
- game_sessions: psutil 由来の game フィールドで継続。フォアグラウンドが裏ブラウザに切り替わっても壊れない
- foreground_sessions: GetForegroundWindow 由来で切替ごとに区切る。during_game=1 ならゲーム中の寄り道
"""

from datetime import datetime, timedelta, timezone

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))


class ActivityCollector:
    def __init__(self, bot):
        self.bot = bot
        self._cur_game: str | None = None
        self._cur_game_session_id: int | None = None
        self._cur_fg: str | None = None
        self._cur_fg_session_id: int | None = None

    async def poll(self) -> dict:
        """1tickの取得→記録。戻り値は Heartbeat のデバッグログ用。"""
        result = {"sample": False, "game_change": None, "fg_change": None}

        monitor = self.bot.activity_detector._agent_monitor
        agents = self.bot.config.get("windows_agents", [])
        main_agent = next((a for a in agents if a.get("role") == "main"), None)
        if not main_agent:
            return result

        data = await monitor.fetch(main_agent)
        if data is None:
            return result

        game = data.get("game") or None
        fg = data.get("foreground_process") or None
        is_fullscreen = 1 if data.get("is_fullscreen") else 0
        ts = jst_now()

        # 生サンプル記録
        try:
            await self.bot.database.execute(
                "INSERT INTO activity_samples (ts, game, foreground_process, is_fullscreen) VALUES (?, ?, ?, ?)",
                (ts, game, fg, is_fullscreen),
            )
            result["sample"] = True
        except Exception as e:
            log.warning("activity_samples insert failed: %s", e)

        # ゲームセッション管理
        if game != self._cur_game:
            if self._cur_game_session_id is not None:
                await self._close_game_session(ts)
            if game:
                await self._open_game_session(game, ts)
            result["game_change"] = {"from": self._cur_game, "to": game}
            self._cur_game = game

        # フォアグラウンドセッション管理（during_game は「今 game がセットされているか」で判定）
        if fg != self._cur_fg:
            if self._cur_fg_session_id is not None:
                await self._close_fg_session(ts)
            if fg:
                await self._open_fg_session(fg, ts, during_game=self._cur_game)
            result["fg_change"] = {"from": self._cur_fg, "to": fg}
            self._cur_fg = fg

        return result

    async def _open_game_session(self, game: str, ts: str) -> None:
        cursor = await self.bot.database.execute(
            "INSERT INTO game_sessions (game_name, start_at) VALUES (?, ?)",
            (game, ts),
        )
        self._cur_game_session_id = cursor.lastrowid

    async def _close_game_session(self, ts: str) -> None:
        row = await self.bot.database.fetchone(
            "SELECT start_at FROM game_sessions WHERE id = ?",
            (self._cur_game_session_id,),
        )
        dur = _duration_sec(row["start_at"], ts) if row else None
        await self.bot.database.execute(
            "UPDATE game_sessions SET end_at = ?, duration_sec = ? WHERE id = ?",
            (ts, dur, self._cur_game_session_id),
        )
        self._cur_game_session_id = None

    async def _open_fg_session(self, fg: str, ts: str, during_game: str | None) -> None:
        cursor = await self.bot.database.execute(
            "INSERT INTO foreground_sessions (process_name, start_at, during_game, game_name) VALUES (?, ?, ?, ?)",
            (fg, ts, 1 if during_game else 0, during_game),
        )
        self._cur_fg_session_id = cursor.lastrowid

    async def _close_fg_session(self, ts: str) -> None:
        row = await self.bot.database.fetchone(
            "SELECT start_at FROM foreground_sessions WHERE id = ?",
            (self._cur_fg_session_id,),
        )
        dur = _duration_sec(row["start_at"], ts) if row else None
        await self.bot.database.execute(
            "UPDATE foreground_sessions SET end_at = ?, duration_sec = ? WHERE id = ?",
            (ts, dur, self._cur_fg_session_id),
        )
        self._cur_fg_session_id = None

    async def restore_open_sessions(self) -> None:
        """起動時: DBに end_at=NULL のセッションが残っていたら、メモリ状態として拾う。
        Main PC 再接続後に同じ game/fg なら継続、違えば close する。
        """
        game_row = await self.bot.database.fetchone(
            "SELECT id, game_name FROM game_sessions WHERE end_at IS NULL ORDER BY id DESC LIMIT 1"
        )
        if game_row:
            self._cur_game = game_row["game_name"]
            self._cur_game_session_id = game_row["id"]
        fg_row = await self.bot.database.fetchone(
            "SELECT id, process_name FROM foreground_sessions WHERE end_at IS NULL ORDER BY id DESC LIMIT 1"
        )
        if fg_row:
            self._cur_fg = fg_row["process_name"]
            self._cur_fg_session_id = fg_row["id"]


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
