"""ActivitySource — Main PC のゲーム/フォアグラウンドプロセス状況を InnerMind に提供。"""

from datetime import datetime, timedelta, timezone

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))


class ActivitySource(ContextSource):
    name = "いにわのPC活動"
    priority = 55

    async def collect(self, shared: dict) -> dict | None:
        # 現在状態（_cur_game / _cur_fg）
        collector = getattr(self.bot, "activity_collector", None)
        cur_game = collector._cur_game if collector else None
        cur_fg = collector._cur_fg if collector else None

        # 直近6時間のゲーム別合計時間
        now = datetime.now(tz=_JST)
        cutoff = (now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        games = await self.bot.database.fetchall(
            """
            SELECT game_name, SUM(COALESCE(duration_sec,
                CAST((julianday('now') - julianday(start_at)) * 86400 AS INTEGER))) AS sec
            FROM game_sessions
            WHERE start_at >= ?
            GROUP BY game_name ORDER BY sec DESC LIMIT 3
            """,
            (cutoff,),
        )

        # フォアグラウンド top3（during_game=0 のみ＝純粋な作業時間）
        fg_top = await self.bot.database.fetchall(
            """
            SELECT process_name, SUM(COALESCE(duration_sec, 0)) AS sec
            FROM foreground_sessions
            WHERE start_at >= ? AND during_game = 0
            GROUP BY process_name ORDER BY sec DESC LIMIT 3
            """,
            (cutoff,),
        )

        if not cur_game and not cur_fg and not games and not fg_top:
            return None

        return {
            "current_game": cur_game,
            "current_foreground": cur_fg,
            "recent_games": games,
            "recent_foreground": fg_top,
        }

    def format_for_prompt(self, data: dict) -> str:
        lines = ["### いにわのPC活動"]
        cg = data.get("current_game")
        cf = data.get("current_foreground")
        if cg:
            lines.append(f"現在プレイ中: {cg}")
        if cf:
            suffix = "（ゲーム中の裏側）" if cg else ""
            lines.append(f"フォアグラウンド: {cf}{suffix}")

        games = data.get("recent_games") or []
        if games:
            lines.append("直近6時間のゲーム:")
            for g in games:
                mins = int((g["sec"] or 0) // 60)
                lines.append(f"- {g['game_name']}: {mins}分")

        fg = data.get("recent_foreground") or []
        if fg:
            lines.append("直近6時間の作業アプリ（ゲーム中以外）:")
            for f in fg:
                mins = int((f["sec"] or 0) // 60)
                if mins <= 0:
                    continue
                lines.append(f"- {f['process_name']}: {mins}分")

        return "\n".join(lines)
