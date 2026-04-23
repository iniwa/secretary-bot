"""楽天ブックス書籍検索 API ラッパー。

紙書籍（漫画・ラノベ等）の新刊検出用。ISBN・タイトル・著者・発売日を返す。

⚠️ エンドポイント URL は実装着手時に公式ドキュメントで最終確認:
   https://webservice.rakuten.co.jp/documentation/books-book-search
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.logger import get_logger
from src.rakuten.client import RakutenApiClient

log = get_logger(__name__)

# 2026 年 2 月新仕様の暫定 URL。config.yaml から上書き可能にする。
DEFAULT_BOOKS_SEARCH_URL = (
    "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404"
)

# 漫画・ラノベ系ジャンル ID
GENRE_COMIC = "001001"
GENRE_NOVEL = "001004"


@dataclass(frozen=True)
class BookItem:
    """楽天ブックスの 1 書籍情報。"""

    isbn: str
    title: str
    sub_title: str
    series_name: str
    author: str
    publisher: str
    sales_date_raw: str  # "2026年05月01日" 形式
    item_url: str
    image_url: str
    item_caption: str
    item_price: int

    @property
    def sales_date_iso(self) -> str | None:
        """'YYYY年MM月DD日' を ISO 8601 ('YYYY-MM-DD') に変換。解釈失敗時 None。"""
        raw = self.sales_date_raw
        if not raw:
            return None
        try:
            y = raw.split("年")[0]
            m = raw.split("年")[1].split("月")[0]
            d = raw.split("月")[1].rstrip("日")
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except (IndexError, ValueError):
            log.debug("sales_date parse failed: %r", raw)
            return None


def _item_from_response(raw: dict[str, Any]) -> BookItem:
    """楽天 API レスポンスの 1 Item を BookItem に変換する。

    formatVersion=1 では `{"Item": {...}}` のネスト構造。
    """
    item = raw.get("Item", raw)
    return BookItem(
        isbn=str(item.get("isbn", "")),
        title=str(item.get("title", "")),
        sub_title=str(item.get("subTitle", "")),
        series_name=str(item.get("seriesName", "")),
        author=str(item.get("author", "")),
        publisher=str(item.get("publisherName", "")),
        sales_date_raw=str(item.get("salesDate", "")),
        item_url=str(item.get("itemUrl", "")),
        image_url=str(item.get("largeImageUrl") or item.get("mediumImageUrl", "")),
        item_caption=str(item.get("itemCaption", "")),
        item_price=int(item.get("itemPrice", 0) or 0),
    )


async def search_books(
    client: RakutenApiClient,
    *,
    title: str | None = None,
    author: str | None = None,
    publisher: str | None = None,
    sort: str = "-releaseDate",
    hits: int = 30,
    genre_id: str | None = None,
    out_of_stock: bool = True,
    endpoint: str = DEFAULT_BOOKS_SEARCH_URL,
) -> list[BookItem]:
    """楽天ブックス書籍検索 API を叩いて結果を返す（新刊順既定）。

    title / author / publisher のいずれかが必須。
    """
    if not any([title, author, publisher]):
        raise ValueError("title, author, publisher のいずれかを指定してね")

    params: dict[str, Any] = {
        "sort": sort,
        "hits": max(1, min(hits, 30)),
        "outOfStockFlag": 1 if out_of_stock else 0,
    }
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    if publisher:
        params["publisherName"] = publisher
    if genre_id:
        params["booksGenreId"] = genre_id

    data = await client.request(endpoint, params)
    items = data.get("Items", [])
    return [_item_from_response(raw) for raw in items]
