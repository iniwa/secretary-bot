"""編成モード（team / team_group / slots）。"""

from ._base import _now
from .discs import get_disc

__all__ = [
    "_team_slots_detailed",
    "_team_build_ids",
    "compute_team_conflicts",
    "compute_group_conflicts",
    "get_team",
    "list_teams",
    "create_team",
    "update_team",
    "delete_team",
    "set_team_slot",
    "get_team_group",
    "list_team_groups",
    "create_team_group",
    "update_team_group",
    "delete_team_group",
]


# ---------- Teams (編成モード) ----------

async def _team_slots_detailed(db, team_id: int) -> list[dict]:
    """team のスロット 0..2 を全て返す（空スロットもプレースホルダで埋める）。"""
    rows = await db.fetchall(
        "SELECT ts.position, ts.character_id, ts.build_id, "
        "c.slug AS character_slug, c.name_ja AS character_name_ja, "
        "c.element AS character_element, c.icon_url AS character_icon_url, "
        "b.name AS build_name, b.is_current AS build_is_current "
        "FROM zzz_team_slots ts "
        "LEFT JOIN zzz_characters c ON c.id = ts.character_id "
        "LEFT JOIN zzz_builds b ON b.id = ts.build_id "
        "WHERE ts.team_id = ? ORDER BY ts.position",
        (team_id,),
    )
    by_pos = {r["position"]: r for r in rows}
    result = []
    for pos in range(3):
        r = by_pos.get(pos)
        if r and r.get("character_id"):
            result.append({
                "position": pos,
                "character_id": r["character_id"],
                "character_slug": r["character_slug"],
                "character_name_ja": r["character_name_ja"],
                "character_element": r["character_element"],
                "character_icon_url": r["character_icon_url"],
                "build_id": r.get("build_id"),
                "build_name": r.get("build_name"),
                "build_is_current": bool(r.get("build_is_current") or 0),
            })
        else:
            result.append({
                "position": pos, "character_id": None,
                "character_slug": None, "character_name_ja": None,
                "character_element": None, "character_icon_url": None,
                "build_id": None, "build_name": None, "build_is_current": False,
            })
    return result


async def _team_build_ids(db, team_id: int) -> list[int]:
    rows = await db.fetchall(
        "SELECT build_id FROM zzz_team_slots "
        "WHERE team_id = ? AND build_id IS NOT NULL",
        (team_id,),
    )
    return [r["build_id"] for r in rows]


async def compute_team_conflicts(db, team_id: int) -> list[dict]:
    """team 内 3 ビルドのディスク被り一覧。

    返り値: [{disc_id, slot, set_name_ja, main_stat_name, main_stat_value,
              used_by: [{build_id, character_name_ja, build_name, is_current}]}]
    """
    build_ids = await _team_build_ids(db, team_id)
    if len(build_ids) < 2:
        return []
    placeholders = ",".join(["?"] * len(build_ids))
    rows = await db.fetchall(
        f"SELECT bs.disc_id, COUNT(DISTINCT bs.build_id) AS cnt "
        f"FROM zzz_build_slots bs "
        f"WHERE bs.disc_id IS NOT NULL AND bs.build_id IN ({placeholders}) "
        f"GROUP BY bs.disc_id HAVING cnt >= 2",
        tuple(build_ids),
    )
    if not rows:
        return []
    conflicts = []
    for r in rows:
        disc = await get_disc(db, r["disc_id"])
        used = await db.fetchall(
            f"SELECT bs.build_id, b.name AS build_name, b.is_current, "
            f"c.name_ja AS character_name_ja "
            f"FROM zzz_build_slots bs "
            f"JOIN zzz_builds b ON b.id = bs.build_id "
            f"JOIN zzz_characters c ON c.id = b.character_id "
            f"WHERE bs.disc_id = ? AND bs.build_id IN ({placeholders})",
            (r["disc_id"], *build_ids),
        )
        conflicts.append({
            "disc_id": r["disc_id"],
            "disc": disc,
            "used_by": [
                {
                    "build_id": u["build_id"],
                    "build_name": u["build_name"],
                    "is_current": bool(u["is_current"]),
                    "character_name_ja": u["character_name_ja"],
                }
                for u in used
            ],
        })
    return conflicts


async def compute_group_conflicts(db, group_id: int) -> list[dict]:
    """group 内（複数 team）の全ビルドにわたるディスク被り。"""
    teams = await db.fetchall(
        "SELECT id FROM zzz_teams WHERE group_id = ?", (group_id,),
    )
    if not teams:
        return []
    build_ids: list[int] = []
    build_to_team: dict[int, int] = {}
    for t in teams:
        ids = await _team_build_ids(db, t["id"])
        for bid in ids:
            build_ids.append(bid)
            build_to_team.setdefault(bid, t["id"])
    if len(build_ids) < 2:
        return []
    placeholders = ",".join(["?"] * len(build_ids))
    rows = await db.fetchall(
        f"SELECT bs.disc_id, COUNT(DISTINCT bs.build_id) AS cnt "
        f"FROM zzz_build_slots bs "
        f"WHERE bs.disc_id IS NOT NULL AND bs.build_id IN ({placeholders}) "
        f"GROUP BY bs.disc_id HAVING cnt >= 2",
        tuple(build_ids),
    )
    if not rows:
        return []
    conflicts = []
    for r in rows:
        disc = await get_disc(db, r["disc_id"])
        used = await db.fetchall(
            f"SELECT bs.build_id, b.name AS build_name, b.is_current, "
            f"c.name_ja AS character_name_ja "
            f"FROM zzz_build_slots bs "
            f"JOIN zzz_builds b ON b.id = bs.build_id "
            f"JOIN zzz_characters c ON c.id = b.character_id "
            f"WHERE bs.disc_id = ? AND bs.build_id IN ({placeholders})",
            (r["disc_id"], *build_ids),
        )
        conflicts.append({
            "disc_id": r["disc_id"],
            "disc": disc,
            "used_by": [
                {
                    "build_id": u["build_id"],
                    "build_name": u["build_name"],
                    "is_current": bool(u["is_current"]),
                    "character_name_ja": u["character_name_ja"],
                    "team_id": build_to_team.get(u["build_id"]),
                }
                for u in used
            ],
        })
    return conflicts


async def get_team(db, team_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT * FROM zzz_teams WHERE id = ?", (team_id,),
    )
    if not row:
        return None
    slots = await _team_slots_detailed(db, team_id)
    conflicts = await compute_team_conflicts(db, team_id)
    return {
        "id": row["id"],
        "group_id": row.get("group_id"),
        "name": row["name"],
        "display_order": row.get("display_order") or 0,
        "slots": slots,
        "conflicts": conflicts,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_teams(db, *, group_id: int | None = None,
                     standalone: bool = False) -> list[dict]:
    if standalone:
        rows = await db.fetchall(
            "SELECT id FROM zzz_teams WHERE group_id IS NULL "
            "ORDER BY display_order, id",
        )
    elif group_id is not None:
        rows = await db.fetchall(
            "SELECT id FROM zzz_teams WHERE group_id = ? "
            "ORDER BY display_order, id",
            (group_id,),
        )
    else:
        rows = await db.fetchall(
            "SELECT id FROM zzz_teams ORDER BY group_id, display_order, id"
        )
    return [await get_team(db, r["id"]) for r in rows]


async def create_team(db, *, name: str, group_id: int | None = None,
                      display_order: int = 0) -> int:
    now = _now()
    cursor = await db.execute(
        "INSERT INTO zzz_teams (group_id, name, display_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (group_id, name, display_order, now, now),
    )
    return cursor.lastrowid


async def update_team(db, team_id: int, *, name: str | None = None,
                      display_order: int | None = None) -> int:
    fields, params = [], []
    if name is not None:
        fields.append("name = ?"); params.append(name)
    if display_order is not None:
        fields.append("display_order = ?"); params.append(display_order)
    if not fields:
        return 0
    fields.append("updated_at = ?"); params.append(_now())
    params.append(team_id)
    return await db.execute_returning_rowcount(
        f"UPDATE zzz_teams SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


async def delete_team(db, team_id: int) -> int:
    await db.execute("DELETE FROM zzz_team_slots WHERE team_id = ?", (team_id,))
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_teams WHERE id = ?", (team_id,),
    )


async def set_team_slot(db, team_id: int, position: int, *,
                        character_id: int | None,
                        build_id: int | None) -> None:
    if character_id is None:
        await db.execute(
            "DELETE FROM zzz_team_slots WHERE team_id = ? AND position = ?",
            (team_id, position),
        )
        await db.execute(
            "UPDATE zzz_teams SET updated_at = ? WHERE id = ?",
            (_now(), team_id),
        )
        return
    await db.execute(
        "INSERT INTO zzz_team_slots (team_id, position, character_id, build_id) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(team_id, position) DO UPDATE SET "
        "character_id = excluded.character_id, build_id = excluded.build_id",
        (team_id, position, character_id, build_id),
    )
    await db.execute(
        "UPDATE zzz_teams SET updated_at = ? WHERE id = ?",
        (_now(), team_id),
    )


async def get_team_group(db, group_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT * FROM zzz_team_groups WHERE id = ?", (group_id,),
    )
    if not row:
        return None
    teams = await list_teams(db, group_id=group_id)
    conflicts = await compute_group_conflicts(db, group_id)
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "display_order": row.get("display_order") or 0,
        "teams": teams,
        "conflicts": conflicts,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_team_groups(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT id FROM zzz_team_groups ORDER BY display_order, id"
    )
    return [await get_team_group(db, r["id"]) for r in rows]


async def create_team_group(db, *, name: str, description: str | None = None,
                            display_order: int = 0) -> int:
    now = _now()
    cursor = await db.execute(
        "INSERT INTO zzz_team_groups (name, description, display_order, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, description, display_order, now, now),
    )
    return cursor.lastrowid


async def update_team_group(db, group_id: int, *, name: str | None = None,
                            description: str | None = None,
                            display_order: int | None = None) -> int:
    fields, params = [], []
    if name is not None:
        fields.append("name = ?"); params.append(name)
    if description is not None:
        fields.append("description = ?"); params.append(description)
    if display_order is not None:
        fields.append("display_order = ?"); params.append(display_order)
    if not fields:
        return 0
    fields.append("updated_at = ?"); params.append(_now())
    params.append(group_id)
    return await db.execute_returning_rowcount(
        f"UPDATE zzz_team_groups SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


async def delete_team_group(db, group_id: int) -> int:
    teams = await db.fetchall(
        "SELECT id FROM zzz_teams WHERE group_id = ?", (group_id,),
    )
    for t in teams:
        await delete_team(db, t["id"])
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_team_groups WHERE id = ?", (group_id,),
    )
