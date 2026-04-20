"""カレンダー読み取り同期。

calendar_read_sources に登録されたカレンダーから events.list で予定を取得し、
calendar_events テーブルにキャッシュする。is_private=1 のソースは
タイトル/場所/説明をすべて破棄してから保存する。
"""

import asyncio
import functools
from datetime import UTC, datetime, timedelta

from googleapiclient.errors import HttpError

from src.database import JST, jst_now
from src.gcal.service import build_calendar_service
from src.logger import get_logger

log = get_logger(__name__)


class CalendarSyncError(Exception):
    pass


async def sync_all_sources(bot, lookahead_days: int = 60) -> dict:
    """有効な全ソースを同期して結果サマリを返す。"""
    sources = await bot.database.fetchall(
        "SELECT calendar_id, is_private FROM calendar_read_sources WHERE enabled = 1"
    )
    if not sources:
        return {"sources": 0, "events": 0}

    try:
        service = await asyncio.to_thread(build_calendar_service)
    except FileNotFoundError as e:
        raise CalendarSyncError(str(e))

    total_events = 0
    errors: list[str] = []
    for src in sources:
        calendar_id = src["calendar_id"]
        is_private = bool(src["is_private"])
        try:
            count = await _sync_one(bot, service, calendar_id, is_private, lookahead_days)
            total_events += count
            await bot.database.execute(
                "UPDATE calendar_read_sources SET last_synced_at = ? WHERE calendar_id = ?",
                (jst_now(), calendar_id),
            )
        except HttpError as e:
            msg = f"{calendar_id}: HTTP {e.resp.status}"
            log.warning("Calendar sync failed: %s", msg)
            errors.append(msg)
        except Exception as e:
            log.warning("Calendar sync failed for %s: %s", calendar_id, e)
            errors.append(f"{calendar_id}: {e}")

    result = {"sources": len(sources), "events": total_events}
    if errors:
        result["errors"] = errors
    return result


async def _sync_one(
    bot,
    service,
    calendar_id: str,
    is_private: bool,
    lookahead_days: int,
) -> int:
    """1つのカレンダーを同期。取得件数を返す。"""
    now = datetime.now(UTC)
    end = now + timedelta(days=lookahead_days)

    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        None,
        functools.partial(
            service.events().list(
                calendarId=calendar_id,
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                maxResults=250,
                singleEvents=True,
                orderBy="startTime",
            ).execute
        ),
    )
    items = resp.get("items", [])

    # このカレンダーの既存キャッシュを一旦全削除（古い/削除済みイベントの取り残し防止）
    await bot.database.execute(
        "DELETE FROM calendar_events WHERE calendar_id = ?",
        (calendar_id,),
    )

    fetched_at = jst_now()
    inserted = 0
    for ev in items:
        event_id = ev.get("id")
        if not event_id:
            continue

        start = ev.get("start", {})
        end_ = ev.get("end", {})
        start_at = start.get("dateTime") or start.get("date")
        end_at = end_.get("dateTime") or end_.get("date")
        if not start_at or not end_at:
            continue
        is_all_day = 1 if "date" in start and "dateTime" not in start else 0

        # Private の場合タイトルは保存しない（場所・説明・参加者は常に破棄）
        title = None if is_private else (ev.get("summary") or None)

        await bot.database.execute(
            "INSERT OR REPLACE INTO calendar_events "
            "(event_id, calendar_id, title, start_at, end_at, is_all_day, is_private, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, calendar_id, title,
                start_at, end_at, is_all_day,
                1 if is_private else 0, fetched_at,
            ),
        )
        inserted += 1

    return inserted


def parse_event_datetime(start_at: str) -> datetime | None:
    """event.start_at (ISO 8601 または YYYY-MM-DD) を datetime に変換。

    終日予定（YYYY-MM-DD）は JST 00:00 として扱う。
    """
    try:
        if "T" in start_at:
            # dateTime 形式 — タイムゾーン情報あり
            # Python 3.11+ では fromisoformat が 'Z' を扱える
            return datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        return datetime.strptime(start_at, "%Y-%m-%d").replace(tzinfo=JST)
    except (ValueError, TypeError):
        return None
