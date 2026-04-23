"""楽天 Kobo 電子書籍検索 API ラッパー。

紙書籍のタイトル+著者で検索し、類似度スコアで Kobo 版の存在を判定する。

⚠️ エンドポイント URL は実装着手時に公式ドキュメントで最終確認:
   https://webservice.rakuten.co.jp/documentation/kobo-ebook-search
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from src.logger import get_logger
from src.rakuten.client import RakutenApiClient

log = get_logger(__name__)

DEFAULT_KOBO_SEARCH_URL = (
    "https://openapi.rakuten.co.jp/services/api/Kobo/EbookSearch/20170426"
)

# 新仕様で koboGenreId が必須化されたため既定で電子書籍全般を指定する。
KOBO_GENRE_EBOOK_ALL = "101"


@dataclass(frozen=True)
class KoboItem:
    """楽天 Kobo の 1 電子書籍情報。"""

    title: str
    author: str
    publisher: str
    sales_date_raw: str
    item_url: str
    image_url: str
    item_caption: str
    item_price: int


@dataclass(frozen=True)
class KoboMatch:
    """紙書籍と Kobo 商品のマッチ結果。"""

    item: KoboItem
    title_similarity: float
    author_matched: bool


def _item_from_response(raw: dict[str, Any]) -> KoboItem:
    item = raw.get("Item", raw)
    return KoboItem(
        title=str(item.get("title", "")),
        author=str(item.get("author", "")),
        publisher=str(item.get("publisherName", "")),
        sales_date_raw=str(item.get("salesDate", "")),
        item_url=str(item.get("itemUrl", "")),
        image_url=str(item.get("largeImageUrl") or item.get("mediumImageUrl", "")),
        item_caption=str(item.get("itemCaption", "")),
        item_price=int(item.get("itemPrice", 0) or 0),
    )


async def search_kobo(
    client: RakutenApiClient,
    *,
    title: str | None = None,
    author: str | None = None,
    hits: int = 10,
    kobo_genre_id: str = KOBO_GENRE_EBOOK_ALL,
    endpoint: str = DEFAULT_KOBO_SEARCH_URL,
) -> list[KoboItem]:
    """Kobo 電子書籍を検索する。"""
    params: dict[str, Any] = {
        "hits": max(1, min(hits, 30)),
        "koboGenreId": kobo_genre_id,
    }
    if title:
        params["title"] = title
    if author:
        params["author"] = author

    data = await client.request(endpoint, params)
    items = data.get("Items", [])
    return [_item_from_response(raw) for raw in items]


def _normalize(s: str) -> str:
    """比較用にタイトルを正規化（空白・括弧・記号を除去）。"""
    if not s:
        return ""
    for ch in (
        "　", " ", "(", ")", "（", "）",
        "「", "」", "『", "』", "【", "】",
    ):
        s = s.replace(ch, "")
    return s.lower()


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _author_matches(paper_author: str, kobo_author: str) -> bool:
    """著者名が実質的に一致するか（部分一致で判定）。

    楽天ブックスと Kobo で姓名の空白有無など表記ブレがあるため
    正規化した上で片方がもう片方を含めば一致とみなす。
    """
    a = _normalize(paper_author)
    b = _normalize(kobo_author)
    if not a or not b:
        return False
    return a in b or b in a


async def is_available_in_kobo(
    client: RakutenApiClient,
    *,
    paper_title: str,
    paper_author: str,
    title_similarity_threshold: float = 0.80,
    author_must_match: bool = True,
    endpoint: str = DEFAULT_KOBO_SEARCH_URL,
) -> KoboMatch | None:
    """紙書籍の (タイトル, 著者) から Kobo 版を検索し、最もマッチする 1 件を返す。

    マッチ条件:
      - タイトル類似度 >= threshold
      - author_must_match=True なら著者一致
    """
    results = await search_kobo(
        client, title=paper_title, author=paper_author,
        hits=10, endpoint=endpoint,
    )
    if not results:
        return None

    best: KoboMatch | None = None
    for kobo in results:
        sim = _title_similarity(paper_title, kobo.title)
        author_ok = _author_matches(paper_author, kobo.author)
        if sim < title_similarity_threshold:
            continue
        if author_must_match and not author_ok:
            continue
        candidate = KoboMatch(
            item=kobo, title_similarity=sim, author_matched=author_ok,
        )
        if best is None or sim > best.title_similarity:
            best = candidate

    if best:
        log.info(
            "kobo_match_found paper=%r kobo=%r sim=%.3f",
            paper_title, best.item.title, best.title_similarity,
        )
    return best
