"""興味タグ抽出・スコアリング — people_memory の【タグ】行を集計して興味プロファイルを作る。"""

import math
import re
import unicodedata
from collections import Counter

from src.logger import get_logger

log = get_logger(__name__)

# 【タグ】で始まる行を抽出する正規表現（行頭・行中いずれでも）
_TAG_LINE_RE = re.compile(r"【タグ】\s*(.+)", re.MULTILINE)
# split区切り: カンマ / 読点 / スラッシュ（半角・全角両方）
_TAG_SPLIT_RE = re.compile(r"[,、，/／]")


def _normalize_tag(raw: str) -> str:
    """タグを正規化: 前後空白除去 → 全角英数を半角 → 英字を小文字化（日本語は維持）。"""
    if not raw:
        return ""
    # NFKCで全角英数 → 半角
    s = unicodedata.normalize("NFKC", raw).strip()
    # 英字のみ小文字化（日本語はそのまま）
    s = "".join(ch.lower() if ch.isascii() and ch.isalpha() else ch for ch in s)
    return s


def _extract_tags_from_document(document: str) -> list[str]:
    """1エントリのdocument文字列から【タグ】行を抽出して正規化済みタグのリストを返す。"""
    if not document:
        return []
    tags: list[str] = []
    for m in _TAG_LINE_RE.finditer(document):
        tag_str = m.group(1)
        for raw in _TAG_SPLIT_RE.split(tag_str):
            norm = _normalize_tag(raw)
            # 空文字・1文字は除外
            if len(norm) >= 2:
                tags.append(norm)
    return tags


async def extract_interest_tags(bot, top_n: int = 30) -> list[tuple[str, int]]:
    """people_memory 全件から【タグ】行を抽出・正規化・集計し、頻出順に返す。

    Returns:
        [(tag, count), ...]  count降順 top_n件
    """
    try:
        entries = bot.chroma.get_all("people_memory", limit=10000)
    except Exception as e:
        log.warning("interest_extractor: get_all failed: %s", e)
        return []

    counter: Counter[str] = Counter()
    for entry in entries:
        # chroma_client.get_all は "text" キー、契約では "document" キーも想定
        doc = entry.get("document") or entry.get("text") or ""
        for tag in _extract_tags_from_document(doc):
            counter[tag] += 1

    return counter.most_common(top_n)


async def score_text_by_interests(
    bot,
    text: str,
    interest_tags: list[tuple[str, int]] | None = None,
) -> tuple[float, list[str]]:
    """テキストに対して興味タグとのマッチ数ベースのスコアと、ヒットタグ一覧を返す。

    スコア: 各ヒットタグについて log2(count + 1) を加算（頻出タグの寄与を抑える）
    戻り値: (score, matched_tags)
    """
    if not text:
        return 0.0, []

    if interest_tags is None:
        interest_tags = await extract_interest_tags(bot)

    if not interest_tags:
        return 0.0, []

    # 大小文字無視で判定するため text 側も lower 化（日本語は影響なし）
    text_lower = text.lower()

    score = 0.0
    matched: list[str] = []
    for tag, count in interest_tags:
        if not tag:
            continue
        if tag in text_lower:
            score += math.log2(count + 1)
            matched.append(tag)
    return score, matched
