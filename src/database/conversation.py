"""会話ログ関連のDBメソッド。"""

from datetime import datetime, timedelta

from src.database._base import JST, jst_now


class ConversationMixin:
    async def log_conversation(
        self, channel: str, role: str, content: str,
        mode: str | None = None, unit: str | None = None,
        user_id: str = "", channel_name: str = "",
    ) -> None:
        await self.execute(
            "INSERT INTO conversation_log (timestamp, channel, role, content, user_id, mode, unit, channel_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (jst_now(), channel, role, content, user_id, mode, unit, channel_name),
        )

    async def get_conversation_logs(
        self, limit: int = 50, offset: int = 0,
        keyword: str | None = None,
        channel: str | None = None,
        bot_only: bool = False,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if keyword:
            conditions.append("content LIKE ?")
            params.append(f"%{keyword}%")
        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        if bot_only:
            # webguiは全表示、discord系はボットが応答した会話のみ
            conditions.append(
                "(channel = 'webgui'"
                " OR role = 'assistant'"
                " OR (role = 'user' AND EXISTS ("
                "   SELECT 1 FROM conversation_log c2"
                "   WHERE c2.role = 'assistant'"
                "   AND c2.channel = conversation_log.channel"
                "   AND c2.id > conversation_log.id"
                "   AND c2.id <= conversation_log.id + 5"
                ")))"
            )
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM conversation_log{where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    async def get_recent_messages(self, limit: int = 20) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM conversation_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    async def get_recent_channel_messages(
        self, channel: str, limit: int = 20, user_id: str = "",
        minutes: int = 0,
    ) -> list[dict]:
        """チャネル・ユーザー単位の直近会話履歴を古い順で返す。

        minutes: 0以外を指定すると、現在時刻から指定分以内のメッセージのみ返す。
        """
        conditions = ["channel = ?"]
        params: list = [channel]
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if minutes > 0:
            cutoff = (datetime.now(JST) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        where = " AND ".join(conditions)
        params.append(limit)
        rows = await self.fetchall(
            f"SELECT role, content, channel_name FROM conversation_log "
            f"WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            tuple(params),
        )
        return list(reversed(rows))
