"""セット名 fuzzy 正規化（rapidfuzz）。

VLM 抽出結果の set_name を、DB 登録済みの zzz_set_masters の slug/name_ja/aliases と
マッチングして最適な set_id を返す。閾値未満なら None を返してユーザーに選択させる。
"""

from __future__ import annotations

from typing import Any

try:
    from rapidfuzz import process, fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False


def _build_candidates(sets: list[dict]) -> list[tuple[str, int]]:
    """name_ja と aliases を全部検索対象に入れ、(候補文字列, set_id) を返す。"""
    cands: list[tuple[str, int]] = []
    for s in sets:
        cands.append((s["name_ja"], s["id"]))
        for alias in (s.get("aliases") or []):
            cands.append((alias, s["id"]))
    return cands


def match_set_name(query: str | None, sets: list[dict],
                   *, threshold: float = 0.85) -> int | None:
    """閾値以上の類似度で最適マッチした set_id を返す。見つからなければ None。"""
    if not query:
        return None
    cands = _build_candidates(sets)
    if not cands:
        return None
    if not _HAS_RAPIDFUZZ:
        # フォールバック: 完全一致のみ
        for text, sid in cands:
            if text == query:
                return sid
        return None

    choices = [c[0] for c in cands]
    result = process.extractOne(query, choices, scorer=fuzz.WRatio)
    if not result:
        return None
    text, score, idx = result
    # rapidfuzz の WRatio は 0..100
    if score / 100.0 < threshold:
        return None
    return cands[idx][1]


def normalize_extraction(raw: dict[str, Any], sets: list[dict]) -> dict[str, Any]:
    """VLM 抽出 dict を正規化し、セット名を set_id に置換。

    入力: {slot, set_name, main_stat:{name,value}, sub_stats:[{name,value,upgrades}]}
    出力: 入力に加えて `set_id`（None 可）を付与。
    """
    result = dict(raw)
    set_id = match_set_name(raw.get("set_name"), sets)
    result["set_id"] = set_id
    return result
