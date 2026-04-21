"""docs/zzz_character_codex.md から各キャラの情報を抽出するヘルパ。

- `extract_teams(name_ja)`: `#### 編成例` セクションを丸ごと返す（recommended_team_notes 用）
- `extract_main_stats(name_ja)`: `- **メイン**: 4番 … / 5番 … / 6番 …` 行をパースして
  `{"4": [stat, ...], "5": [...], "6": [...]}` 形式で返す（recommended_main_stats 用）
"""

from __future__ import annotations

import os
import re

_BASE_DIR = os.environ.get("BOT_BASE_DIR", "/app")
_CODEX_PATH = os.path.join(_BASE_DIR, "docs", "zzz_character_codex.md")


def _char_section(name_ja: str) -> str | None:
    """`### <name_ja>(…)?` 見出し配下から次のレベル 3 見出し or 水平線 までのブロックを返す。"""
    if not name_ja or not os.path.exists(_CODEX_PATH):
        return None
    with open(_CODEX_PATH, encoding="utf-8") as f:
        text = f.read()
    head_re = re.compile(
        rf"^###\s+{re.escape(name_ja)}(?:[（(]|\s|$)",
        re.MULTILINE,
    )
    m = head_re.search(text)
    if not m:
        return None
    start = m.end()
    # 次のレベル 3 見出し
    nxt = re.search(r"^###\s+", text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    # 水平線も区切り候補
    hr = re.search(r"^---\s*$", text[start:end], re.MULTILINE)
    if hr:
        end = start + hr.start()
    return text[start:end]


def extract_teams(name_ja: str) -> str | None:
    """`#### 編成例` / `#### チーム例` セクション本文を返す。見つからなければ None。"""
    section = _char_section(name_ja)
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


# メインステ名の正規化: コーデックス表記 → UI 正規名
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


def _parse_slot_stats(text: str) -> list[str]:
    """slot 区間のテキストから候補 stat 名のリストを返す（出現順）。"""
    masked = text
    seen: list[str] = []
    # 最長優先で回す（ATK/攻撃力 両方拾うが正規化で同じ名に畳まれる）
    for pat, name in _STAT_PATTERNS:
        while True:
            m = pat.search(masked)
            if not m:
                break
            if name not in seen:
                seen.append(name)
            masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end() :]
    return seen


def extract_main_stats(name_ja: str) -> dict[str, list[str]] | None:
    """キャラの「- **メイン**: 4 番 … / 5 番 … / 6 番 …」行をパース。

    見つからない／全スロット空 なら None。
    """
    section = _char_section(name_ja)
    if section is None:
        return None
    m = re.search(r"\*\*メイン\*\*\s*[:：]\s*([^\n]+)", section)
    if not m:
        return None
    line = m.group(1).strip()
    # 各スロットの本文を切り出す: `4\s?番 ... (次の 5/6 番の直前まで)`
    result: dict[str, list[str]] = {"4": [], "5": [], "6": []}
    # 位置ベースで slot を取り出す
    slot_matches = list(re.finditer(r"([456])\s?番\s*", line))
    for i, sm in enumerate(slot_matches):
        slot = sm.group(1)
        start = sm.end()
        end = slot_matches[i + 1].start() if i + 1 < len(slot_matches) else len(line)
        segment = line[start:end]
        # 末尾の ` / ` 区切りを削除
        segment = segment.strip(" /")
        stats = _parse_slot_stats(segment)
        if stats:
            result[slot] = stats
    if not any(result.values()):
        return None
    return result
