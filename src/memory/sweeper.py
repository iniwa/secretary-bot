"""ChromaDBメモリの鮮度ベース掃除（日次sweep）。

対象コレクションを走査し、古くて一度もヒットしていないエントリを削除する。
saved_at が欠けているエントリは既存データとして温存する。
"""

from datetime import datetime, timedelta

from src.database import JST
from src.logger import get_logger

log = get_logger(__name__)

SWEEP_COLLECTIONS = ["ai_memory", "people_memory", "conversation_log", "stt_summaries"]
_DEFAULT_STALE_DAYS = 90
_SCAN_LIMIT = 10000


def _parse_saved_at(value) -> datetime | None:
    """saved_at 文字列を JST aware datetime に変換。失敗時は None。"""
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=JST)
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # "%Y-%m-%d %H:%M:%S" と ISO8601 の両方に対応
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=JST)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except Exception:
        return None


async def run_memory_sweep(bot) -> dict[str, int]:
    """全対象コレクションをsweepし、削除件数を返す。

    条件: saved_at が stale_days 日以上前 かつ hit_count == 0
    saved_at が無いものは既存データとして温存する。
    """
    mem_cfg = bot.config.get("memory", {}) if hasattr(bot, "config") else {}
    stale_days = int(mem_cfg.get("sweep_stale_days", _DEFAULT_STALE_DAYS))
    cutoff = datetime.now(JST) - timedelta(days=stale_days)

    result: dict[str, int] = {}
    for col in SWEEP_COLLECTIONS:
        deleted = 0
        try:
            items = bot.chroma.get_all(col, limit=_SCAN_LIMIT)
        except Exception as e:
            log.warning("memory sweep: get_all failed for '%s': %s", col, e)
            result[col] = 0
            continue

        for it in items:
            meta = it.get("metadata") or {}
            saved_at_raw = meta.get("saved_at")
            saved_dt = _parse_saved_at(saved_at_raw)
            if saved_dt is None:
                # saved_at が無いエントリは温存
                continue
            try:
                hit_count = int(meta.get("hit_count", 0) or 0)
            except (TypeError, ValueError):
                hit_count = 0
            if hit_count > 0:
                continue
            if saved_dt >= cutoff:
                continue
            try:
                bot.chroma.delete(col, it["id"])
                deleted += 1
            except Exception as e:
                log.debug("memory sweep: delete failed id=%s col=%s: %s", it.get("id"), col, e)

        if deleted:
            log.info("memory sweep: deleted %d stale entries from '%s'", deleted, col)
        result[col] = deleted

    return result
