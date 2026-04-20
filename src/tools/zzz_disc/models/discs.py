"""Disc インベントリ CRUD + pin/unpin。"""

import json

from ._base import _now, compute_fingerprint

__all__ = [
    "_disc_row_to_dict",
    "list_discs",
    "get_disc",
    "get_disc_by_fingerprint",
    "insert_disc",
    "update_disc",
    "delete_disc",
    "list_builds_using_disc",
    "set_disc_pinned",
    "pin_build_discs",
    "delete_unpinned_discs",
]


# ---------- Discs ----------

def _disc_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "slot": row["slot"],
        "set_id": row["set_id"],
        "main_stat_name": row["main_stat_name"],
        "main_stat_value": row["main_stat_value"],
        "sub_stats": json.loads(row["sub_stats_json"] or "[]"),
        "level": row.get("level") or 0,
        "rarity": row.get("rarity"),
        "fingerprint": row.get("fingerprint"),
        "hoyolab_disc_id": row.get("hoyolab_disc_id"),
        "icon_url": row.get("icon_url"),
        "name": row.get("name"),
        "is_pinned": bool(row.get("is_pinned")),
        "source_image_path": row.get("source_image_path"),
        "note": row.get("note"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def list_discs(db, *, slot: int | None = None,
                     set_id: int | None = None) -> list[dict]:
    conditions = []
    params: list = []
    if slot is not None:
        conditions.append("d.slot = ?")
        params.append(slot)
    if set_id is not None:
        conditions.append("d.set_id = ?")
        params.append(set_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await db.fetchall(
        f"SELECT d.*, s.name_ja AS set_name_ja, s.slug AS set_slug "
        f"FROM zzz_discs d LEFT JOIN zzz_set_masters s ON s.id = d.set_id"
        f"{where} ORDER BY d.id DESC", tuple(params),
    )
    result = []
    for r in rows:
        out = _disc_row_to_dict(r)
        out["set_name_ja"] = r.get("set_name_ja")
        out["set_slug"] = r.get("set_slug")
        result.append(out)
    return result


async def get_disc(db, disc_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT d.*, s.name_ja AS set_name_ja, s.slug AS set_slug "
        "FROM zzz_discs d LEFT JOIN zzz_set_masters s ON s.id = d.set_id "
        "WHERE d.id = ?", (disc_id,),
    )
    if not row:
        return None
    out = _disc_row_to_dict(row)
    out["set_name_ja"] = row.get("set_name_ja")
    out["set_slug"] = row.get("set_slug")
    return out


async def get_disc_by_fingerprint(db, fingerprint: str) -> dict | None:
    row = await db.fetchone("SELECT * FROM zzz_discs WHERE fingerprint = ?", (fingerprint,))
    return _disc_row_to_dict(row) if row else None


async def insert_disc(db, *, slot: int, set_id: int | None,
                      main_stat_name: str, main_stat_value: float,
                      sub_stats: list[dict],
                      level: int = 0,
                      rarity: str | None = None,
                      hoyolab_disc_id: str | None = None,
                      icon_url: str | None = None,
                      name: str | None = None,
                      source_image_path: str | None = None,
                      note: str | None = None,
                      owner_character_id: int | None = None) -> int:
    """既存検索の優先順位:
    1. hoyolab_disc_id 一致 → 強化スナップショットなので最新値で UPDATE
    2. fingerprint 一致 → 同一内容の手動登録/旧データ、既存 id を返す
    どちらにも該当しなければ新規 INSERT。

    HoYoLAB の disc.id はキャラ間で同値を返すことがあるため、
    owner_character_id が指定されたとき、hoyolab_disc_id / fingerprint で
    ヒットした既存行が「他キャラの current build から参照中」ならマッチを
    キャンセルして新規 INSERT に落とす（ディスク行の共有化を防ぐ／既存の
    共有化を次回同期で自然に解消する）。
    """
    fp = compute_fingerprint(slot, set_id, main_stat_name, main_stat_value, sub_stats)

    async def _used_by_other_character(disc_id: int) -> bool:
        if owner_character_id is None:
            return False
        row = await db.fetchone(
            "SELECT 1 FROM zzz_build_slots bs "
            "JOIN zzz_builds b ON b.id = bs.build_id "
            "WHERE bs.disc_id = ? AND b.is_current = 1 "
            "AND b.character_id != ? LIMIT 1",
            (disc_id, owner_character_id),
        )
        return row is not None

    if hoyolab_disc_id:
        existing = None
        # 同キャラ current が参照中の行を優先（複数行があった場合の非決定性を排除）
        if owner_character_id is not None:
            existing = await db.fetchone(
                "SELECT d.id FROM zzz_discs d "
                "JOIN zzz_build_slots bs ON bs.disc_id = d.id "
                "JOIN zzz_builds b ON b.id = bs.build_id "
                "WHERE d.hoyolab_disc_id = ? AND b.character_id = ? "
                "AND b.is_current = 1 LIMIT 1",
                (hoyolab_disc_id, owner_character_id),
            )
        if not existing:
            existing = await db.fetchone(
                "SELECT id FROM zzz_discs WHERE hoyolab_disc_id = ?",
                (hoyolab_disc_id,),
            )
            if existing and await _used_by_other_character(existing["id"]):
                existing = None
        if existing:
            # fingerprint UNIQUE と衝突しないかを事前確認（異常データ保護）
            clash = await db.fetchone(
                "SELECT id FROM zzz_discs WHERE fingerprint = ? AND id != ?",
                (fp, existing["id"]),
            )
            if not clash:
                await db.execute(
                    "UPDATE zzz_discs SET slot = ?, set_id = ?, "
                    "main_stat_name = ?, main_stat_value = ?, sub_stats_json = ?, "
                    "level = ?, rarity = ?, fingerprint = ?, "
                    "icon_url = COALESCE(?, icon_url), name = COALESCE(?, name), "
                    "updated_at = ? WHERE id = ?",
                    (slot, set_id, main_stat_name, main_stat_value,
                     json.dumps(sub_stats, ensure_ascii=False),
                     level, rarity, fp, icon_url, name, _now(), existing["id"]),
                )
            return existing["id"]
    existing = await db.fetchone(
        "SELECT id, icon_url, name FROM zzz_discs WHERE fingerprint = ?", (fp,))
    if existing and await _used_by_other_character(existing["id"]):
        existing = None
    if existing:
        sets, params = [], []
        if icon_url and not existing.get("icon_url"):
            sets.append("icon_url = ?"); params.append(icon_url)
        if name and not existing.get("name"):
            sets.append("name = ?"); params.append(name)
        if hoyolab_disc_id:
            sets.append("hoyolab_disc_id = COALESCE(hoyolab_disc_id, ?)")
            params.append(hoyolab_disc_id)
        if sets:
            params.append(existing["id"])
            await db.execute(
                f"UPDATE zzz_discs SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
        return existing["id"]
    now = _now()
    cursor = await db.execute(
        "INSERT INTO zzz_discs (slot, set_id, main_stat_name, main_stat_value, "
        "sub_stats_json, level, rarity, fingerprint, hoyolab_disc_id, icon_url, name, "
        "source_image_path, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (slot, set_id, main_stat_name, main_stat_value,
         json.dumps(sub_stats, ensure_ascii=False),
         level, rarity, fp, hoyolab_disc_id, icon_url, name,
         source_image_path, note, now, now),
    )
    return cursor.lastrowid


async def update_disc(db, disc_id: int, *, slot: int, set_id: int | None,
                      main_stat_name: str, main_stat_value: float,
                      sub_stats: list[dict],
                      level: int | None = None,
                      rarity: str | None = None,
                      source_image_path: str | None = None,
                      note: str | None = None) -> int:
    fp = compute_fingerprint(slot, set_id, main_stat_name, main_stat_value, sub_stats)
    # 既存の他 disc と fingerprint が衝突するなら UPDATE 拒否（CRUD 側で責任）
    clash = await db.fetchone(
        "SELECT id FROM zzz_discs WHERE fingerprint = ? AND id != ?", (fp, disc_id)
    )
    if clash:
        return -1  # caller 側で別ハンドリング（Phase 1 互換の簡易戻り値）
    return await db.execute_returning_rowcount(
        "UPDATE zzz_discs SET slot = ?, set_id = ?, main_stat_name = ?, "
        "main_stat_value = ?, sub_stats_json = ?, level = COALESCE(?, level), "
        "rarity = COALESCE(?, rarity), fingerprint = ?, "
        "source_image_path = ?, note = ?, updated_at = ? WHERE id = ?",
        (slot, set_id, main_stat_name, main_stat_value,
         json.dumps(sub_stats, ensure_ascii=False),
         level, rarity, fp,
         source_image_path, note, _now(), disc_id),
    )


async def delete_disc(db, disc_id: int) -> int:
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_discs WHERE id = ?", (disc_id,),
    )


async def list_builds_using_disc(db, disc_id: int) -> list[dict]:
    rows = await db.fetchall(
        "SELECT b.id as build_id, b.name, b.is_current, b.character_id, "
        "c.slug as character_slug, c.name_ja as character_name_ja, bs.slot "
        "FROM zzz_build_slots bs "
        "JOIN zzz_builds b ON b.id = bs.build_id "
        "JOIN zzz_characters c ON c.id = b.character_id "
        "WHERE bs.disc_id = ? ORDER BY b.is_current DESC, b.id",
        (disc_id,),
    )
    return rows


async def set_disc_pinned(db, disc_id: int, pinned: bool) -> int:
    return await db.execute_returning_rowcount(
        "UPDATE zzz_discs SET is_pinned = ?, updated_at = ? WHERE id = ?",
        (1 if pinned else 0, _now(), disc_id),
    )


async def pin_build_discs(db, build_id: int) -> int:
    """指定ビルドの装備中ディスク全てに is_pinned=1 を立てる。返値: ピンされた枚数。"""
    row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM zzz_build_slots bs "
        "JOIN zzz_discs d ON d.id = bs.disc_id "
        "WHERE bs.build_id = ? AND bs.disc_id IS NOT NULL AND d.is_pinned = 0",
        (build_id,),
    )
    n = int(row["n"] if row else 0)
    if n == 0:
        return 0
    await db.execute(
        "UPDATE zzz_discs SET is_pinned = 1, updated_at = ? "
        "WHERE id IN (SELECT disc_id FROM zzz_build_slots "
        "WHERE build_id = ? AND disc_id IS NOT NULL)",
        (_now(), build_id),
    )
    return n


async def delete_unpinned_discs(db) -> int:
    """非ピンディスクを削除。参照している build_slots は先に disc_id を NULL 化。"""
    await db.execute(
        "UPDATE zzz_build_slots SET disc_id = NULL "
        "WHERE disc_id IN (SELECT id FROM zzz_discs WHERE is_pinned = 0)",
    )
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_discs WHERE is_pinned = 0",
    )
