"""ゲームプレイ習慣の自動検出（純ヒューリスティック）。

game_sessions テーブルから「デイリー習慣ゲーム」「長期継続ゲーム」を判定し、
InnerMind が発言に使える形で返す。LLM は使わない。
"""

from datetime import datetime, timedelta, timezone

from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))


def _get_habit_config(bot) -> dict:
    """config.yaml の activity.habit セクションを取得。無ければ空 dict。"""
    activity_cfg = bot.config.get("activity", {}) if getattr(bot, "config", None) else {}
    return activity_cfg.get("habit", {}) or {}


def _parse_dt(s: str) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' → JST aware datetime。失敗時 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_JST)
    except (ValueError, TypeError):
        return None


async def detect_daily_habits(
    bot,
    lookback_days: int | None = None,
    min_active_days: int | None = None,
) -> list[dict]:
    """過去 lookback_days 日のうち min_active_days 日以上プレイされたゲームを抽出。

    返り値: [{game_name, active_days, total_sec, last_played_at}, ...]（合計時間降順）
    """
    cfg = _get_habit_config(bot)
    if lookback_days is None:
        lookback_days = int(cfg.get("daily_lookback_days", 14))
    if min_active_days is None:
        min_active_days = int(cfg.get("daily_min_active_days", 10))

    now = datetime.now(tz=_JST)
    cutoff = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")

    # date(start_at) で重複日を畳んで active_days を数える。duration_sec は NULL を 0 扱い。
    rows = await bot.database.fetchall(
        """
        SELECT game_name,
               COUNT(DISTINCT date(start_at)) AS active_days,
               SUM(COALESCE(duration_sec, 0)) AS total_sec,
               MAX(start_at) AS last_played_at
        FROM game_sessions
        WHERE start_at >= ?
        GROUP BY game_name
        HAVING active_days >= ?
        ORDER BY total_sec DESC
        """,
        (cutoff, min_active_days),
    )

    return [
        {
            "game_name": r["game_name"],
            "active_days": int(r["active_days"] or 0),
            "total_sec": int(r["total_sec"] or 0),
            "last_played_at": r["last_played_at"],
        }
        for r in rows
    ]


async def detect_regular_games(
    bot,
    lookback_days: int | None = None,
    min_hours: float | None = None,
    min_sessions_per_week: float | None = None,
) -> list[dict]:
    """過去 lookback_days 日で累計 min_hours 以上、かつ週平均 min_sessions_per_week 回以上のゲーム。

    返り値: [{game_name, total_sec, sessions, weekly_pace, last_played_at, avg_interval_days}, ...]
    - avg_interval_days: プレイした日同士の平均間隔
    """
    cfg = _get_habit_config(bot)
    if lookback_days is None:
        lookback_days = int(cfg.get("regular_lookback_days", 60))
    if min_hours is None:
        min_hours = float(cfg.get("regular_min_hours", 10))
    if min_sessions_per_week is None:
        min_sessions_per_week = float(cfg.get("regular_min_weekly_sessions", 3))

    now = datetime.now(tz=_JST)
    cutoff = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")

    weeks = max(lookback_days / 7.0, 1.0)
    min_sec = min_hours * 3600
    min_sessions_total = min_sessions_per_week * weeks

    # 一次集計: 合計時間 / セッション数 / 最終プレイ
    aggregates = await bot.database.fetchall(
        """
        SELECT game_name,
               SUM(COALESCE(duration_sec, 0)) AS total_sec,
               COUNT(*) AS sessions,
               MAX(start_at) AS last_played_at
        FROM game_sessions
        WHERE start_at >= ?
        GROUP BY game_name
        HAVING total_sec >= ? AND sessions >= ?
        ORDER BY total_sec DESC
        """,
        (cutoff, min_sec, min_sessions_total),
    )

    if not aggregates:
        return []

    results: list[dict] = []
    for agg in aggregates:
        game_name = agg["game_name"]
        # プレイ日（DISTINCT date）を取得し、平均間隔を算出
        day_rows = await bot.database.fetchall(
            """
            SELECT DISTINCT date(start_at) AS d
            FROM game_sessions
            WHERE game_name = ? AND start_at >= ?
            ORDER BY d ASC
            """,
            (game_name, cutoff),
        )
        days = [r["d"] for r in day_rows if r["d"]]

        avg_interval_days = 0.0
        if len(days) >= 2:
            parsed: list[datetime] = []
            for d in days:
                try:
                    parsed.append(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=_JST))
                except ValueError:
                    continue
            if len(parsed) >= 2:
                diffs = [
                    (parsed[i + 1] - parsed[i]).total_seconds() / 86400
                    for i in range(len(parsed) - 1)
                ]
                if diffs:
                    avg_interval_days = sum(diffs) / len(diffs)
        elif len(days) == 1:
            # 1日しかプレイしていない → 間隔不明。lookback_days を上限として扱う
            avg_interval_days = float(lookback_days)

        sessions = int(agg["sessions"] or 0)
        weekly_pace = sessions / weeks if weeks else 0.0

        results.append({
            "game_name": game_name,
            "total_sec": int(agg["total_sec"] or 0),
            "sessions": sessions,
            "weekly_pace": round(weekly_pace, 2),
            "last_played_at": agg["last_played_at"],
            "avg_interval_days": round(avg_interval_days, 2),
        })

    return results


async def check_missed_today(bot) -> list[dict]:
    """デイリー習慣のうち、今日（JST）まだプレイしていないゲームを返す。

    返り値: [{game_name, last_played_at, streak_days}, ...]
    streak_days: 連続未プレイ日数（今日プレイしていないので最低 1）
    """
    habits = await detect_daily_habits(bot)
    if not habits:
        return []

    today = datetime.now(tz=_JST).date()
    today_str = today.strftime("%Y-%m-%d")
    game_names = [h["game_name"] for h in habits]

    # 今日プレイしたゲームをまとめて取得
    placeholders = ",".join("?" * len(game_names))
    rows = await bot.database.fetchall(
        f"""
        SELECT DISTINCT game_name
        FROM game_sessions
        WHERE game_name IN ({placeholders})
          AND date(start_at) = ?
        """,
        (*game_names, today_str),
    )
    played_today = {r["game_name"] for r in rows}

    missed: list[dict] = []
    for h in habits:
        if h["game_name"] in played_today:
            continue
        last_dt = _parse_dt(h["last_played_at"])
        if last_dt is None:
            streak_days = 1
        else:
            streak_days = max((today - last_dt.date()).days, 1)
        missed.append({
            "game_name": h["game_name"],
            "last_played_at": h["last_played_at"],
            "streak_days": streak_days,
        })

    # 連続未プレイが長い順（注目度高い順）
    missed.sort(key=lambda x: x["streak_days"], reverse=True)
    return missed


async def check_long_absence(bot) -> list[dict]:
    """長期継続ゲームから、平均間隔の absence_multiplier 倍以上空いたゲームを返す。

    返り値: [{game_name, days_since, avg_interval_days, total_hours}, ...]
    """
    cfg = _get_habit_config(bot)
    multiplier = float(cfg.get("absence_multiplier", 2.0))

    regulars = await detect_regular_games(bot)
    if not regulars:
        return []

    now = datetime.now(tz=_JST)
    result: list[dict] = []
    for g in regulars:
        last_dt = _parse_dt(g["last_played_at"])
        if last_dt is None:
            continue
        days_since = (now - last_dt).total_seconds() / 86400
        avg_iv = g["avg_interval_days"] or 0.0
        # 平均間隔がゼロ（全日プレイ）の場合は 1日扱いで判定
        threshold = max(avg_iv, 1.0) * multiplier
        if days_since > threshold:
            result.append({
                "game_name": g["game_name"],
                "days_since": round(days_since, 1),
                "avg_interval_days": avg_iv,
                "total_hours": round(g["total_sec"] / 3600, 1),
            })

    result.sort(key=lambda x: x["days_since"], reverse=True)
    return result
