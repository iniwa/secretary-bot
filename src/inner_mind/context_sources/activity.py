"""ActivitySource — Main/Sub PC のゲーム/フォアグラウンドプロセス状況を InnerMind に提供。"""

from datetime import datetime, timedelta, timezone

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))


class ActivitySource(ContextSource):
    name = "いにわのPC活動"
    priority = 55

    async def collect(self, shared: dict) -> dict | None:
        # 現在状態（_cur_game / _cur_fg は PC 別 dict）
        collector = getattr(self.bot, "activity_collector", None)
        cur_game = collector._cur_game.get("main") if collector else None
        cur_fg_main = collector._cur_fg.get("main") if collector else None
        cur_fg_sub = collector._cur_fg.get("sub") if collector else None

        # 直近6時間のゲーム別合計時間（Main のみ）
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

        # フォアグラウンド top3（during_game=0 のみ＝純粋な作業時間）を Main / Sub 別に取得
        fg_top_main = await self.bot.database.fetchall(
            """
            SELECT process_name, SUM(COALESCE(duration_sec, 0)) AS sec
            FROM foreground_sessions
            WHERE start_at >= ? AND during_game = 0 AND pc = 'main'
            GROUP BY process_name ORDER BY sec DESC LIMIT 3
            """,
            (cutoff,),
        )
        fg_top_sub = await self.bot.database.fetchall(
            """
            SELECT process_name, SUM(COALESCE(duration_sec, 0)) AS sec
            FROM foreground_sessions
            WHERE start_at >= ? AND during_game = 0 AND pc = 'sub'
            GROUP BY process_name ORDER BY sec DESC LIMIT 3
            """,
            (cutoff,),
        )

        # 直近サンプルの active_pcs（Main サンプルに CSV 保存されている）
        active_pcs: list[str] = []
        row = await self.bot.database.fetchone(
            "SELECT active_pcs FROM activity_samples WHERE pc = 'main' AND active_pcs IS NOT NULL ORDER BY ts DESC LIMIT 1"
        )
        if row and row.get("active_pcs"):
            active_pcs = [p for p in row["active_pcs"].split(",") if p]

        if not cur_game and not cur_fg_main and not cur_fg_sub and not games and not fg_top_main and not fg_top_sub:
            return None

        return {
            "current_game": cur_game,
            "current_foreground": {"main": cur_fg_main, "sub": cur_fg_sub},
            "active_pcs": active_pcs,
            "recent_games": games,
            "recent_foreground_main": fg_top_main,
            "recent_foreground_sub": fg_top_sub,
        }

    def format_for_prompt(self, data: dict) -> str:
        lines = ["### いにわのPC活動"]
        cg = data.get("current_game")
        cf = data.get("current_foreground") or {}
        cf_main = cf.get("main") if isinstance(cf, dict) else cf
        cf_sub = cf.get("sub") if isinstance(cf, dict) else None
        active = data.get("active_pcs") or []

        if cg:
            lines.append(f"現在プレイ中（Main PC）: {cg}")
        if cf_main:
            suffix = "（ゲーム中の裏側）" if cg else ""
            lines.append(f"Main PC フォアグラウンド: {cf_main}{suffix}")
        if cf_sub:
            lines.append(f"Sub PC フォアグラウンド: {cf_sub}")
        if "main" in active and "sub" in active:
            lines.append("※ Main でゲーム/作業中に Sub PC でも並行作業中（両 PC 同時操作）")
        elif active == ["sub"]:
            lines.append("※ リモートモード中（Sub PC を操作中）")

        games = data.get("recent_games") or []
        if games:
            lines.append("直近6時間のゲーム:")
            for g in games:
                mins = int((g["sec"] or 0) // 60)
                lines.append(f"- {g['game_name']}: {mins}分")

        fg_main = data.get("recent_foreground_main") or []
        if fg_main:
            lines.append("直近6時間の Main PC 作業アプリ（ゲーム中以外）:")
            for f in fg_main:
                mins = int((f["sec"] or 0) // 60)
                if mins <= 0:
                    continue
                lines.append(f"- {f['process_name']}: {mins}分")

        fg_sub = data.get("recent_foreground_sub") or []
        if fg_sub:
            lines.append("直近6時間の Sub PC 作業アプリ:")
            for f in fg_sub:
                mins = int((f["sec"] or 0) // 60)
                if mins <= 0:
                    continue
                lines.append(f"- {f['process_name']}: {mins}分")

        return "\n".join(lines)

    async def salience(self, data: dict, shared: dict) -> float:
        """現在のプレイ/作業は最重要。履歴だけなら下がる。"""
        cg = data.get("current_game")
        cf = data.get("current_foreground") or {}
        cf_main = cf.get("main") if isinstance(cf, dict) else cf
        cf_sub = cf.get("sub") if isinstance(cf, dict) else None

        if cg:
            return 0.85
        if cf_main or cf_sub:
            return 0.65

        games = data.get("recent_games") or []
        fg_main = data.get("recent_foreground_main") or []
        if games or fg_main:
            return 0.35
        return 0.1
