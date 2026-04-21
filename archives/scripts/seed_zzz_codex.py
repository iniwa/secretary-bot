"""実機 bot.db に codex 由来の recommended_main_stats / recommended_team_notes を投入する。

- 既に値が入っているフィールドは上書きしない。
- recommended_team_notes は、codex の編成例本文が skill_summary / recommended_notes と
  大きく重複している場合は投入をスキップする (重複検出は 8-gram 共有率ベース)。
- --dry-run で書き込み前に差分を確認できる。

Usage (in secretary-bot container):
    python /tmp/seed_zzz_codex.py --dry-run
    python /tmp/seed_zzz_codex.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys

DB_PATH = os.environ.get("BOT_DB_PATH", "/app/data/bot.db")
CODEX_PATH = os.environ.get(
    "CODEX_PATH", "/app/repo/docs/zzz_character_codex.md"
)


# ---------- codex 抽出（src/tools/zzz_disc/codex.py の移植） ----------

_STAT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ATK\s*%"), "攻撃力%"),
    (re.compile(r"攻撃力\s*%"), "攻撃力%"),
    (re.compile(r"HP\s*%"), "HP%"),
    (re.compile(r"DEF\s*%"), "防御力%"),
    (re.compile(r"防御力\s*%"), "防御力%"),
    (re.compile(r"EN\s*自動回復|EN\s*回復|エネルギー自動回復"), "エネルギー自動回復%"),
    (re.compile(r"異常マスタリー"), "異常マスタリー"),
    (re.compile(r"異常掌握"), "異常掌握"),
    (re.compile(r"衝撃力\s*%?"), "衝撃力%"),
    (re.compile(r"貫通率"), "貫通率%"),
    (re.compile(r"会心ダメ(?:ージ)?\s*%?"), "会心ダメージ%"),
    (re.compile(r"会心率\s*%?"), "会心率%"),
    (re.compile(r"物理属性ダメ(?:ージ)?\s*%?"), "物理属性ダメージ%"),
    (re.compile(r"炎属性ダメ(?:ージ)?\s*%?"), "炎属性ダメージ%"),
    (re.compile(r"氷属性ダメ(?:ージ)?\s*%?"), "氷属性ダメージ%"),
    (re.compile(r"電気属性ダメ(?:ージ)?\s*%?"), "電気属性ダメージ%"),
    (re.compile(r"エーテル属性ダメ(?:ージ)?\s*%?"), "エーテル属性ダメージ%"),
]


def _char_section(codex_text: str, name_ja: str) -> str | None:
    head_re = re.compile(
        rf"^###\s+{re.escape(name_ja)}(?:[（(]|\s|$)",
        re.MULTILINE,
    )
    m = head_re.search(codex_text)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^###\s+", codex_text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(codex_text)
    hr = re.search(r"^---\s*$", codex_text[start:end], re.MULTILINE)
    if hr:
        end = start + hr.start()
    return codex_text[start:end]


def extract_teams(codex_text: str, name_ja: str) -> str | None:
    section = _char_section(codex_text, name_ja)
    if section is None:
        return None
    lines = section.splitlines(keepends=True)
    teams_start: int | None = None
    teams_end: int | None = None
    for i, line in enumerate(lines):
        if re.match(r"^####\s+(?:編成例|チーム例)\s*$", line):
            teams_start = i + 1
            continue
        if teams_start is not None and teams_end is None and re.match(r"^####\s+", line):
            teams_end = i
            break
    if teams_start is None:
        return None
    if teams_end is None:
        teams_end = len(lines)
    body = "".join(lines[teams_start:teams_end]).strip("\n")
    return body or None


def _parse_slot_stats(text: str) -> list[str]:
    masked = text
    seen: list[str] = []
    for pat, name in _STAT_PATTERNS:
        while True:
            m = pat.search(masked)
            if not m:
                break
            if name not in seen:
                seen.append(name)
            masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    return seen


def extract_main_stats(codex_text: str, name_ja: str) -> dict[str, list[str]] | None:
    section = _char_section(codex_text, name_ja)
    if section is None:
        return None
    m = re.search(r"\*\*メイン\*\*\s*[:：]\s*([^\n]+)", section)
    if not m:
        return None
    line = m.group(1).strip()
    result: dict[str, list[str]] = {"4": [], "5": [], "6": []}
    slot_matches = list(re.finditer(r"([456])\s?番\s*", line))
    for i, sm in enumerate(slot_matches):
        slot = sm.group(1)
        start = sm.end()
        end = slot_matches[i + 1].start() if i + 1 < len(slot_matches) else len(line)
        segment = line[start:end].strip(" /")
        stats = _parse_slot_stats(segment)
        if stats:
            result[slot] = stats
    if not any(result.values()):
        return None
    return result


# ---------- 重複検出 ----------

def _ngrams(s: str, n: int = 8) -> set[str]:
    s = re.sub(r"\s+", "", s)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def overlap_ratio(a: str, b: str, n: int = 8) -> float:
    """a のうち b にも現れる n-gram の割合 (0..1)。"""
    if not a or not b:
        return 0.0
    ga = _ngrams(a, n)
    gb = _ngrams(b, n)
    if not ga:
        return 0.0
    return len(ga & gb) / len(ga)


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実書き込み (省略時は dry-run)")
    ap.add_argument("--dup-threshold", type=float, default=0.5,
                    help="team_notes の重複判定閾値 (codex→既存 n-gram 被覆率)")
    args = ap.parse_args()

    if not os.path.exists(CODEX_PATH):
        print(f"codex not found: {CODEX_PATH}", file=sys.stderr)
        return 2
    if not os.path.exists(DB_PATH):
        print(f"db not found: {DB_PATH}", file=sys.stderr)
        return 2

    with open(CODEX_PATH, encoding="utf-8") as f:
        codex_text = f.read()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name_ja, recommended_main_stats_json, recommended_team_notes, "
        "recommended_notes, skill_summary "
        "FROM zzz_characters ORDER BY display_order, id"
    )
    rows = [dict(r) for r in cur.fetchall()]

    plan_ms: list[tuple[int, str, dict]] = []
    plan_tn: list[tuple[int, str, str]] = []
    skip_tn_dup: list[tuple[int, str, float]] = []
    miss: list[str] = []

    for r in rows:
        cid = r["id"]
        name = (r["name_ja"] or "").strip()
        if not name:
            continue

        # main stats
        try:
            cur_ms = json.loads(r["recommended_main_stats_json"] or "{}")
        except Exception:
            cur_ms = {}
        if not any(cur_ms.values()):
            ms = extract_main_stats(codex_text, name)
            if ms:
                plan_ms.append((cid, name, ms))

        # team notes
        if not r["recommended_team_notes"]:
            teams = extract_teams(codex_text, name)
            if teams:
                existing_blob = "\n".join([
                    r.get("recommended_notes") or "",
                    r.get("skill_summary") or "",
                ])
                ratio = overlap_ratio(teams, existing_blob)
                if ratio >= args.dup_threshold:
                    skip_tn_dup.append((cid, name, ratio))
                else:
                    plan_tn.append((cid, name, teams))
            else:
                if not extract_main_stats(codex_text, name):
                    miss.append(name)

    print("=== plan: recommended_main_stats ({}) ===".format(len(plan_ms)))
    for cid, name, ms in plan_ms:
        print(f"  [{cid:3d}] {name}: {ms}")
    print()
    print("=== plan: recommended_team_notes ({}) ===".format(len(plan_tn)))
    for cid, name, body in plan_tn:
        first = body.splitlines()[0] if body else ""
        print(f"  [{cid:3d}] {name}: ({len(body)} chars) {first[:60]}")
    print()
    if skip_tn_dup:
        print("=== skip: team_notes 重複 ({}) ===".format(len(skip_tn_dup)))
        for cid, name, ratio in skip_tn_dup:
            print(f"  [{cid:3d}] {name}: overlap={ratio:.2f}")
        print()
    if miss:
        print("=== codex 未掲載 ({}) ===".format(len(miss)))
        for name in miss:
            print(f"  {name}")
        print()

    if not args.apply:
        print("[dry-run] --apply で実書き込み")
        return 0

    for cid, _name, ms in plan_ms:
        payload = json.dumps(ms, ensure_ascii=False)
        cur.execute(
            "UPDATE zzz_characters SET recommended_main_stats_json = ? WHERE id = ?",
            (payload, cid),
        )
    for cid, _name, body in plan_tn:
        cur.execute(
            "UPDATE zzz_characters SET recommended_team_notes = ? WHERE id = ?",
            (body, cid),
        )
    conn.commit()
    print(f"[applied] main_stats={len(plan_ms)} team_notes={len(plan_tn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
