"""HoYoLAB Battle Chronicle からの ZZZ キャラ・ディスク取得。

genshin.py ライブラリ経由で `ltuid_v2` / `ltoken_v2` Cookie を使ってアクセスする。
エージェントごとの「現在の装備」を取得し、`zzz_discs` に upsert、`zzz_builds(is_current=1)`
と `zzz_build_slots` を更新する。

※ genshin.py は 1.7 系以降で ZZZ 対応。API 形状はバージョン依存なので
`getattr` で可能な属性を拾う緩い実装にしてある。
"""

from __future__ import annotations

from src.logger import get_logger
from . import models

log = get_logger(__name__)


async def _load_client():
    try:
        import genshin  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "genshin.py is not installed. Add 'genshin>=1.7,<2.0' to requirements.txt"
        ) from e
    return genshin


def _pick(obj, *names, default=None):
    """属性もしくは dict キーのどれかから値を取り出す緩いヘルパ。"""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _as_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    return {k: getattr(obj, k) for k in dir(obj)
            if not k.startswith("_") and not callable(getattr(obj, k, None))}


def _extract_substats(disc_obj) -> list[dict]:
    subs = _pick(disc_obj, "sub_properties", "substats", "sub_stats", default=[]) or []
    result = []
    for s in subs:
        result.append({
            "name": str(_pick(s, "name", "property_name", default="") or ""),
            "value": _parse_value(_pick(s, "value", "base", default=0)),
            "upgrades": int(_pick(s, "times", "upgrade_times", "upgrades", default=0) or 0),
        })
    return result


def _parse_value(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace("%", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0


def _extract_main_stat(disc_obj) -> tuple[str, float]:
    main = _pick(disc_obj, "main_property", "main_stat", "mainstat")
    if not main:
        return ("", 0.0)
    name = str(_pick(main, "name", "property_name", default="") or "")
    value = _parse_value(_pick(main, "value", "base", default=0))
    return (name, value)


def _extract_slot(disc_obj) -> int:
    slot = _pick(disc_obj, "equipment_type", "slot", "position", "equip_slot")
    if isinstance(slot, int):
        return slot
    if isinstance(slot, str) and slot.isdigit():
        return int(slot)
    return 0


def _extract_agent_stats(agent_obj) -> dict:
    """エージェントの基本ステータス（HP/ATK/DEF/Crit等）を dict にまとめる。"""
    props = _pick(agent_obj, "properties", "stats", default=[]) or []
    stats: dict = {}
    for p in props:
        name = str(_pick(p, "name", "property_name", default="") or "")
        value = _pick(p, "final", "value", "base", default=None)
        if name and value is not None:
            stats[name] = value
    level = _pick(agent_obj, "level", default=None)
    if level is not None:
        stats["_level"] = level
    return stats


async def sync_current_builds(db, account: dict,
                              *, filter_hoyolab_id: str | None = None) -> dict:
    """HoYoLAB から取得 → zzz_discs / zzz_builds / zzz_build_slots に反映。

    filter_hoyolab_id が指定されていれば、その agent_id を持つ1キャラのみ同期。
    """
    genshin = await _load_client()
    cookies = {
        "ltuid_v2": account["ltuid_v2"],
        "ltoken_v2": account["ltoken_v2"],
    }
    client = genshin.Client(cookies=cookies, game=getattr(genshin.Game, "ZZZ", None))
    uid = int(account["uid"])

    agents = await _fetch_agents(client, uid)
    if filter_hoyolab_id:
        agents = [
            a for a in agents
            if str(_pick(a, "id", "agent_id", "character_id") or "") == str(filter_hoyolab_id)
        ]

    synced_chars = 0
    synced_discs = 0
    errors: list[str] = []
    results: list[dict] = []

    for agent in agents:
        agent_name = _pick(agent, "full_name", "name", default="?") or "?"
        try:
            n = await _sync_one_agent(db, client, uid, agent)
            synced_chars += 1
            synced_discs += n
            results.append({
                "name_ja": agent_name,
                "slug": f"hoyolab-{_pick(agent, 'id', 'agent_id', default='')}",
                "ok": True,
                "disc_count": n,
            })
        except Exception as e:
            log.exception("sync agent failed")
            errors.append(f"{agent_name}: {e}")
            results.append({
                "name_ja": agent_name, "ok": False, "error": str(e),
            })

    await models.update_hoyolab_synced(db, account["uid"])
    return {
        "synced_characters": synced_chars,
        "synced_discs": synced_discs,
        "errors": errors,
        "results": results,
    }


async def _fetch_agents(client, uid: int) -> list:
    """agent 一覧を取得（API バージョンに応じて複数メソッドを試す）。"""
    for method in ("get_zzz_agents", "get_zzz_characters", "get_zenless_agents"):
        fn = getattr(client, method, None)
        if fn is None:
            continue
        try:
            result = await fn(uid)
            if result:
                return list(result)
        except Exception as e:
            log.debug("agent fetch via %s failed: %s", method, e)
    raise RuntimeError("no usable method found on genshin.Client for ZZZ agents")


async def _fetch_agent_detail(client, uid: int, agent_id):
    """1 エージェントの装備詳細を取る（discs 込み）。"""
    for method in ("get_zzz_agent_info", "get_zzz_character_info", "get_zzz_agent"):
        fn = getattr(client, method, None)
        if fn is None:
            continue
        try:
            return await fn(agent_id, uid=uid)
        except TypeError:
            try:
                return await fn(agent_id)
            except Exception as e:
                log.debug("agent detail via %s failed: %s", method, e)
        except Exception as e:
            log.debug("agent detail via %s failed: %s", method, e)
    return None


async def _sync_one_agent(db, client, uid: int, agent) -> int:
    agent_id = _pick(agent, "id", "agent_id", "character_id")
    name_ja = str(_pick(agent, "full_name", "name", default="") or "")
    if not agent_id or not name_ja:
        raise RuntimeError("agent id/name missing")

    # character を upsert（slug = hoyolab-{agent_id}）
    slug_candidate = f"hoyolab-{agent_id}"
    existing = await db.fetchone(
        "SELECT id FROM zzz_characters WHERE hoyolab_agent_id = ? OR name_ja = ?",
        (str(agent_id), name_ja),
    )
    if existing:
        character_id = existing["id"]
        await db.execute(
            "UPDATE zzz_characters SET hoyolab_agent_id = ? WHERE id = ? AND "
            "(hoyolab_agent_id IS NULL OR hoyolab_agent_id = '')",
            (str(agent_id), character_id),
        )
    else:
        await models.upsert_character(
            db, slug=slug_candidate, name_ja=name_ja,
            hoyolab_agent_id=str(agent_id),
        )
        row = await db.fetchone(
            "SELECT id FROM zzz_characters WHERE slug = ?", (slug_candidate,))
        character_id = row["id"]

    # 詳細取得（discs 付き）
    detail = await _fetch_agent_detail(client, uid, agent_id) or agent
    discs_raw = _pick(detail, "equip", "discs", "disc_list", "equipment", default=[]) or []
    # discs_raw から「ディスク（equipment_type 1..6）」だけを抽出（Wエンジン等を除外）
    disc_ids_by_slot: dict[int, int] = {}
    count = 0
    for d in discs_raw:
        slot = _extract_slot(d)
        if not 1 <= slot <= 6:
            continue
        main_name, main_val = _extract_main_stat(d)
        if not main_name:
            continue
        set_name = str(_pick(d, "equip_suit", "suit_name", "set_name", default="") or "")
        if isinstance(_pick(d, "equip_suit"), dict):
            set_name = _pick(d, "equip_suit").get("name") or set_name
        set_id = None
        if set_name:
            set_id = await models.find_or_create_set_by_name(db, set_name)

        disc_id = await models.insert_disc(
            db,
            slot=slot, set_id=set_id,
            main_stat_name=main_name, main_stat_value=main_val,
            sub_stats=_extract_substats(d),
            level=int(_pick(d, "level", default=0) or 0),
            rarity=str(_pick(d, "rarity", default="") or "") or None,
            hoyolab_disc_id=str(_pick(d, "id", "disc_id", default="") or "") or None,
        )
        disc_ids_by_slot[slot] = disc_id
        count += 1

    # current ビルドを更新
    stats = _extract_agent_stats(detail)
    build_id = await models.upsert_current_build(
        db, character_id=character_id,
        name="現在の装備", stats=stats,
    )
    await models.clear_build_slots(db, build_id)
    for slot, disc_id in disc_ids_by_slot.items():
        await models.set_build_slot(db, build_id, slot, disc_id)

    return count
