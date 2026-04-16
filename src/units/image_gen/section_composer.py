"""セクション合成: 複数の prompt_sections を 1 本の positive/negative に畳む。

設計方針:
- 入力は順序付き（UI の drag 並び替えに追従）
- 重複タグは先勝ち（= UI 上で上にある方の weight が採用される）
- weight 記法 `(tag:1.2)` は 1 つの tag として扱い、名前部分で重複判定
- user_positive / user_negative は `user_position` に従って挿入:
    head    : セクションの前
    tail    : セクションの後（既定）
    section:<category_key> : 指定カテゴリのセクション群の直前
- positive / negative は完全に独立したパイプライン（ネガティブがポジに混ざらない）

合成ロジックはクライアント（compose.js）側でもプレビュー用に再実装されるが、
真のソースは常にサーバ（この関数）。ジョブ投入時はサーバ側で再合成する。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# `(tag:weight)` 記法: `tag` と `weight` を抽出、weight は任意
_WEIGHTED_RE = re.compile(r"^\((?P<tag>.+?)\s*(?::\s*(?P<w>[-+]?\d*\.?\d+))?\)$")


@dataclass
class ComposedTag:
    raw: str              # 最終出力に使う文字列（"(tag:1.2)" など）
    key: str              # 重複判定用の正規化キー（lowercase, weight 除去, 余白畳み）
    weight: float | None  # weight が書かれていなければ None
    source_section_id: int | None = None
    source_category: str | None = None


@dataclass
class ComposeResult:
    positive: str = ""
    negative: str = ""
    positive_tags: list[ComposedTag] = field(default_factory=list)
    negative_tags: list[ComposedTag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # UI に出す注意書き
    dropped: list[str] = field(default_factory=list)   # 重複で落ちたタグの key


def _normalize_key(tag: str) -> str:
    """重複判定用の正規化キー。

    `(tag:1.2)`, `(tag)`, `tag` のすべてが同じキーを返す。
    """
    t = tag.strip()
    m = _WEIGHTED_RE.match(t)
    if m:
        t = m.group("tag").strip()
    # 内部連続空白を 1 つに畳む、大文字小文字無視
    t = re.sub(r"\s+", " ", t)
    return t.lower()


def _parse_weight(tag: str) -> float | None:
    m = _WEIGHTED_RE.match(tag.strip())
    if not m:
        return None
    w = m.group("w")
    return float(w) if w is not None else None


def _split_tags(text: str | None) -> list[str]:
    """`a, b, (c:1.2), d` を `["a", "b", "(c:1.2)", "d"]` に。
    空文字・連続カンマは無視。
    """
    if not text:
        return []
    # 単純にカンマ区切り。括弧内にカンマが出るケースは ComfyUI では実質無いのでこれで十分。
    parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]


def _append_tags(
    accumulator: list[ComposedTag],
    seen_keys: dict[str, ComposedTag],
    text: str | None,
    *,
    section_id: int | None,
    category: str | None,
    warnings: list[str],
    dropped: list[str],
) -> None:
    for raw in _split_tags(text):
        key = _normalize_key(raw)
        if not key:
            continue
        w = _parse_weight(raw)
        if key in seen_keys:
            prev = seen_keys[key]
            # 先勝ち。weight が異なっていたら warning を立てる
            if w is not None and prev.weight is not None and abs((prev.weight or 1.0) - (w or 1.0)) > 1e-6:
                warnings.append(
                    f"tag `{key}` の weight が衝突 (採用={prev.weight}, 後発={w})"
                )
            elif w is not None and prev.weight is None:
                warnings.append(
                    f"tag `{key}` は先行 section で weight 無し、後発 {w} は採用されず"
                )
            dropped.append(key)
            continue
        tag = ComposedTag(
            raw=raw, key=key, weight=w,
            source_section_id=section_id, source_category=category,
        )
        accumulator.append(tag)
        seen_keys[key] = tag


@dataclass
class SectionInput:
    """compose_prompt への 1 入力。DB row (dict) を薄く包む。"""
    id: int | None
    category_key: str | None
    positive: str | None
    negative: str | None

    @classmethod
    def from_row(cls, row: dict) -> "SectionInput":
        return cls(
            id=row.get("id"),
            category_key=row.get("category_key"),
            positive=row.get("positive"),
            negative=row.get("negative"),
        )


def compose_prompt(
    sections: Iterable[SectionInput | dict],
    user_positive: str | None = None,
    user_negative: str | None = None,
    user_position: str = "tail",
) -> ComposeResult:
    """セクション列＋ユーザー入力から最終 positive/negative を合成する。

    Args:
        sections: 合成順に並んだセクション群（dict も受け取れる）
        user_positive / user_negative: ユーザーが直接タイプした追記
        user_position: 'head' | 'tail' | 'section:<category_key>'
    """
    norm_sections: list[SectionInput] = []
    for s in sections:
        if isinstance(s, dict):
            norm_sections.append(SectionInput.from_row(s))
        else:
            norm_sections.append(s)

    result = ComposeResult()
    seen_pos: dict[str, ComposedTag] = {}
    seen_neg: dict[str, ComposedTag] = {}

    def _emit_user_pos() -> None:
        if user_positive:
            _append_tags(
                result.positive_tags, seen_pos, user_positive,
                section_id=None, category="__user__",
                warnings=result.warnings, dropped=result.dropped,
            )

    def _emit_user_neg() -> None:
        if user_negative:
            _append_tags(
                result.negative_tags, seen_neg, user_negative,
                section_id=None, category="__user__",
                warnings=result.warnings, dropped=result.dropped,
            )

    # user_position 解釈
    pos_mode = "tail"
    pos_target: str | None = None
    if user_position == "head":
        pos_mode = "head"
    elif user_position == "tail":
        pos_mode = "tail"
    elif isinstance(user_position, str) and user_position.startswith("section:"):
        pos_mode = "section"
        pos_target = user_position.split(":", 1)[1] or None
    else:
        pos_mode = "tail"

    if pos_mode == "head":
        _emit_user_pos()
        _emit_user_neg()

    section_target_emitted = False
    for sec in norm_sections:
        if (
            pos_mode == "section"
            and not section_target_emitted
            and sec.category_key == pos_target
        ):
            _emit_user_pos()
            _emit_user_neg()
            section_target_emitted = True
        _append_tags(
            result.positive_tags, seen_pos, sec.positive,
            section_id=sec.id, category=sec.category_key,
            warnings=result.warnings, dropped=result.dropped,
        )
        _append_tags(
            result.negative_tags, seen_neg, sec.negative,
            section_id=sec.id, category=sec.category_key,
            warnings=result.warnings, dropped=result.dropped,
        )

    if pos_mode == "section" and not section_target_emitted:
        # 指定カテゴリのセクションが 1 件も無かった → tail 扱い
        _emit_user_pos()
        _emit_user_neg()
    elif pos_mode == "tail":
        _emit_user_pos()
        _emit_user_neg()

    result.positive = ", ".join(t.raw for t in result.positive_tags)
    result.negative = ", ".join(t.raw for t in result.negative_tags)
    return result
