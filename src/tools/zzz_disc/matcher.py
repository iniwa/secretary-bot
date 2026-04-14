"""ディスクとプリセットのスコアリング。Phase 4 で本実装。

現状は計画書 `score(disc, preset)` の仕様通りに動く最小実装を入れておく。
routes.py からは `score_disc_against_preset` / `top_candidates_for_disc` を呼ぶ。
"""

from __future__ import annotations


def score_disc_against_preset(disc: dict, preset: dict) -> float:
    """計画書のスコア式に従う。

    - 指定セット一致: +3.0
    - メインステータス一致: +3.0
    - サブステータス一致: weight * (1 + upgrades * 0.3)
    """
    s = 0.0
    preferred_sets = preset.get("preferred_set_ids") or []
    if disc.get("set_id") is not None and disc["set_id"] in preferred_sets:
        s += 3.0

    main_stats = preset.get("preferred_main_stats") or []
    if disc.get("main_stat_name") in main_stats:
        s += 3.0

    sub_priority = {p["name"]: p.get("weight", 0)
                    for p in (preset.get("sub_stat_priority") or [])}
    for sub in (disc.get("sub_stats") or []):
        w = sub_priority.get(sub.get("name"), 0)
        s += w * (1 + sub.get("upgrades", 0) * 0.3)
    return s


def top_candidates_for_disc(
    disc: dict,
    presets: list[dict],
    characters_by_id: dict[int, dict],
    *,
    threshold: float = 3.0,
    limit: int = 10,
) -> list[dict]:
    """全プリセットに対してスコアを計算し、ディスクの slot に一致する中で
    閾値以上のものを降順で返す。
    """
    results = []
    disc_slot = disc.get("slot")
    for preset in presets:
        if preset.get("slot") != disc_slot:
            continue
        score = score_disc_against_preset(disc, preset)
        if score < threshold:
            continue
        ch = characters_by_id.get(preset["character_id"])
        if not ch:
            continue
        results.append({
            "character_id": ch["id"],
            "character_slug": ch["slug"],
            "character_name_ja": ch["name_ja"],
            "slot": preset["slot"],
            "score": round(score, 2),
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
