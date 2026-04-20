"""InnerMind 自律アクション (pending_actions) 関連のDBメソッド。"""

from datetime import datetime

from src.database._base import JST, jst_now


class PendingActionMixin:
    # --- InnerMind 自律アクション: pending_actions ---

    async def create_pending_action(
        self, *, monologue_id: int | None, tier: int,
        unit_name: str | None, method: str | None, params: str,
        reasoning: str, summary: str, user_id: str,
        channel_id: str | None, expires_at: str,
    ) -> int:
        cursor = await self.execute(
            "INSERT INTO pending_actions "
            "(monologue_id, tier, unit_name, method, params, reasoning, summary, "
            " status, user_id, channel_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (monologue_id, tier, unit_name, method, params, reasoning, summary,
             user_id, channel_id, jst_now(), expires_at),
        )
        return cursor.lastrowid

    async def get_pending_action(self, pending_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM pending_actions WHERE id = ?", (pending_id,)
        )

    async def list_pending_actions(
        self, *, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        if status:
            return await self.fetchall(
                "SELECT * FROM pending_actions WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        return await self.fetchall(
            "SELECT * FROM pending_actions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def count_pending_today(self, tier: int, user_id: str) -> int:
        """今日作成された同 tier の pending 数（上限制御用）。"""
        today = datetime.now(JST).strftime("%Y-%m-%d")
        row = await self.fetchone(
            "SELECT COUNT(*) AS c FROM pending_actions "
            "WHERE tier = ? AND user_id = ? AND date(created_at) = ?",
            (tier, user_id, today),
        )
        return int(row["c"]) if row else 0

    async def set_pending_discord_message(
        self, pending_id: int, message_id: str,
    ) -> None:
        await self.execute(
            "UPDATE pending_actions SET discord_message_id = ? WHERE id = ?",
            (message_id, pending_id),
        )

    async def resolve_pending_action(
        self, pending_id: int, status: str,
        result: str | None = None, error: str | None = None,
    ) -> None:
        """pending_action を approved/rejected/expired/executed/failed/cancelled のいずれかに確定。"""
        await self.execute(
            "UPDATE pending_actions "
            "SET status = ?, result = ?, error = ?, resolved_at = ? WHERE id = ?",
            (status, result, error, jst_now(), pending_id),
        )

    async def count_pending_unread(self, user_id: str | None = None) -> int:
        """承認待ちの pending 件数（通知バッジ用）。"""
        if user_id:
            row = await self.fetchone(
                "SELECT COUNT(*) AS c FROM pending_actions "
                "WHERE status = 'pending' AND user_id = ?", (user_id,),
            )
        else:
            row = await self.fetchone(
                "SELECT COUNT(*) AS c FROM pending_actions WHERE status = 'pending'"
            )
        return int(row["c"]) if row else 0
