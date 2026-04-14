"""ZZZ Disc Manager: SQLite スキーマ管理 + CRUD。

`bot.database` (src/database.py の Database) を経由して操作する。
テーブルは `zzz_` プレフィックスで疎結合管理（_migrations dict には追加しない）。
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Any

JST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS zzz_characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name_ja TEXT NOT NULL,
  element TEXT,
  faction TEXT,
  icon_url TEXT,
  display_order INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_set_masters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name_ja TEXT NOT NULL,
  aliases_json TEXT,
  two_pc_effect TEXT,
  four_pc_effect TEXT
);

CREATE TABLE IF NOT EXISTS zzz_discs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 6),
  set_id INTEGER REFERENCES zzz_set_masters(id),
  main_stat_name TEXT NOT NULL,
  main_stat_value REAL NOT NULL,
  sub_stats_json TEXT NOT NULL,
  source_image_path TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_presets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  character_id INTEGER NOT NULL REFERENCES zzz_characters(id),
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 6),
  preferred_set_ids_json TEXT,
  preferred_main_stats_json TEXT,
  sub_stat_priority_json TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(character_id, slot)
);

CREATE TABLE IF NOT EXISTS zzz_extraction_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  image_path TEXT,
  extracted_json TEXT,
  normalized_json TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zzz_jobs_status ON zzz_extraction_jobs(status, created_at);
"""


async def init_schema(db) -> None:
    """`CREATE TABLE IF NOT EXISTS` で 5 テーブル冪等作成。"""
    await db.db.executescript(_SCHEMA_SQL)
    await db.db.commit()


# ---------- マスタ初期投入 ----------

async def upsert_character(db, *, slug: str, name_ja: str,
                           element: str | None = None,
                           faction: str | None = None,
                           icon_url: str | None = None,
                           display_order: int = 0) -> None:
    existing = await db.fetchone("SELECT id FROM zzz_characters WHERE slug = ?", (slug,))
    if existing:
        return
    await db.execute(
        "INSERT INTO zzz_characters (slug, name_ja, element, faction, icon_url, display_order, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (slug, name_ja, element, faction, icon_url, display_order, _now()),
    )


async def upsert_set_master(db, *, slug: str, name_ja: str,
                            aliases: list[str] | None = None,
                            two_pc_effect: str | None = None,
                            four_pc_effect: str | None = None) -> None:
    existing = await db.fetchone("SELECT id FROM zzz_set_masters WHERE slug = ?", (slug,))
    if existing:
        return
    await db.execute(
        "INSERT INTO zzz_set_masters (slug, name_ja, aliases_json, two_pc_effect, four_pc_effect) "
        "VALUES (?, ?, ?, ?, ?)",
        (slug, name_ja, json.dumps(aliases or [], ensure_ascii=False),
         two_pc_effect, four_pc_effect),
    )


# ---------- 参照系 ----------

async def list_characters(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT id, slug, name_ja, element, faction, icon_url, display_order "
        "FROM zzz_characters ORDER BY display_order, id"
    )
    return rows


async def list_set_masters(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT id, slug, name_ja, aliases_json, two_pc_effect, four_pc_effect "
        "FROM zzz_set_masters ORDER BY id"
    )
    for r in rows:
        r["aliases"] = json.loads(r.pop("aliases_json") or "[]")
    return rows


# ---------- Discs CRUD ----------

def _disc_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "slot": row["slot"],
        "set_id": row["set_id"],
        "main_stat_name": row["main_stat_name"],
        "main_stat_value": row["main_stat_value"],
        "sub_stats": json.loads(row["sub_stats_json"] or "[]"),
        "source_image_path": row["source_image_path"],
        "note": row["note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_discs(db, *, slot: int | None = None,
                     set_id: int | None = None) -> list[dict]:
    conditions = []
    params: list = []
    if slot is not None:
        conditions.append("slot = ?")
        params.append(slot)
    if set_id is not None:
        conditions.append("set_id = ?")
        params.append(set_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await db.fetchall(
        f"SELECT * FROM zzz_discs{where} ORDER BY id DESC", tuple(params),
    )
    return [_disc_row_to_dict(r) for r in rows]


async def get_disc(db, disc_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM zzz_discs WHERE id = ?", (disc_id,))
    return _disc_row_to_dict(row) if row else None


async def insert_disc(db, *, slot: int, set_id: int | None,
                      main_stat_name: str, main_stat_value: float,
                      sub_stats: list[dict],
                      source_image_path: str | None = None,
                      note: str | None = None) -> int:
    now = _now()
    cursor = await db.execute(
        "INSERT INTO zzz_discs (slot, set_id, main_stat_name, main_stat_value, "
        "sub_stats_json, source_image_path, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (slot, set_id, main_stat_name, main_stat_value,
         json.dumps(sub_stats, ensure_ascii=False),
         source_image_path, note, now, now),
    )
    return cursor.lastrowid


async def update_disc(db, disc_id: int, *, slot: int, set_id: int | None,
                      main_stat_name: str, main_stat_value: float,
                      sub_stats: list[dict],
                      source_image_path: str | None = None,
                      note: str | None = None) -> int:
    return await db.execute_returning_rowcount(
        "UPDATE zzz_discs SET slot = ?, set_id = ?, main_stat_name = ?, "
        "main_stat_value = ?, sub_stats_json = ?, source_image_path = ?, "
        "note = ?, updated_at = ? WHERE id = ?",
        (slot, set_id, main_stat_name, main_stat_value,
         json.dumps(sub_stats, ensure_ascii=False),
         source_image_path, note, _now(), disc_id),
    )


async def delete_disc(db, disc_id: int) -> int:
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_discs WHERE id = ?", (disc_id,),
    )


# ---------- Presets ----------

def _preset_row_to_dict(row: dict) -> dict:
    return {
        "character_id": row["character_id"],
        "slot": row["slot"],
        "preferred_set_ids": json.loads(row["preferred_set_ids_json"] or "[]"),
        "preferred_main_stats": json.loads(row["preferred_main_stats_json"] or "[]"),
        "sub_stat_priority": json.loads(row["sub_stat_priority_json"] or "[]"),
        "updated_at": row["updated_at"],
    }


async def list_presets_for_character(db, character_id: int) -> list[dict]:
    rows = await db.fetchall(
        "SELECT character_id, slot, preferred_set_ids_json, "
        "preferred_main_stats_json, sub_stat_priority_json, updated_at "
        "FROM zzz_presets WHERE character_id = ? ORDER BY slot",
        (character_id,),
    )
    return [_preset_row_to_dict(r) for r in rows]


async def list_all_presets(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT character_id, slot, preferred_set_ids_json, "
        "preferred_main_stats_json, sub_stat_priority_json, updated_at "
        "FROM zzz_presets ORDER BY character_id, slot"
    )
    return [_preset_row_to_dict(r) for r in rows]


async def upsert_preset(db, *, character_id: int, slot: int,
                        preferred_set_ids: list[int],
                        preferred_main_stats: list[str],
                        sub_stat_priority: list[dict]) -> None:
    now = _now()
    await db.execute(
        "INSERT INTO zzz_presets (character_id, slot, preferred_set_ids_json, "
        "preferred_main_stats_json, sub_stat_priority_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(character_id, slot) DO UPDATE SET "
        "preferred_set_ids_json = excluded.preferred_set_ids_json, "
        "preferred_main_stats_json = excluded.preferred_main_stats_json, "
        "sub_stat_priority_json = excluded.sub_stat_priority_json, "
        "updated_at = excluded.updated_at",
        (character_id, slot,
         json.dumps(preferred_set_ids), json.dumps(preferred_main_stats, ensure_ascii=False),
         json.dumps(sub_stat_priority, ensure_ascii=False), now),
    )


# ---------- Jobs ----------

def _job_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "source": row["source"],
        "image_path": row["image_path"],
        "extracted_json": json.loads(row["extracted_json"]) if row["extracted_json"] else None,
        "normalized_json": json.loads(row["normalized_json"]) if row["normalized_json"] else None,
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def create_job(db, *, source: str, image_path: str | None = None) -> int:
    now = _now()
    cursor = await db.execute(
        "INSERT INTO zzz_extraction_jobs (status, source, image_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("queued", source, image_path, now, now),
    )
    return cursor.lastrowid


async def get_job(db, job_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM zzz_extraction_jobs WHERE id = ?", (job_id,))
    return _job_row_to_dict(row) if row else None


async def list_jobs(db, *, statuses: list[str] | None = None,
                    limit: int = 100) -> list[dict]:
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        rows = await db.fetchall(
            f"SELECT * FROM zzz_extraction_jobs WHERE status IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT ?",
            tuple(statuses) + (limit,),
        )
    else:
        rows = await db.fetchall(
            "SELECT * FROM zzz_extraction_jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return [_job_row_to_dict(r) for r in rows]


async def update_job(db, job_id: int, *, status: str | None = None,
                     image_path: str | None = None,
                     extracted_json: Any | None = None,
                     normalized_json: Any | None = None,
                     error_message: str | None = None) -> int:
    fields = []
    params: list = []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if image_path is not None:
        fields.append("image_path = ?")
        params.append(image_path)
    if extracted_json is not None:
        fields.append("extracted_json = ?")
        params.append(json.dumps(extracted_json, ensure_ascii=False))
    if normalized_json is not None:
        fields.append("normalized_json = ?")
        params.append(json.dumps(normalized_json, ensure_ascii=False))
    if error_message is not None:
        fields.append("error_message = ?")
        params.append(error_message)
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(job_id)
    return await db.execute_returning_rowcount(
        f"UPDATE zzz_extraction_jobs SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


async def delete_job(db, job_id: int) -> int:
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_extraction_jobs WHERE id = ?", (job_id,),
    )


async def list_jobs_to_resume(db) -> list[dict]:
    """起動時復元: 未完了ジョブをキューに積み直す。"""
    rows = await db.fetchall(
        "SELECT * FROM zzz_extraction_jobs "
        "WHERE status IN ('queued', 'capturing', 'extracting') ORDER BY id"
    )
    return [_job_row_to_dict(r) for r in rows]


async def prune_finished_jobs(db, retention: int = 200) -> int:
    """saved/failed のうち古いものを削除。"""
    rows = await db.fetchall(
        "SELECT id FROM zzz_extraction_jobs WHERE status IN ('saved', 'failed') "
        "ORDER BY id DESC"
    )
    stale = [r["id"] for r in rows[retention:]]
    if not stale:
        return 0
    placeholders = ",".join(["?"] * len(stale))
    return await db.execute_returning_rowcount(
        f"DELETE FROM zzz_extraction_jobs WHERE id IN ({placeholders})",
        tuple(stale),
    )
