"""前日のゲーム/フォアグラウンドセッションを集計し、LLMで自然文化して people_memory に保存。"""

from datetime import datetime, timedelta, timezone

from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))

_SYSTEM = (
    "あなたはユーザーのPC活動を簡潔にまとめるアシスタントです。"
    "出力は日本語のみ。前置きや見出しは不要で、事実ベースで1〜3文にまとめてください。"
)

_PROMPT = """\
以下はいにわ（ユーザー）の {date} のPC活動データです。

## ゲーム別時間（Main PC・psutil 連続検知）
{games}

## Main PC フォアグラウンド別時間（ゲーム中以外）
{fg_main}

## Sub PC フォアグラウンド別時間
{fg_sub}

## 両 PC 同時操作時間（SF6 等プレイ中に Sub PC でも作業していた時間）
{simultaneous}

## 指示
- 上記データをもとに、1〜3文で「いにわは {date} に何をしていたか」を短くまとめてください
- ゲーム時間が主ならゲーム中心、作業アプリが主なら作業中心で要約
- Main / Sub を同時に使っていた時間が目立つ場合は「Main でゲーム中に Sub で作業」のように明記
- 合間に別のことをしていた場合はカッコで補足してもよい（例：「5時間Factorio（合間にブラウザとDiscord）」）
- 時間は「X時間Y分」または「X分」で表記
"""


async def run_daily_summary(bot, target_date: str | None = None) -> bool:
    """指定日（YYYY-MM-DD、未指定なら昨日）の活動を集計し people_memory に保存。
    戻り値: 実行したら True、データ無し/失敗で False。
    """
    if target_date is None:
        target_date = (datetime.now(tz=_JST) - timedelta(days=1)).strftime("%Y-%m-%d")

    day_start = f"{target_date} 00:00:00"
    day_end = f"{target_date} 23:59:59"

    # ゲーム集計（開始がその日のセッション）
    games = await bot.database.fetchall(
        """
        SELECT game_name, SUM(COALESCE(duration_sec, 0)) AS sec
        FROM game_sessions
        WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
        GROUP BY game_name HAVING sec > 0 ORDER BY sec DESC
        """,
        (day_start, day_end),
    )

    fg_rows = await bot.database.fetchall(
        """
        SELECT pc, process_name, SUM(COALESCE(duration_sec, 0)) AS sec, during_game
        FROM foreground_sessions
        WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
        GROUP BY pc, process_name, during_game HAVING sec > 0 ORDER BY sec DESC
        """,
        (day_start, day_end),
    )
    # Main はゲーム中以外だけ抽出（ゲーム中の寄り道は別文脈）、Sub は全部
    fg_main = [r for r in fg_rows if r["pc"] == "main" and not r["during_game"]]
    fg_sub = [r for r in fg_rows if r["pc"] == "sub"]

    # 両 PC 同時操作時間（Main サンプルの active_pcs に main と sub 両方含む回数 × poll間隔）
    sim_row = await bot.database.fetchone(
        """
        SELECT COUNT(*) AS c FROM activity_samples
        WHERE pc='main' AND ts BETWEEN ? AND ?
          AND active_pcs LIKE '%main%' AND active_pcs LIKE '%sub%'
        """,
        (day_start, day_end),
    )
    poll_interval = int(bot.config.get("activity", {}).get("poll_interval_seconds", 60))
    simultaneous_sec = int((sim_row["c"] if sim_row else 0) * poll_interval)

    if not games and not fg_main and not fg_sub:
        log.debug("daily_summary: no activity for %s", target_date)
        return False

    games_text = "\n".join(f"- {g['game_name']}: {_fmt(g['sec'])}" for g in games) or "なし"
    fg_main_text = "\n".join(f"- {f['process_name']}: {_fmt(f['sec'])}" for f in fg_main[:10]) or "なし"
    fg_sub_text = "\n".join(f"- {f['process_name']}: {_fmt(f['sec'])}" for f in fg_sub[:10]) or "なし"
    sim_text = _fmt(simultaneous_sec) if simultaneous_sec > 0 else "なし"

    prompt = _PROMPT.format(
        date=target_date,
        games=games_text,
        fg_main=fg_main_text,
        fg_sub=fg_sub_text,
        simultaneous=sim_text,
    )

    try:
        summary = await bot.llm_router.generate(
            prompt, system=_SYSTEM, purpose="activity_daily",
        )
    except Exception as e:
        log.warning("daily activity summary LLM failed: %s", e)
        return False

    try:
        await bot.people_memory.save(
            summary.strip(),
            metadata={"source": "activity_daily", "date": target_date},
        )
        log.info("daily activity summary saved for %s", target_date)
        return True
    except Exception as e:
        log.warning("daily activity summary save failed: %s", e)
        return False


def _fmt(sec: int | None) -> str:
    if not sec:
        return "0分"
    sec = int(sec)
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h}時間{m}分"
    return f"{m}分"
