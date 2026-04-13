"""HabitSource — ゲームプレイ習慣の揺らぎを InnerMind に提供。

デイリー習慣ゲームの未プレイ、長期継続ゲームからの離脱を検出し、
ミミが「今日Xまだやってないね？」などと自然に言及できる形で渡す。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from src.activity.habit_detector import check_long_absence, check_missed_today
from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))


class HabitSource(ContextSource):
    name = "いにわのゲーム習慣"
    priority = 50

    async def collect(self, shared: dict) -> dict | None:
        missed, absent = await asyncio.gather(
            check_missed_today(self.bot),
            check_long_absence(self.bot),
            return_exceptions=True,
        )

        if isinstance(missed, Exception):
            log.warning("HabitSource: check_missed_today failed: %s", missed)
            missed = []
        if isinstance(absent, Exception):
            log.warning("HabitSource: check_long_absence failed: %s", absent)
            absent = []

        if not missed and not absent:
            return None

        return {"missed_today": missed, "long_absence": absent}

    def format_for_prompt(self, data: dict) -> str:
        """観察データを中立的に列挙する。発言の要否・対象はミミ（LLM）が決める。"""
        lines = ["### いにわの最近のプレイ傾向"]

        missed = data.get("missed_today") or []
        if missed:
            lines.append("最近のプレイ頻度と今日の状況:")
            for m in missed[:5]:
                ratio = m.get("activity_ratio", 0)
                active = m.get("active_days", 0)
                lookback = m.get("lookback_days", 14)
                streak = m.get("streak_days", 1)
                last_txt = self._format_last(m.get("last_played_at"))
                streak_txt = f"{streak}日連続未プレイ" if streak > 1 else "今日未プレイ"
                lines.append(
                    f"- 『{m['game_name']}』過去{lookback}日中{active}日プレイ"
                    f"（頻度{ratio:.0%}）・{streak_txt}・最後は{last_txt}"
                )

        absent = data.get("long_absence") or []
        if absent:
            lines.append("以前は継続的だったが最近間隔が空いているもの:")
            for a in absent[:3]:
                avg_iv = a.get("avg_interval_days", 0)
                days_since = a.get("days_since", 0)
                hours = a.get("total_hours", 0)
                if avg_iv and avg_iv >= 0.5:
                    lines.append(
                        f"- 『{a['game_name']}』{days_since:.0f}日経過"
                        f"（普段の間隔{avg_iv:.1f}日・累計{hours:.0f}時間）"
                    )
                else:
                    lines.append(
                        f"- 『{a['game_name']}』{days_since:.0f}日経過（累計{hours:.0f}時間）"
                    )

        return "\n".join(lines)

    @staticmethod
    def _format_last(last_played_at: str | None) -> str:
        """last_played_at を『今日／昨日／N日前』の自然文に変換。"""
        if not last_played_at:
            return "不明"
        try:
            last_dt = datetime.strptime(last_played_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_JST)
        except (ValueError, TypeError):
            return last_played_at
        today = datetime.now(tz=_JST).date()
        diff = (today - last_dt.date()).days
        if diff <= 0:
            return "今日"
        if diff == 1:
            return "昨日"
        return f"{diff}日前"
