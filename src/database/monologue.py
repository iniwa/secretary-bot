"""InnerMind モノローグ・自己モデル関連のDBメソッド。"""

from src.database._base import jst_now


class MonologueMixin:
    # --- InnerMind モノローグ ---

    async def save_monologue(
        self, monologue: str, mood: str | None = None,
        did_notify: bool = False, notified_message: str | None = None,
        context_json: str = "",
        action: str | None = None, reasoning: str | None = None,
        action_params: str | None = None, action_result: str | None = None,
        pending_id: int | None = None,
    ) -> int:
        """モノローグを保存し、挿入されたIDを返す。
        action != None の行は自律アクションの decision ログ。"""
        cursor = await self.execute(
            "INSERT INTO mimi_monologue "
            "(monologue, mood, did_notify, notified_message, created_at, context_json, "
            " action, reasoning, action_params, action_result, pending_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (monologue, mood, 1 if did_notify else 0, notified_message, jst_now(), context_json,
             action, reasoning, action_params, action_result, pending_id),
        )
        return cursor.lastrowid

    async def set_monologue_action_result(
        self, monologue_id: int, action_result: str,
    ) -> None:
        """decision 実行後に結果 JSON を書き戻す。"""
        await self.execute(
            "UPDATE mimi_monologue SET action_result = ? WHERE id = ?",
            (action_result, monologue_id),
        )

    async def update_monologue_notify(
        self, monologue_id: int, notified_message: str,
    ) -> None:
        """モノローグの発言情報を更新する。"""
        await self.execute(
            "UPDATE mimi_monologue SET did_notify = 1, notified_message = ? WHERE id = ?",
            (notified_message, monologue_id),
        )

    async def get_monologues(
        self, limit: int = 50, did_notify_only: bool = False,
    ) -> list[dict]:
        """モノローグ履歴を取得する。"""
        where = " WHERE did_notify = 1" if did_notify_only else ""
        return await self.fetchall(
            f"SELECT * FROM mimi_monologue{where} ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_last_monologue(self) -> dict | None:
        """最新のモノローグを1件取得する。"""
        return await self.fetchone(
            "SELECT * FROM mimi_monologue ORDER BY created_at DESC LIMIT 1"
        )

    # --- InnerMind 自己モデル ---

    async def upsert_self_model(self, key: str, value: str) -> None:
        """自己モデルのkey-valueを更新（存在すればUPDATE、なければINSERT）。"""
        existing = await self.fetchone(
            "SELECT id FROM mimi_self_model WHERE key = ?", (key,)
        )
        if existing:
            await self.execute(
                "UPDATE mimi_self_model SET value = ?, updated_at = ? WHERE key = ?",
                (value, jst_now(), key),
            )
        else:
            await self.execute(
                "INSERT INTO mimi_self_model (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, jst_now()),
            )

    async def get_self_model(self) -> dict[str, str]:
        """自己モデル全体をdict形式で取得する。"""
        rows = await self.fetchall("SELECT key, value FROM mimi_self_model")
        return {r["key"]: r["value"] for r in rows}
