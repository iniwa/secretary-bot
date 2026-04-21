"""前日のゲーム/フォアグラウンド/OBS/STT を統合した活動記録（日記）を生成する。

旧 daily_summary.py の置き換え。1〜3文の要約ではなく、
時系列の出来事をタイムライン化してから LLM に渡し、記録調の文章を出力する。

出力先:
- daily_diaries テーブル（per-day 一意）
- people_memory（source=daily_diary, date=YYYY-MM-DD）
"""

import json
from datetime import datetime, timedelta, timezone

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))

_SYSTEM = (
    "あなたはユーザーのPC活動を淡々と記録する書記係です。"
    "出力は日本語のみ。キャラクター性・感情表現・呼びかけは一切含めません。"
    "事実ベースの記録文として書いてください。"
)

_PROMPT = """\
以下は {date} のいにわ（ユーザー）の PC 活動タイムラインです。
複数の情報源（ゲーム検出・フォアグラウンド・OBS・発話要約）を時系列に結合しています。

## タイムライン
{timeline}

## 集計
- 総ゲーム時間: {total_game}
- 総配信時間: {total_stream}（{streaming_note}）
- 総録画時間: {total_record}
- 両 PC 同時操作時間: {simultaneous}

## 指示
- {date} の活動を、時系列の記録として 300〜500 字でまとめてください
- キャラクター性・感情・呼びかけは禁止（「〜だった」「〜した」等の客観記録調）
- ゲーム・作業・配信・発話文脈（STT 要約）の順ではなく、**時刻順に事象を並べる**
- 配信や録画があればその時間帯を明記（「14:00〜18:00: Street Fighter 6 を配信しながらプレイ」等）
- 長時間続いた FG（作業アプリ）は主要作業として扱う
- STT 要約から読み取れる文脈（ゲーム中の発話、通話、独り言など）を適宜織り込む
- 前置き・見出し・箇条書きは使わず、地の文で記述
"""


async def run_daily_diary(bot, target_date: str | None = None) -> bool:
    """指定日（YYYY-MM-DD、未指定なら昨日）の活動を日記化して保存。
    戻り値: 実行したら True、データ無し/失敗で False。
    """
    if target_date is None:
        target_date = (datetime.now(tz=_JST) - timedelta(days=1)).strftime("%Y-%m-%d")

    day_start = f"{target_date} 00:00:00"
    day_end = f"{target_date} 23:59:59"

    # --- データ収集 ---
    games = await bot.database.fetchall(
        """
        SELECT game_name, start_at, end_at, COALESCE(duration_sec, 0) AS sec
        FROM game_sessions
        WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
        ORDER BY start_at
        """,
        (day_start, day_end),
    )
    fg_rows = await bot.database.fetchall(
        """
        SELECT pc, process_name, start_at, end_at, COALESCE(duration_sec, 0) AS sec, during_game
        FROM foreground_sessions
        WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
        ORDER BY start_at
        """,
        (day_start, day_end),
    )
    obs = await bot.database.fetchall(
        """
        SELECT kind, start_at, end_at, COALESCE(duration_sec, 0) AS sec
        FROM obs_sessions
        WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
        ORDER BY start_at
        """,
        (day_start, day_end),
    )
    stt_summaries = await bot.database.fetchall(
        """
        SELECT id, summary, created_at
        FROM stt_summaries
        WHERE created_at BETWEEN ? AND ?
        ORDER BY created_at
        """,
        (day_start, day_end),
    )
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

    if not games and not fg_rows and not obs and not stt_summaries:
        log.debug("daily_diary: no activity for %s", target_date)
        return False

    # --- 集計値 ---
    total_game_sec = sum(g["sec"] for g in games)
    total_stream_sec = sum(o["sec"] for o in obs if o["kind"] == "streaming")
    total_record_sec = sum(o["sec"] for o in obs if o["kind"] == "recording")
    streaming_detected = 1 if total_stream_sec > 0 else 0

    # --- タイムライン構築 ---
    timeline_items: list[tuple[str, str]] = []  # (sort_key, line)

    for g in games:
        s = _hhmm(g["start_at"])
        e = _hhmm(g["end_at"])
        timeline_items.append((g["start_at"], f"[{s}-{e}] ゲーム: {g['game_name']} ({_fmt(g['sec'])})"))

    # FG は主要作業のみ拾う（5分未満の切替や during_game=1 は除外、片 PC 10 件まで）
    fg_main = [r for r in fg_rows if r["pc"] == "main" and not r["during_game"] and r["sec"] >= 300]
    fg_sub = [r for r in fg_rows if r["pc"] == "sub" and r["sec"] >= 300]
    for r in sorted(fg_main, key=lambda x: x["sec"], reverse=True)[:10]:
        s = _hhmm(r["start_at"])
        e = _hhmm(r["end_at"])
        timeline_items.append((r["start_at"], f"[{s}-{e}] Main作業: {r['process_name']} ({_fmt(r['sec'])})"))
    for r in sorted(fg_sub, key=lambda x: x["sec"], reverse=True)[:10]:
        s = _hhmm(r["start_at"])
        e = _hhmm(r["end_at"])
        timeline_items.append((r["start_at"], f"[{s}-{e}] Sub作業: {r['process_name']} ({_fmt(r['sec'])})"))

    for o in obs:
        s = _hhmm(o["start_at"])
        e = _hhmm(o["end_at"])
        kind_ja = {"streaming": "配信", "recording": "録画", "replay_buffer": "リプレイバッファ"}.get(
            o["kind"], o["kind"]
        )
        timeline_items.append((o["start_at"], f"[{s}-{e}] OBS {kind_ja} ({_fmt(o['sec'])})"))

    for s in stt_summaries:
        timeline_items.append((s["created_at"], f"[{_hhmm(s['created_at'])}] 発話要約 #{s['id']}:\n{s['summary']}"))

    timeline_items.sort(key=lambda x: x[0])
    timeline_text = "\n".join(item[1] for item in timeline_items) or "（活動データなし）"

    streaming_note = "streaming) OBS 配信検知あり" if streaming_detected else "OBS 配信なし"
    prompt = _PROMPT.format(
        date=target_date,
        timeline=timeline_text,
        total_game=_fmt(total_game_sec),
        total_stream=_fmt(total_stream_sec),
        streaming_note=streaming_note,
        total_record=_fmt(total_record_sec),
        simultaneous=_fmt(simultaneous_sec) if simultaneous_sec > 0 else "なし",
    )

    try:
        diary = await bot.llm_router.generate(
            prompt, system=_SYSTEM, purpose="activity_daily_diary",
        )
    except Exception as e:
        log.warning("daily diary LLM failed: %s", e)
        return False

    diary = diary.strip()
    created_at = jst_now()

    # --- 永続化 ---
    try:
        await bot.database.execute(
            """
            INSERT INTO daily_diaries
                (date, diary, streaming_detected, total_game_sec, total_stream_sec, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                diary = excluded.diary,
                streaming_detected = excluded.streaming_detected,
                total_game_sec = excluded.total_game_sec,
                total_stream_sec = excluded.total_stream_sec,
                created_at = excluded.created_at
            """,
            (target_date, diary, streaming_detected, total_game_sec, total_stream_sec, created_at),
        )
    except Exception as e:
        log.warning("daily_diaries upsert failed: %s", e)
        return False

    try:
        if getattr(bot, "people_memory", None):
            await bot.people_memory.save(
                diary,
                metadata={
                    "source": "daily_diary",
                    "date": target_date,
                    "streaming_detected": streaming_detected,
                    "total_game_sec": total_game_sec,
                    "total_stream_sec": total_stream_sec,
                },
            )
    except Exception as e:
        log.warning("daily diary people_memory save failed: %s", e)

    log.info(
        "daily diary saved for %s (game=%ss, stream=%ss, streaming_detected=%d)",
        target_date, total_game_sec, total_stream_sec, streaming_detected,
    )
    return True


def _fmt(sec: int | None) -> str:
    if not sec:
        return "0分"
    sec = int(sec)
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h}時間{m}分"
    return f"{m}分"


def _hhmm(ts: str | None) -> str:
    if not ts:
        return "??:??"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%H:%M")
    except Exception:
        # "YYYY-MM-DD HH:MM:SS" の部分抽出フォールバック
        if len(ts) >= 16:
            return ts[11:16]
        return ts
