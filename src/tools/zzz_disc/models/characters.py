"""キャラ / セットマスタの upsert・参照。"""

import hashlib
import json

from ._base import _now

__all__ = [
    "upsert_character",
    "upsert_set_master",
    "find_or_create_set_by_name",
    "_decode_char_row",
    "list_characters",
    "update_character_recommended_substats",
    "update_character_recommended_disc_sets",
    "update_character_skills",
    "update_character_recommended_notes",
    "update_character_recommended_team_notes",
    "update_character_recommended_main_stats",
    "list_characters_with_build_stats",
    "get_character",
    "get_character_by_slug",
    "list_set_masters",
]


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
    if not existing and hoyolab_agent_id:
        existing = await db.fetchone(
            "SELECT id, icon_url, element, faction, hoyolab_agent_id "
            "FROM zzz_characters WHERE hoyolab_agent_id = ?", (hoyolab_agent_id,))
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
    raw_subs = row.pop("recommended_substats_json", None)
    try:
        row["recommended_substats"] = json.loads(raw_subs) if raw_subs else []
    except Exception:
        row["recommended_substats"] = []
    raw_sets = row.pop("recommended_disc_sets_json", None)
    try:
        row["recommended_disc_sets"] = json.loads(raw_sets) if raw_sets else []
    except Exception:
        row["recommended_disc_sets"] = []
    raw_skills = row.pop("skills_json", None)
    try:
        row["skills"] = json.loads(raw_skills) if raw_skills else []
    except Exception:
        row["skills"] = []
    raw_main = row.pop("recommended_main_stats_json", None)
    try:
        parsed = json.loads(raw_main) if raw_main else {}
        row["recommended_main_stats"] = parsed if isinstance(parsed, dict) else {}
    except Exception:
        row["recommended_main_stats"] = {}
    return row


_CHAR_COLS = (
    "id, slug, name_ja, element, faction, icon_url, display_order, "
    "hoyolab_agent_id, recommended_substats_json, recommended_disc_sets_json, "
    "skills_json, skill_summary, recommended_notes, recommended_team_notes, "
    "recommended_main_stats_json"
)


async def list_characters(db) -> list[dict]:
    rows = await db.fetchall(
        f"SELECT {_CHAR_COLS} FROM zzz_characters ORDER BY display_order, id"
    )
    return [_decode_char_row(r) for r in rows]


async def update_character_recommended_substats(db, character_id: int,
                                                stats: list[str]) -> int:
    payload = json.dumps(list(stats), ensure_ascii=False)
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET recommended_substats_json = ? WHERE id = ?",
        (payload, character_id),
    )


async def update_character_recommended_disc_sets(db, character_id: int,
                                                 sets: list[str]) -> int:
    payload = json.dumps(list(sets), ensure_ascii=False)
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET recommended_disc_sets_json = ? WHERE id = ?",
        (payload, character_id),
    )


async def update_character_skills(db, character_id: int,
                                  skills: list[dict], summary: str | None) -> int:
    payload = json.dumps(list(skills or []), ensure_ascii=False)
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET skills_json = ?, skill_summary = ? WHERE id = ?",
        (payload, summary, character_id),
    )


async def update_character_recommended_notes(db, character_id: int,
                                             notes: str | None) -> int:
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET recommended_notes = ? WHERE id = ?",
        (notes, character_id),
    )


async def update_character_recommended_team_notes(db, character_id: int,
                                                  notes: str | None) -> int:
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET recommended_team_notes = ? WHERE id = ?",
        (notes, character_id),
    )


async def update_character_recommended_main_stats(db, character_id: int,
                                                  main_stats: dict) -> int:
    """slot (5/6/7) → list[str] の形式で保存。"""
    clean: dict[str, list[str]] = {}
    for k, v in (main_stats or {}).items():
        key = str(k)
        if key not in ("5", "6", "7"):
            continue
        if not isinstance(v, list):
            continue
        clean[key] = [s for s in v if isinstance(s, str) and s]
    payload = json.dumps(clean, ensure_ascii=False)
    return await db.execute_returning_rowcount(
        "UPDATE zzz_characters SET recommended_main_stats_json = ? WHERE id = ?",
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
        f"SELECT {_CHAR_COLS} FROM zzz_characters WHERE id = ?", (character_id,),
    )
    return _decode_char_row(dict(row)) if row else None


async def get_character_by_slug(db, slug: str) -> dict | None:
    row = await db.fetchone(
        f"SELECT {_CHAR_COLS} FROM zzz_characters WHERE slug = ?", (slug,),
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
