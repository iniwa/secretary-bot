"""Wildcard / Dynamic Prompts の展開エンジン。

クライアント（static/js/lib/wildcard.js）と同じロジックを保つ。
真のソースは常にここ（サーバ）で、ジョブ投入前の検証・プレビュー API・
将来 Discord 等のクライアント非経由パスからでも同じ挙動になるようにする。

## 記法

- `{a|b|c}`               — 均等ランダム
- `{2::a|1::b}`           — 重み付きランダム（重みは非負の数値）
- `{1-5}` / `{5-1}`       — 整数ランダム（inclusive・両端どちら先でも可）
- `__name__`              — wildcard_files(name) の内容から 1 行ランダム選択
                             行頭 `#` と空行はコメント扱いで無視
- エスケープ: `\{` `\|` `\}` `\:` `\\` `\_` 等、任意の 1 文字をリテラル化

## 方針

- 入れ子は非対応。`{a|{b|c}}` は最初の `}` で閉じ、残りはリテラル扱い
- 置換結果の中にさらに `{...}` / `__foo__` が現れても再展開しない
- 未定義の `__foo__` は `__foo__` をそのまま残し warnings に記録
- 決定的展開は rng_seed で制御（同じ seed + 同じ入力 + 同じ files → 同じ結果）
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Mapping


# `__name__`（name は英数 / `_` `.` `-`）
_FILE_RE = re.compile(r"__([A-Za-z0-9_.\-]+)__")

# `w::body`（body は `.*`）
_WEIGHTED_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*::\s*(.*)$")

# `1-5` のような整数レンジ
_RANGE_RE = re.compile(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$")


@dataclass
class Choice:
    token: str              # 原文トークン（"{a|b|c}" や "__hair__"）
    kind: str               # 'alt' | 'range' | 'file'
    picked: str             # 採用された文字列
    source: str | None = None  # file の場合 "file:<name>"


@dataclass
class ExpandResult:
    text: str
    choices: list[Choice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _unescape(s: str) -> str:
    """`\\X` → `X` に変換。末尾の単独 `\\` はそのまま残す。"""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _split_top_level_pipe(inner: str) -> list[str]:
    """`|` でトップレベル分割（`\\|` はリテラル扱い）。
    入れ子非対応のため、中括弧内の `|` も単純に分割する。
    """
    parts: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "\\" and i + 1 < len(inner):
            cur.append(c)
            cur.append(inner[i + 1])
            i += 2
            continue
        if c == "|":
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def _find_matching_brace(text: str, open_idx: int) -> int | None:
    """`text[open_idx] == '{'` から未エスケープの `}` を探し、その位置を返す。
    入れ子非対応なので最初に現れた `}` で閉じる。見つからなければ None。
    """
    i = open_idx + 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            i += 2
            continue
        if c == "}":
            return i
        i += 1
    return None


def _pick_weighted(rng: random.Random, raw_alts: list[str]) -> str:
    """重み付き選択。各 alt は `w::body` を受け付け、重み未指定は 1.0。
    戻り値は unescape 済みの body。
    """
    weights: list[float] = []
    bodies: list[str] = []
    for a in raw_alts:
        stripped = a.strip()
        m = _WEIGHTED_RE.match(stripped)
        if m:
            try:
                w = float(m.group(1))
            except ValueError:
                w = 1.0
            weights.append(max(0.0, w))
            bodies.append(_unescape(m.group(2)))
        else:
            weights.append(1.0)
            bodies.append(_unescape(stripped))

    total = sum(weights)
    if total <= 0.0:
        return bodies[rng.randrange(len(bodies))]
    r = rng.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r < acc:
            return bodies[i]
    return bodies[-1]


def _expand_brace(
    content: str, rng: random.Random,
) -> tuple[str, Choice | None, list[str]]:
    warnings: list[str] = []
    token = "{" + content + "}"
    m = _RANGE_RE.match(content)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        picked = str(rng.randint(lo, hi))
        return (picked, Choice(token=token, kind="range", picked=picked), warnings)
    alts = _split_top_level_pipe(content)
    # 空 or 全空の場合は何も出さず choice も記録しない
    if not alts or all(a.strip() == "" for a in alts):
        return ("", None, warnings)
    picked = _pick_weighted(rng, alts)
    return (picked, Choice(token=token, kind="alt", picked=picked), warnings)


def _pick_file_line(rng: random.Random, content: str) -> str | None:
    lines: list[str] = []
    for raw in (content or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    if not lines:
        return None
    return rng.choice(lines)


def expand(
    template: str,
    *,
    files: Mapping[str, str] | None = None,
    rng_seed: int | None = None,
) -> ExpandResult:
    """wildcard テンプレートを 1 回展開する。

    Args:
        template: 入力プロンプト文字列
        files: 参照ファイル辞書（name → 本体文字列）
        rng_seed: 指定すれば決定的な展開になる
    """
    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()
    files = files or {}
    choices: list[Choice] = []
    warnings: list[str] = []
    out: list[str] = []

    i = 0
    n = len(template)
    while i < n:
        c = template[i]

        # Escape: `\X` → literal X
        if c == "\\" and i + 1 < n:
            out.append(template[i + 1])
            i += 2
            continue

        # Brace token: `{...}`
        if c == "{":
            close = _find_matching_brace(template, i)
            if close is not None:
                inner = template[i + 1 : close]
                picked, choice, warn = _expand_brace(inner, rng)
                out.append(picked)
                if choice is not None:
                    choices.append(choice)
                warnings.extend(warn)
                i = close + 1
                continue
            # 閉じられない `{` はそのままリテラル

        # File ref: `__name__`
        if c == "_" and i + 1 < n and template[i + 1] == "_":
            m = _FILE_RE.match(template, i)
            if m:
                name = m.group(1)
                if name in files:
                    picked = _pick_file_line(rng, files[name])
                    if picked is not None:
                        out.append(picked)
                        choices.append(Choice(
                            token=m.group(0), kind="file",
                            picked=picked, source=f"file:{name}",
                        ))
                        i = m.end()
                        continue
                    warnings.append(f"wildcard file `{name}` に有効な行が無い")
                else:
                    warnings.append(f"wildcard file `{name}` が未定義")
                # フォールバック: 元の `__name__` をそのままリテラルとして残す
                out.append(m.group(0))
                i = m.end()
                continue

        out.append(c)
        i += 1

    return ExpandResult(text="".join(out), choices=choices, warnings=warnings)
