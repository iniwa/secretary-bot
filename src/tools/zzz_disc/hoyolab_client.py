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
    # genshin.py v1.7+: disc.properties (list of ZZZProperty with name/value/type)
    subs = _pick(disc_obj, "properties", "sub_properties", "substats", "sub_stats", default=[]) or []
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
    # genshin.py v1.7+: disc.main_properties (list, 通常 1件)
    mains = _pick(disc_obj, "main_properties", default=None)
    main = None
    if isinstance(mains, list) and mains:
        main = mains[0]
    else:
        main = _pick(disc_obj, "main_property", "main_stat", "mainstat")
    if not main:
        return ("", 0.0)
    name = str(_pick(main, "name", "property_name", default="") or "")
    value = _parse_value(_pick(main, "value", "base", default=0))
    return (name, value)


def _extract_slot(disc_obj) -> int:
    # genshin.py v1.7+: disc.position (int 1..6)
    slot = _pick(disc_obj, "position", "equipment_type", "slot", "equip_slot")
    if isinstance(slot, int):
        return slot
    if isinstance(slot, str) and slot.isdigit():
        return int(slot)
    return 0


def _extract_set_info(disc_obj) -> tuple[str, str | None, str | None, str | None]:
    """(set_name, set_id, two_pc_desc, four_pc_desc) を返す。"""
    se = _pick(disc_obj, "set_effect", "equip_suit", default=None)
    if not se:
        name = str(_pick(disc_obj, "suit_name", "set_name", default="") or "")
        return (name, None, None, None)
    d = _as_dict(se) if not isinstance(se, dict) else se
    return (
        str(d.get("name") or ""),
        str(d.get("id")) if d.get("id") is not None else None,
        d.get("two_piece_description") or d.get("desc2"),
        d.get("four_piece_description") or d.get("desc4"),
    )


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
    cookie 失効時は auto_login_enabled かつ email/password が保存されていれば
    自動ログインで cookies を更新し、一度だけリトライする。
    """
    genshin = await _load_client()
    uid = int(account["uid"])

    async def _run(acc: dict) -> list:
        cookies = {
            "ltuid_v2": acc["ltuid_v2"],
            "ltoken_v2": acc["ltoken_v2"],
        }
        if acc.get("ltmid_v2"):
            cookies["ltmid_v2"] = acc["ltmid_v2"]
        if acc.get("account_mid_v2"):
            cookies["account_mid_v2"] = acc["account_mid_v2"]
        if acc.get("account_id_v2"):
            cookies["account_id_v2"] = acc["account_id_v2"]
        client = genshin.Client(cookies=cookies,
                                game=getattr(genshin.Game, "ZZZ", None))
        return await _fetch_agents(client, uid)

    try:
        agents = await _run(account)
    except Exception as e:
        # cookie 失効 → 自動リフレッシュしてリトライ
        is_auth_err = isinstance(e, getattr(genshin, "InvalidCookies", type(None))) \
            or "invalid" in str(e).lower() and "cookie" in str(e).lower() \
            or "10001" in str(e) or "-100" in str(e)
        if not is_auth_err or not account.get("auto_login_enabled") \
                or not (account.get("email") and account.get("password")):
            raise
        log.warning("HoYoLAB cookie 失効の可能性。自動ログインで再取得を試行")
        from . import hoyolab_auth
        await hoyolab_auth.refresh_account_cookies(db, account)
        account = await models.get_hoyolab_account(db) or account
        agents = await _run(account)

    # agents 取得後は詳細取得のため再利用する client が必要。_run は agents だけ返すので
    # 再構築する。
    cookies = {
        "ltuid_v2": account["ltuid_v2"],
        "ltoken_v2": account["ltoken_v2"],
    }
    if account.get("ltmid_v2"):
        cookies["ltmid_v2"] = account["ltmid_v2"]
    if account.get("account_mid_v2"):
        cookies["account_mid_v2"] = account["account_mid_v2"]
    if account.get("account_id_v2"):
        cookies["account_id_v2"] = account["account_id_v2"]
    client = genshin.Client(cookies=cookies, game=getattr(genshin.Game, "ZZZ", None))
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

    # 詳細取得（discs 付き）— agent 基本情報より詳細の方が icon が整っている
    detail = await _fetch_agent_detail(client, uid, agent_id) or agent

    # agent icon: square_icon を優先、無ければ rectangle_icon / banner_icon
    agent_icon = (
        _pick(detail, "square_icon", default=None)
        or _pick(agent, "square_icon", default=None)
        or _pick(detail, "rectangle_icon", default=None)
        or _pick(agent, "rectangle_icon", default=None)
        or _pick(detail, "banner_icon", default=None)
        or _pick(agent, "banner_icon", default=None)
    )
    agent_element = str(_pick(agent, "element", default="") or
                        _pick(detail, "element", default="") or "") or None
    agent_faction = str(_pick(agent, "faction_name", default="") or
                        _pick(detail, "faction_name", default="") or "") or None

    # character を upsert（slug = hoyolab-{agent_id}）
    slug_candidate = f"hoyolab-{agent_id}"
    existing = await db.fetchone(
        "SELECT id, icon_url, element, faction, hoyolab_agent_id "
        "FROM zzz_characters WHERE hoyolab_agent_id = ? OR name_ja = ?",
        (str(agent_id), name_ja),
    )
    if existing:
        character_id = existing["id"]
        sets, params = [], []
        if not existing.get("hoyolab_agent_id"):
            sets.append("hoyolab_agent_id = ?"); params.append(str(agent_id))
        if agent_icon and not existing.get("icon_url"):
            sets.append("icon_url = ?"); params.append(str(agent_icon))
        if agent_element and not existing.get("element"):
            sets.append("element = ?"); params.append(agent_element)
        if agent_faction and not existing.get("faction"):
            sets.append("faction = ?"); params.append(agent_faction)
        if sets:
            params.append(character_id)
            await db.execute(
                f"UPDATE zzz_characters SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
    else:
        await models.upsert_character(
            db, slug=slug_candidate, name_ja=name_ja,
            element=agent_element, faction=agent_faction,
            icon_url=str(agent_icon) if agent_icon else None,
            hoyolab_agent_id=str(agent_id),
        )
        row = await db.fetchone(
            "SELECT id FROM zzz_characters WHERE slug = ?", (slug_candidate,))
        character_id = row["id"]

    discs_raw = _pick(detail, "discs", "equip", "disc_list", "equipment", default=[]) or []
    disc_ids_by_slot: dict[int, int] = {}
    count = 0
    for d in discs_raw:
        slot = _extract_slot(d)
        if not 1 <= slot <= 6:
            continue
        main_name, main_val = _extract_main_stat(d)
        if not main_name:
            continue
        set_name, hoyo_set_id, two_pc, four_pc = _extract_set_info(d)
        set_id = None
        if set_name:
            set_id = await models.find_or_create_set_by_name(
                db, set_name,
                two_pc_effect=two_pc, four_pc_effect=four_pc,
            )

        disc_id = await models.insert_disc(
            db,
            slot=slot, set_id=set_id,
            main_stat_name=main_name, main_stat_value=main_val,
            sub_stats=_extract_substats(d),
            level=int(_pick(d, "level", default=0) or 0),
            rarity=str(_pick(d, "rarity", default="") or "") or None,
            hoyolab_disc_id=str(_pick(d, "id", "disc_id", default="") or "") or None,
            icon_url=str(_pick(d, "icon", default="") or "") or None,
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
