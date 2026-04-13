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

## ゲーム別時間（psutil 連続検知）
{games}

## フォアグラウンド別時間（ゲーム中以外）
{fg}

## 指示
- 上記データをもとに、1〜3文で「いにわは {date} に何をしていたか」を短くまとめてください
- ゲーム時間が主ならゲーム中心、作業アプリが主なら作業中心で要約
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
        SELECT process_name, SUM(COALESCE(duration_sec, 0)) AS sec, during_game
        FROM foreground_sessions
        WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
        GROUP BY process_name, during_game HAVING sec > 0 ORDER BY sec DESC
        """,
        (day_start, day_end),
    )
    # ゲーム中以外だけ抽出、ゲーム中の寄り道は別途文脈
    fg_work = [r for r in fg_rows if not r["during_game"]]

    if not games and not fg_work:
        log.debug("daily_summary: no activity for %s", target_date)
        return False

    games_text = "\n".join(f"- {g['game_name']}: {_fmt(g['sec'])}" for g in games) or "なし"
    fg_text = "\n".join(f"- {f['process_name']}: {_fmt(f['sec'])}" for f in fg_work[:10]) or "なし"

    prompt = _PROMPT.format(date=target_date, games=games_text, fg=fg_text)

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
