"""kobo_watch ユニット用 SQLite アクセサ（Mixin）。

テーブル:
  - book_watch_targets        監視対象（著者+タイトルキーワード）
  - book_watch_known_books    既知 ISBN（新刊判定用）
  - book_watch_detections     検出・通知履歴
  - system_state              IP 監視 / 最終実行時刻 など汎用 key/value
"""

from __future__ import annotations

from src.database._base import jst_now


class KoboWatchMixin:
    # === book_watch_targets ===

    async def kobo_target_add(
        self, *, author: str, title_keyword: str | None,
        user_id: str, notify_kobo_only: bool = False,
    ) -> int:
        """監視対象を追加。重複（author, title_keyword）は IntegrityError を投げる。"""
        cursor = await self.execute(
            "INSERT INTO book_watch_targets "
            "(author, title_keyword, user_id, enabled, notify_kobo_only, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (author, title_keyword, user_id,
             1 if notify_kobo_only else 0, jst_now()),
        )
        return int(cursor.lastrowid or 0)

    async def kobo_target_get(self, target_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM book_watch_targets WHERE id = ?", (int(target_id),),
        )

    async def kobo_target_list(self, *, enabled_only: bool = True) -> list[dict]:
        if enabled_only:
            return await self.fetchall(
                "SELECT * FROM book_watch_targets WHERE enabled = 1 "
                "ORDER BY created_at DESC"
            )
        return await self.fetchall(
            "SELECT * FROM book_watch_targets ORDER BY created_at DESC"
        )

    async def kobo_target_find_by_keyword(self, keyword: str) -> list[dict]:
        """title_keyword または author の部分一致で候補を返す（古い順）。"""
        like = f"%{keyword}%"
        return await self.fetchall(
            "SELECT * FROM book_watch_targets "
            "WHERE title_keyword LIKE ? OR author LIKE ? "
            "ORDER BY created_at DESC",
            (like, like),
        )

    async def kobo_target_remove(self, target_id: int) -> bool:
        # CASCADE が SQLite で効くには PRAGMA foreign_keys が要るので明示削除も併用
        await self.execute(
            "DELETE FROM book_watch_known_books WHERE target_id = ?",
            (int(target_id),),
        )
        await self.execute(
            "DELETE FROM book_watch_detections WHERE target_id = ?",
            (int(target_id),),
        )
        rc = await self.execute_returning_rowcount(
            "DELETE FROM book_watch_targets WHERE id = ?", (int(target_id),),
        )
        return rc > 0

    async def kobo_target_update(
        self, target_id: int, *,
        enabled: bool | None = None,
        notify_kobo_only: bool | None = None,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if notify_kobo_only is not None:
            sets.append("notify_kobo_only = ?")
            params.append(1 if notify_kobo_only else 0)
        if not sets:
            return False
        params.append(int(target_id))
        rc = await self.execute_returning_rowcount(
            f"UPDATE book_watch_targets SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return rc > 0

    # === book_watch_known_books ===

    async def kobo_known_record(
        self, *, isbn: str, target_id: int, title: str, author: str,
        publisher: str | None = None, sales_date: str | None = None,
        item_url: str | None = None, image_url: str | None = None,
    ) -> bool:
        """ISBN を既知として記録。重複は INSERT OR IGNORE 相当で True/False を返す。"""
        rc = await self.execute_returning_rowcount(
            "INSERT OR IGNORE INTO book_watch_known_books "
            "(isbn, target_id, title, author, publisher, sales_date, "
            " item_url, image_url, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (isbn, int(target_id), title, author, publisher, sales_date,
             item_url, image_url, jst_now()),
        )
        return rc > 0

    async def kobo_known_exists(self, isbn: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM book_watch_known_books WHERE isbn = ? LIMIT 1",
            (isbn,),
        )
        return row is not None

    async def kobo_known_get(self, isbn: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM book_watch_known_books WHERE isbn = ?", (isbn,),
        )

    async def kobo_known_list_by_target(self, target_id: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM book_watch_known_books WHERE target_id = ? "
            "ORDER BY sales_date DESC",
            (int(target_id),),
        )

    # === book_watch_detections ===

    async def kobo_detection_record(
        self, *, isbn: str, target_id: int, kobo_available: bool,
        kobo_url: str | None = None, notified_at: str | None = None,
        suppressed_reason: str | None = None,
    ) -> int:
        cursor = await self.execute(
            "INSERT INTO book_watch_detections "
            "(isbn, target_id, kobo_available, kobo_url, notified_at, "
            " suppressed_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (isbn, int(target_id), 1 if kobo_available else 0, kobo_url,
             notified_at, suppressed_reason, jst_now()),
        )
        return int(cursor.lastrowid or 0)

    async def kobo_detection_mark_notified(
        self, detection_id: int, notified_at: str,
    ) -> bool:
        rc = await self.execute_returning_rowcount(
            "UPDATE book_watch_detections "
            "SET notified_at = ?, suppressed_reason = NULL WHERE id = ?",
            (notified_at, int(detection_id)),
        )
        return rc > 0

    async def kobo_detection_list(self, limit: int = 50) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM book_watch_detections "
            "ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )

    async def kobo_detection_list_pending(self) -> list[dict]:
        """通知保留中（notified_at IS NULL かつ suppressed_reason が一時的なもの）。"""
        return await self.fetchall(
            "SELECT * FROM book_watch_detections "
            "WHERE notified_at IS NULL "
            "  AND (suppressed_reason IS NULL "
            "       OR suppressed_reason NOT IN ('kobo_only_filter')) "
            "ORDER BY created_at ASC"
        )

    # === system_state（IP 監視 / 最終実行時刻 など）===

    async def system_state_get(self, key: str) -> str | None:
        row = await self.fetchone(
            "SELECT value FROM system_state WHERE key = ?", (key,),
        )
        return row.get("value") if row else None

    async def system_state_set(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO system_state (key, value, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value = excluded.value, updated_at = excluded.updated_at",
            (key, value, jst_now()),
        )
