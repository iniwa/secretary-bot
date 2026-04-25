"""Wildcard ファイル辞書・プロンプトセッション関連のDBメソッド。"""

from datetime import datetime, timedelta

from src.database._base import JST, jst_now


class WildcardMixin:
    # === 画像生成: wildcard_files ===

    async def wildcard_file_list(self, *, include_nsfw: bool = True) -> list[dict]:
        """一覧表示用。content は含めず length だけ返す（軽量）。
        include_nsfw=False で is_nsfw=1 の行を除外（NSFWモードOFF時用）。"""
        where = "" if include_nsfw else " WHERE is_nsfw = 0"
        return await self.fetchall(
            "SELECT name, description, updated_at, is_nsfw, length(content) AS size "
            f"FROM wildcard_files{where} ORDER BY name ASC",
        )

    async def wildcard_file_get(self, name: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM wildcard_files WHERE name = ?", (name,),
        )

    async def wildcard_file_get_all(self) -> list[dict]:
        """expand 用: 全ファイルの (name, content) を取得。"""
        return await self.fetchall(
            "SELECT name, content FROM wildcard_files",
        )

    async def wildcard_file_put(
        self, *, name: str, content: str, description: str | None = None,
        is_nsfw: bool = False,
    ) -> None:
        """UPSERT。既存なら content / description / is_nsfw / updated_at を差し替え。"""
        await self.execute(
            "INSERT INTO wildcard_files (name, content, description, is_nsfw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "content = excluded.content, "
            "description = excluded.description, "
            "is_nsfw = excluded.is_nsfw, "
            "updated_at = excluded.updated_at",
            (name, content, description, 1 if is_nsfw else 0, jst_now(), jst_now()),
        )

    async def wildcard_file_delete(self, name: str) -> bool:
        rowcount = await self.execute_returning_rowcount(
            "DELETE FROM wildcard_files WHERE name = ?", (name,),
        )
        return rowcount == 1

    # === 画像生成: prompt_sessions ===

    async def prompt_session_get_active(
        self, user_id: str, platform: str,
    ) -> dict | None:
        """未失効（expires_at > now）で最も新しいセッションを返す。"""
        return await self.fetchone(
            "SELECT * FROM prompt_sessions "
            "WHERE user_id = ? AND platform = ? "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY updated_at DESC LIMIT 1",
            (user_id, platform, jst_now()),
        )

    async def prompt_session_get(self, session_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM prompt_sessions WHERE id = ?", (session_id,),
        )

    async def prompt_session_insert(
        self, *, user_id: str, platform: str,
        positive: str | None, negative: str | None,
        history_json: str | None = None,
        base_workflow_id: int | None = None,
        params_json: str | None = None,
        ttl_days: int = 7,
    ) -> int:
        """新規プロンプトセッションを作成し id を返す。"""
        now = datetime.now(JST)
        expires = (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = await self.execute(
            "INSERT INTO prompt_sessions "
            "(user_id, platform, positive, negative, history_json, "
            " base_workflow_id, params_json, updated_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, platform, positive, negative, history_json,
             base_workflow_id, params_json, jst_now(), expires),
        )
        return int(cur.lastrowid or 0)

    async def prompt_session_update(
        self, session_id: int, *,
        positive: str | None = None, negative: str | None = None,
        history_json: str | None = None,
        base_workflow_id: int | None = None,
        params_json: str | None = None,
        ttl_days: int | None = 7,
    ) -> bool:
        """部分更新。None のフィールドは更新対象外（空文字にしたい場合は明示的に ""）。"""
        sets: list[str] = []
        params: list = []
        if positive is not None:
            sets.append("positive = ?")
            params.append(positive)
        if negative is not None:
            sets.append("negative = ?")
            params.append(negative)
        if history_json is not None:
            sets.append("history_json = ?")
            params.append(history_json)
        if base_workflow_id is not None:
            sets.append("base_workflow_id = ?")
            params.append(base_workflow_id)
        if params_json is not None:
            sets.append("params_json = ?")
            params.append(params_json)
        sets.append("updated_at = ?")
        params.append(jst_now())
        if ttl_days is not None:
            expires = (datetime.now(JST) + timedelta(days=ttl_days)).strftime(
                "%Y-%m-%d %H:%M:%S",
            )
            sets.append("expires_at = ?")
            params.append(expires)
        params.append(session_id)
        await self.execute(
            f"UPDATE prompt_sessions SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return True

    async def prompt_session_list(
        self, user_id: str | None = None, limit: int = 20,
    ) -> list[dict]:
        if user_id:
            return await self.fetchall(
                "SELECT * FROM prompt_sessions "
                "WHERE user_id = ? "
                "AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, jst_now(), limit),
            )
        return await self.fetchall(
            "SELECT * FROM prompt_sessions "
            "WHERE expires_at IS NULL OR expires_at > ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (jst_now(), limit),
        )

    async def prompt_session_delete(self, session_id: int) -> None:
        await self.execute(
            "DELETE FROM prompt_sessions WHERE id = ?", (session_id,),
        )

    async def prompt_session_cleanup_expired(self) -> int:
        """TTL 切れのセッションを削除し、削除件数を返す。"""
        rows = await self.fetchall(
            "SELECT id FROM prompt_sessions "
            "WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (jst_now(),),
        )
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        await self.execute(
            f"DELETE FROM prompt_sessions WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return len(ids)
