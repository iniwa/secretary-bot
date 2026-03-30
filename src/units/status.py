"""PC・サーバー状態確認ユニット。"""

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit


class StatusUnit(BaseUnit):
    UNIT_NAME = "status"
    UNIT_DESCRIPTION = "PCやサーバーの稼働状況を確認。「PCは起きてる？」「ステータス確認」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            result = await self._check_status()
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _check_status(self) -> str:
        lines = ["🖥️ システム状態", "━━━━━━━━━━━━━━━━━━━━"]

        # Ollama
        ollama_ok = self.bot.llm_router.ollama_available
        lines.append(f"  Ollama:  {'🟢 稼働中' if ollama_ok else '🔴 停止中'}")

        # Windows Agents
        pool = self.bot.unit_manager.agent_pool
        for agent in pool._agents:
            alive = await pool._is_alive(agent)
            name = agent.get("name", agent["id"])
            mode = pool.get_mode(agent["id"])
            lines.append(f"  {name}:  {'🟢 稼働中' if alive else '🔴 停止中'}（{mode}）")

        lines.append("━━━━━━━━━━━━━━━━━━━━")

        # DB
        try:
            row = await self.bot.database.fetchone("SELECT COUNT(*) as cnt FROM conversation_log")
            lines.append(f"  会話ログ:      {row['cnt']}件")
        except Exception:
            lines.append("  会話ログ:      取得失敗")

        # ChromaDB
        for col_name in ["ai_memory", "people_memory"]:
            count = self.bot.chroma.count(col_name)
            lines.append(f"  {col_name}:  {count}件")

        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(StatusUnit(bot))
