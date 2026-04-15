"""ZZZ Disc Manager: SQLite スキーマ管理 + CRUD（ビルド中心モデル）。

- zzz_characters / zzz_set_masters: マスタ
- zzz_discs: インベントリ（fingerprint で重複排除）
- zzz_builds: キャラ別ビルド（is_current=1 が「現在の装備」、0 がプリセット）
- zzz_build_slots: ビルド × 部位 → disc_id
- zzz_hoyolab_accounts: HoYoLAB cookie（平文）
- zzz_extraction_jobs: VLM 抽出キュー
"""

import hashlib
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
  hoyolab_agent_id TEXT,
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
  level INTEGER DEFAULT 0,
  rarity TEXT,
  fingerprint TEXT,
  hoyolab_disc_id TEXT,
  source_image_path TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_builds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  character_id INTEGER NOT NULL REFERENCES zzz_characters(id),
  name TEXT NOT NULL,
  tag TEXT,
  rank TEXT,
  notes TEXT,
  is_current INTEGER NOT NULL DEFAULT 0,
  stats_json TEXT,
  synced_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zzz_builds_char ON zzz_builds(character_id, is_current);
CREATE UNIQUE INDEX IF NOT EXISTS idx_zzz_builds_current ON zzz_builds(character_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS zzz_build_slots (
  build_id INTEGER NOT NULL REFERENCES zzz_builds(id) ON DELETE CASCADE,
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 6),
  disc_id INTEGER REFERENCES zzz_discs(id),
  PRIMARY KEY(build_id, slot)
);
CREATE INDEX IF NOT EXISTS idx_zzz_build_slots_disc ON zzz_build_slots(disc_id);

CREATE TABLE IF NOT EXISTS zzz_hoyolab_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE NOT NULL,
  region TEXT NOT NULL,
  ltuid_v2 TEXT NOT NULL,
  ltoken_v2 TEXT NOT NULL,
  nickname TEXT,
  last_synced_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
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


async def _maybe_add_column(db, table: str, column: str, coldef: str) -> None:
    info = await db.fetchall(f"PRAGMA table_info({table})")
    if column not in {r["name"] for r in info}:
        await db.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")
        await db.db.commit()


async def init_schema(db) -> None:
    """CREATE TABLE IF NOT EXISTS + 既存 DB へのカラム追加 + インデックス。

    インデックス作成は fingerprint 列が確実に存在した後に行う。
    """
    await db.db.executescript(_SCHEMA_SQL)
    await db.db.commit()
    # 既存環境への追加カラム（fingerprint等は Phase 1 時点では無かった）
    await _maybe_add_column(db, "zzz_discs", "level", "INTEGER DEFAULT 0")
    await _maybe_add_column(db, "zzz_discs", "rarity", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "fingerprint", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "hoyolab_disc_id", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "icon_url", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "name", "TEXT")
    await _maybe_add_column(db, "zzz_characters", "hoyolab_agent_id", "TEXT")
    await _maybe_add_column(db, "zzz_characters", "recommended_substats_json", "TEXT")
    # HoYoLAB 自動ログイン用（平文・自宅 Pi 前提）
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "email", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "password", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "auto_login_enabled", "INTEGER DEFAULT 0")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "last_auto_login_at", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "last_auto_login_error", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "account_mid_v2", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "account_id_v2", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "cookie_token_v2", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "ltmid_v2", "TEXT")
    # fingerprint 用 UNIQUE インデックスは列追加後に作成
    await db.db.executescript(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_zzz_discs_fingerprint "
        "ON zzz_discs(fingerprint) WHERE fingerprint IS NOT NULL;"
    )
    await db.db.commit()
    # 既存ディスクに fingerprint を埋める
    await _backfill_fingerprints(db)


def compute_fingerprint(slot: int, set_id: int | None,
                        main_stat_name: str, main_stat_value: float,
                        sub_stats: list[dict]) -> str:
    """物理的に同じディスクを識別するための決定論的ハッシュ。

    (slot, set_id, main_stat, 各サブステ{name,value,upgrades}) から計算。
    sub_stats は name の辞書順に正規化。
    """
    # NOTE: upgrades (強化回数) は value から導出される派生値のため
    # fingerprint には含めない（再同期の冪等性を保つ）
    subs_norm = sorted(
        [
            [s.get("name") or "",
             round(float(s.get("value", 0) or 0), 3)]
            for s in (sub_stats or [])
        ],
        key=lambda x: x[0],
    )
    payload = json.dumps(
        [slot, set_id, main_stat_name,
         round(float(main_stat_value or 0), 3), subs_norm],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def _backfill_fingerprints(db) -> None:
    rows = await db.fetchall(
        "SELECT id, slot, set_id, main_stat_name, main_stat_value, sub_stats_json "
        "FROM zzz_discs WHERE fingerprint IS NULL OR fingerprint = ''"
    )
    for r in rows:
        subs = json.loads(r["sub_stats_json"] or "[]")
        fp = compute_fingerprint(
            r["slot"], r["set_id"], r["main_stat_name"],
            r["main_stat_value"], subs,
        )
        try:
            await db.execute(
                "UPDATE zzz_discs SET fingerprint = ? WHERE id = ?",
                (fp, r["id"]),
            )
        except Exception:
            # 既存ディスクに重複がある場合は先に見つけたものを残し、後続はスキップ
            pass


# ---------- マスタ ----------

async def upsert_character(db, *, slug: str, name_ja: str,
                           element: str | None = None,
                           faction: str | None = None,
                           icon_url: str | None = None,
                           display_order: int = 0,
                           hoyolab_agent_id: str | None = None) -> None:
    existing = await db.fetchone(
        "SELECT id, icon_url, element, faction, hoyolab_agent_id "
        "FROM zzz_characters WHERE slug = ?", (slug,))
    if existing:
        sets, params = [], []
        if hoyolab_agent_id and not existing.get("hoyolab_agent_id"):
            sets.append("hoyolab_agent_id = ?"); params.append(hoyolab_agent_id)
        if icon_url and not existing.get("icon_url"):
            sets.append("icon_url = ?"); params.append(icon_url)
        if element and not existing.get("element"):
            sets.append("element = ?"); params.append(element)
        if faction and not existing.get("faction"):
            sets.append("faction = ?"); params.append(faction)
        if sets:
            params.append(existing["id"])
            await db.execute(
                f"UPDATE zzz_characters SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
        return
    await db.execute(
        "INSERT INTO zzz_characters (slug, name_ja, element, faction, icon_url, "
        "display_order, hoyolab_agent_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (slug, name_ja, element, faction, icon_url, display_order,
         hoyolab_agent_id, _now()),
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


async def find_or_create_set_by_name(db, name_ja: str, *,
                                     two_pc_effect: str | None = None,
                                     four_pc_effect: str | None = None) -> int:
    """HoYoLAB 同期時、既知のセット名をそのまま upsert。見つからなければ新規作成。
    既存に effect 文字列が無ければ今回取得分で補填する。"""
    row = await db.fetchone(
        "SELECT id, two_pc_effect, four_pc_effect FROM zzz_set_masters WHERE name_ja = ?",
        (name_ja,))
    if row:
        sets, params = [], []
        if two_pc_effect and not row.get("two_pc_effect"):
            sets.append("two_pc_effect = ?"); params.append(two_pc_effect)
        if four_pc_effect and not row.get("four_pc_effect"):
            sets.append("four_pc_effect = ?"); params.append(four_pc_effect)
        if sets:
            params.append(row["id"])
            await db.execute(
                f"UPDATE zzz_set_masters SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
        return row["id"]
    # aliases 検索
    all_rows = await db.fetchall(
        "SELECT id, aliases_json FROM zzz_set_masters"
    )
    for r in all_rows:
        aliases = json.loads(r["aliases_json"] or "[]")
        if name_ja in aliases:
            return r["id"]
    # 新規作成（slug は name_ja のハッシュベース）
    slug = "auto-" + hashlib.sha1(name_ja.encode("utf-8")).hexdigest()[:12]
    cursor = await db.execute(
        "INSERT INTO zzz_set_masters (slug, name_ja, aliases_json, "
        "two_pc_effect, four_pc_effect) VALUES (?, ?, ?, ?, ?)",
        (slug, name_ja, "[]", two_pc_effect, four_pc_effect),
    )
    return cursor.lastrowid


# ---------- 参照系 ----------

def _decode_char_row(row: dict) -> dict:
    if row is None:
        return row
    raw = row.pop("recommended_substats_json", None)
    try:
        row["recommended_substats"] = json.loads(raw) if raw else []
    except Exception:
        row["recommended_substats"] = []
    return row


async def list_characters(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT id, slug, name_ja, element, faction, icon_url, display_order, hoyolab_agent_id, recommended_substats_json "
        "FROM zzz_characters ORDER BY display_order, id"
    )
    return [_decode_char_row(r) for r in rows]


async def update_character_recommended_substats(db, character_id: int,
                                                stats: list[str]) -> int:
    payload = json.dumps(list(stats), ensure_ascii=False)
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET recommended_substats_json = ? WHERE id = ?",
        (payload, character_id),
    )


async def list_characters_with_build_stats(db) -> list[dict]:
    """キャラ一覧に has_current_build と preset_count を付与して返す。"""
    chars = await list_characters(db)
    build_rows = await db.fetchall(
        "SELECT character_id, is_current FROM zzz_builds"
    )
    stats: dict[int, dict] = {}
    for r in build_rows:
        cid = r["character_id"]
        s = stats.setdefault(cid, {"has_current": False, "preset": 0})
        if r["is_current"]:
            s["has_current"] = True
        else:
            s["preset"] += 1
    for c in chars:
        s = stats.get(c["id"], {"has_current": False, "preset": 0})
        c["has_current_build"] = s["has_current"]
        c["preset_count"] = s["preset"]
    return chars


async def get_character(db, character_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT id, slug, name_ja, element, faction, icon_url, display_order, hoyolab_agent_id, recommended_substats_json "
        "FROM zzz_characters WHERE id = ?", (character_id,),
    )
    return _decode_char_row(dict(row)) if row else None


async def get_character_by_slug(db, slug: str) -> dict | None:
    row = await db.fetchone(
        "SELECT id, slug, name_ja, element, faction, icon_url, display_order, hoyolab_agent_id, recommended_substats_json "
        "FROM zzz_characters WHERE slug = ?", (slug,),
    )
    return _decode_char_row(dict(row)) if row else None


async def list_set_masters(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT id, slug, name_ja, aliases_json, two_pc_effect, four_pc_effect "
        "FROM zzz_set_masters ORDER BY id"
    )
    for r in rows:
        r["aliases"] = json.loads(r.pop("aliases_json") or "[]")
    return rows


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
                      note: str | None = None) -> int:
    """fingerprint で既存検索して重複排除。既存があれば既存 id を返す。
    既存に icon_url / name が無く今回取得できた場合は補填する。"""
    fp = compute_fingerprint(slot, set_id, main_stat_name, main_stat_value, sub_stats)
    existing = await db.fetchone(
        "SELECT id, icon_url, name FROM zzz_discs WHERE fingerprint = ?", (fp,))
    if existing:
        sets, params = [], []
        if icon_url and not existing.get("icon_url"):
            sets.append("icon_url = ?"); params.append(icon_url)
        if name and not existing.get("name"):
            sets.append("name = ?"); params.append(name)
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
                               synced_at: str | None = None) -> int:
    """character_id の is_current=1 ビルドを更新（なければ作成）。"""
    now = _now()
    synced_at = synced_at or now
    existing = await db.fetchone(
        "SELECT id FROM zzz_builds WHERE character_id = ? AND is_current = 1",
        (character_id,),
    )
    if existing:
        await db.execute(
            "UPDATE zzz_builds SET name = ?, stats_json = ?, synced_at = ?, "
            "updated_at = ? WHERE id = ?",
            (name, json.dumps(stats or {}, ensure_ascii=False), synced_at,
             now, existing["id"]),
        )
        return existing["id"]
    cursor = await db.execute(
        "INSERT INTO zzz_builds (character_id, name, is_current, stats_json, "
        "synced_at, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (character_id, name, json.dumps(stats or {}, ensure_ascii=False),
         synced_at, now, now),
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
    cursor = await db.execute(
        "INSERT INTO zzz_builds (character_id, name, tag, rank, notes, is_current, "
        "stats_json, synced_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (src["character_id"], name, tag, rank, notes,
         json.dumps(src.get("stats") or {}, ensure_ascii=False),
         src.get("synced_at"), now, now),
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


# ---------- HoYoLAB アカウント ----------

async def get_hoyolab_account(db) -> dict | None:
    """単一アカウント想定（自宅 Pi 用途）。"""
    row = await db.fetchone(
        "SELECT id, uid, region, ltuid_v2, ltoken_v2, nickname, last_synced_at, "
        "email, password, auto_login_enabled, last_auto_login_at, last_auto_login_error, "
        "account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2 "
        "FROM zzz_hoyolab_accounts ORDER BY id LIMIT 1"
    )
    return dict(row) if row else None


async def upsert_hoyolab_account(db, *, uid: str, region: str,
                                 ltuid_v2: str, ltoken_v2: str,
                                 nickname: str | None = None,
                                 email: str | None = None,
                                 password: str | None = None,
                                 auto_login_enabled: bool | None = None,
                                 account_mid_v2: str | None = None,
                                 account_id_v2: str | None = None,
                                 cookie_token_v2: str | None = None,
                                 ltmid_v2: str | None = None) -> None:
    """主要 cookies と任意で認証情報・追加 cookies を upsert。

    credentials・追加 cookies は None のとき既存値を維持する。
    auto_login_enabled も None のとき既存値維持。
    """
    now = _now()
    auto_flag = None if auto_login_enabled is None else (1 if auto_login_enabled else 0)
    existing = await db.fetchone(
        "SELECT id FROM zzz_hoyolab_accounts WHERE uid = ?", (uid,)
    )
    if existing:
        await db.execute(
            "UPDATE zzz_hoyolab_accounts SET region = ?, ltuid_v2 = ?, ltoken_v2 = ?, "
            "nickname = COALESCE(?, nickname), "
            "email = COALESCE(?, email), "
            "password = COALESCE(?, password), "
            "auto_login_enabled = COALESCE(?, auto_login_enabled), "
            "account_mid_v2 = COALESCE(?, account_mid_v2), "
            "account_id_v2 = COALESCE(?, account_id_v2), "
            "cookie_token_v2 = COALESCE(?, cookie_token_v2), "
            "ltmid_v2 = COALESCE(?, ltmid_v2), "
            "updated_at = ? WHERE id = ?",
            (region, ltuid_v2, ltoken_v2, nickname,
             email, password, auto_flag,
             account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2,
             now, existing["id"]),
        )
        return
    await db.execute(
        "INSERT INTO zzz_hoyolab_accounts (uid, region, ltuid_v2, ltoken_v2, "
        "nickname, email, password, auto_login_enabled, "
        "account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, region, ltuid_v2, ltoken_v2, nickname, email, password,
         auto_flag if auto_flag is not None else 0,
         account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2,
         now, now),
    )


async def update_hoyolab_cookies(db, *, uid: str,
                                 ltuid_v2: str, ltoken_v2: str,
                                 account_mid_v2: str | None = None,
                                 account_id_v2: str | None = None,
                                 cookie_token_v2: str | None = None,
                                 ltmid_v2: str | None = None,
                                 error: str | None = None) -> None:
    """自動ログイン成功時に cookies のみ更新（credentials は触らない）。"""
    await db.execute(
        "UPDATE zzz_hoyolab_accounts SET ltuid_v2 = ?, ltoken_v2 = ?, "
        "account_mid_v2 = COALESCE(?, account_mid_v2), "
        "account_id_v2 = COALESCE(?, account_id_v2), "
        "cookie_token_v2 = COALESCE(?, cookie_token_v2), "
        "ltmid_v2 = COALESCE(?, ltmid_v2), "
        "last_auto_login_at = ?, last_auto_login_error = ?, updated_at = ? "
        "WHERE uid = ?",
        (ltuid_v2, ltoken_v2,
         account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2,
         _now(), error, _now(), uid),
    )


async def record_auto_login_error(db, *, uid: str, error: str) -> None:
    await db.execute(
        "UPDATE zzz_hoyolab_accounts SET last_auto_login_at = ?, "
        "last_auto_login_error = ?, updated_at = ? WHERE uid = ?",
        (_now(), error, _now(), uid),
    )


async def update_hoyolab_synced(db, uid: str) -> None:
    await db.execute(
        "UPDATE zzz_hoyolab_accounts SET last_synced_at = ? WHERE uid = ?",
        (_now(), uid),
    )


async def delete_characters_without_builds(db) -> dict:
    """ビルドが 1 件も無いキャラを削除する（current/preset 両方なし）。

    HoYoLAB 同期で所持キャラのみ current が作られる前提で、
    所持していないシードキャラ行を掃除する用途。

    Returns: {'chars': n, 'deleted_slugs': [...]}
    """
    rows = await db.fetchall(
        "SELECT c.id, c.slug, c.name_ja FROM zzz_characters c "
        "LEFT JOIN zzz_builds b ON b.character_id = c.id "
        "WHERE b.id IS NULL"
    )
    if not rows:
        return {"chars": 0, "deleted": []}
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    await db.execute(
        f"DELETE FROM zzz_characters WHERE id IN ({placeholders})",
        tuple(ids),
    )
    return {
        "chars": len(ids),
        "deleted": [{"slug": r["slug"], "name_ja": r["name_ja"]} for r in rows],
    }


async def reset_hoyolab_synced_data(db) -> dict:
    """HoYoLAB 同期で作られたデータを一掃する。

    - slug が hoyolab-* の重複キャラを削除（元のプリセットキャラは残す）
    - 全 current ビルドと build_slots を削除（再同期で復活）
    - 全 disc を削除（HoYoLAB 同期由来が大半。手動登録分があれば失われる点に注意）

    Returns: {'chars': n, 'builds': n, 'discs': n}
    """
    c = await db.execute_returning_rowcount(
        "DELETE FROM zzz_build_slots WHERE build_id IN "
        "(SELECT id FROM zzz_builds WHERE is_current = 1)"
    )
    b = await db.execute_returning_rowcount(
        "DELETE FROM zzz_builds WHERE is_current = 1"
    )
    d = await db.execute_returning_rowcount("DELETE FROM zzz_discs")
    ch = await db.execute_returning_rowcount(
        "DELETE FROM zzz_characters WHERE slug LIKE 'hoyolab-%'"
    )
    return {"slots": c, "builds": b, "discs": d, "chars": ch}


# ---------- Jobs（既存維持） ----------

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
        fields.append("status = ?"); params.append(status)
    if image_path is not None:
        fields.append("image_path = ?"); params.append(image_path)
    if extracted_json is not None:
        fields.append("extracted_json = ?"); params.append(json.dumps(extracted_json, ensure_ascii=False))
    if normalized_json is not None:
        fields.append("normalized_json = ?"); params.append(json.dumps(normalized_json, ensure_ascii=False))
    if error_message is not None:
        fields.append("error_message = ?"); params.append(error_message)
    fields.append("updated_at = ?"); params.append(_now())
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
    rows = await db.fetchall(
        "SELECT * FROM zzz_extraction_jobs "
        "WHERE status IN ('queued', 'capturing', 'extracting') ORDER BY id"
    )
    return [_job_row_to_dict(r) for r in rows]


async def prune_finished_jobs(db, retention: int = 200) -> int:
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
