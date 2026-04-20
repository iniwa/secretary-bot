"""ビルド CRUD + スロット割当 + 共有検出。"""

import json

from ._base import _now
from .discs import _disc_row_to_dict, get_disc, list_builds_using_disc

__all__ = [
    "_build_row_to_dict",
    "list_builds_for_character",
    "list_all_builds",
    "get_build",
    "get_current_build",
    "get_build_slots",
    "upsert_current_build",
    "clear_build_slots",
    "set_build_slot",
    "copy_build_as_preset",
    "update_build_meta",
    "delete_build",
    "list_all_disc_usage",
    "find_shared_discs",
]


# ---------- Builds ----------

def _build_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "character_id": row["character_id"],
        "name": row["name"],
        "tag": row.get("tag"),
        "rank": row.get("rank"),
        "notes": row.get("notes"),
        "is_current": bool(row["is_current"]),
        "stats": json.loads(row["stats_json"]) if row.get("stats_json") else {},
        "w_engine": json.loads(row["w_engine_json"]) if row.get("w_engine_json") else None,
        "synced_at": row.get("synced_at"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_builds_for_character(db, character_id: int) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM zzz_builds WHERE character_id = ? "
        "ORDER BY is_current DESC, id DESC",
        (character_id,),
    )
    return [_build_row_to_dict(r) for r in rows]


async def list_all_builds(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM zzz_builds ORDER BY character_id, is_current DESC, id"
    )
    return [_build_row_to_dict(r) for r in rows]


async def get_build(db, build_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM zzz_builds WHERE id = ?", (build_id,))
    return _build_row_to_dict(row) if row else None


async def get_current_build(db, character_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT * FROM zzz_builds WHERE character_id = ? AND is_current = 1",
        (character_id,),
    )
    return _build_row_to_dict(row) if row else None


async def get_build_slots(db, build_id: int) -> list[dict]:
    """build_id のスロット 1..6 を全部返す（disc 情報も join）。"""
    rows = await db.fetchall(
        "SELECT bs.slot, bs.disc_id, d.* "
        "FROM zzz_build_slots bs LEFT JOIN zzz_discs d ON d.id = bs.disc_id "
        "WHERE bs.build_id = ? ORDER BY bs.slot",
        (build_id,),
    )
    result = []
    for r in rows:
        disc = _disc_row_to_dict(r) if r.get("disc_id") else None
        result.append({"slot": r["slot"], "disc_id": r.get("disc_id"), "disc": disc})
    return result


async def upsert_current_build(db, *, character_id: int,
                               name: str = "現在の装備",
                               stats: dict | None = None,
                               w_engine: dict | None = None,
                               synced_at: str | None = None) -> int:
    """character_id の is_current=1 ビルドを更新（なければ作成）。"""
    now = _now()
    synced_at = synced_at or now
    stats_j = json.dumps(stats or {}, ensure_ascii=False)
    weng_j = json.dumps(w_engine, ensure_ascii=False) if w_engine else None
    existing = await db.fetchone(
        "SELECT id FROM zzz_builds WHERE character_id = ? AND is_current = 1",
        (character_id,),
    )
    if existing:
        await db.execute(
            "UPDATE zzz_builds SET name = ?, stats_json = ?, w_engine_json = ?, "
            "synced_at = ?, updated_at = ? WHERE id = ?",
            (name, stats_j, weng_j, synced_at, now, existing["id"]),
        )
        return existing["id"]
    cursor = await db.execute(
        "INSERT INTO zzz_builds (character_id, name, is_current, stats_json, "
        "w_engine_json, synced_at, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
        (character_id, name, stats_j, weng_j, synced_at, now, now),
    )
    return cursor.lastrowid


async def clear_build_slots(db, build_id: int) -> None:
    await db.execute("DELETE FROM zzz_build_slots WHERE build_id = ?", (build_id,))


async def set_build_slot(db, build_id: int, slot: int, disc_id: int | None) -> None:
    await db.execute(
        "INSERT INTO zzz_build_slots (build_id, slot, disc_id) VALUES (?, ?, ?) "
        "ON CONFLICT(build_id, slot) DO UPDATE SET disc_id = excluded.disc_id",
        (build_id, slot, disc_id),
    )


async def copy_build_as_preset(db, source_build_id: int, *,
                               name: str, tag: str | None = None,
                               rank: str | None = None,
                               notes: str | None = None) -> int:
    """既存ビルド（主に is_current）をプリセット複製。slot 配列も複製。"""
    src = await get_build(db, source_build_id)
    if not src:
        raise ValueError("source build not found")
    now = _now()
    weng = src.get("w_engine")
    weng_j = json.dumps(weng, ensure_ascii=False) if weng else None
    cursor = await db.execute(
        "INSERT INTO zzz_builds (character_id, name, tag, rank, notes, is_current, "
        "stats_json, w_engine_json, synced_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
        (src["character_id"], name, tag, rank, notes,
         json.dumps(src.get("stats") or {}, ensure_ascii=False),
         weng_j, src.get("synced_at"), now, now),
    )
    new_id = cursor.lastrowid
    slots = await get_build_slots(db, source_build_id)
    for s in slots:
        if s["disc_id"]:
            await set_build_slot(db, new_id, s["slot"], s["disc_id"])
    return new_id


async def update_build_meta(db, build_id: int, *,
                            name: str | None = None,
                            tag: str | None = None,
                            rank: str | None = None,
                            notes: str | None = None) -> int:
    fields = []
    params: list = []
    if name is not None:
        fields.append("name = ?"); params.append(name)
    if tag is not None:
        fields.append("tag = ?"); params.append(tag)
    if rank is not None:
        fields.append("rank = ?"); params.append(rank)
    if notes is not None:
        fields.append("notes = ?"); params.append(notes)
    if not fields:
        return 0
    fields.append("updated_at = ?"); params.append(_now())
    params.append(build_id)
    return await db.execute_returning_rowcount(
        f"UPDATE zzz_builds SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


async def delete_build(db, build_id: int) -> int:
    # is_current は削除しない（sync で再生成されるべき）
    row = await db.fetchone("SELECT is_current FROM zzz_builds WHERE id = ?", (build_id,))
    if not row:
        return 0
    if row["is_current"]:
        raise ValueError("current build cannot be deleted")
    await db.execute("DELETE FROM zzz_build_slots WHERE build_id = ?", (build_id,))
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_builds WHERE id = ?", (build_id,),
    )


async def list_all_disc_usage(db) -> list[dict]:
    """全 disc × 利用ビルドのフラットな対応表を返す（フィルタUI 用）。

    結果の各行: disc_id, build_id, character_id, character_slug,
                character_name_ja, build_name, is_current, slot
    disc が未割当の場合は出現しない（フロントで discs と差分を取る想定）。
    """
    rows = await db.fetchall(
        "SELECT bs.disc_id, bs.slot, b.id AS build_id, b.name AS build_name, "
        "b.is_current, c.id AS character_id, c.slug AS character_slug, "
        "c.name_ja AS character_name_ja "
        "FROM zzz_build_slots bs "
        "JOIN zzz_builds b ON b.id = bs.build_id "
        "JOIN zzz_characters c ON c.id = b.character_id "
        "WHERE bs.disc_id IS NOT NULL "
        "ORDER BY c.display_order, c.id, b.is_current DESC, b.id"
    )
    return rows


async def find_shared_discs(db) -> list[dict]:
    """複数ビルドで使われている disc を返す。"""
    rows = await db.fetchall(
        "SELECT bs.disc_id, COUNT(DISTINCT bs.build_id) AS usage_count "
        "FROM zzz_build_slots bs WHERE bs.disc_id IS NOT NULL "
        "GROUP BY bs.disc_id HAVING usage_count >= 2"
    )
    result = []
    for r in rows:
        disc = await get_disc(db, r["disc_id"])
        builds = await list_builds_using_disc(db, r["disc_id"])
        result.append({
            "disc": disc,
            "usage_count": r["usage_count"],
            "used_by": builds,
        })
    return result
