"""楽天 Web Service API 共通パッケージ（2026 年 2 月新仕様）。

旧 API は 2026-05-13 に停止。新仕様前提で実装する。

公開シンボル:
- RakutenApiClient / RakutenApiConfig — HTTP クライアント
- BookItem / search_books            — 楽天ブックス書籍検索
- KoboItem / KoboMatch / search_kobo / is_available_in_kobo — 楽天 Kobo 検索
"""

from src.rakuten.books_api import BookItem, search_books
from src.rakuten.client import RakutenApiClient, RakutenApiConfig
from src.rakuten.kobo_api import (
    KoboItem,
    KoboMatch,
    is_available_in_kobo,
    search_kobo,
)

__all__ = [
    "BookItem",
    "KoboItem",
    "KoboMatch",
    "RakutenApiClient",
    "RakutenApiConfig",
    "is_available_in_kobo",
    "search_books",
    "search_kobo",
]
