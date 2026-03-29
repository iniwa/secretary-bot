"""PC・サーバー状態確認ユニット。"""

from src.units.base_unit import BaseUnit


class StatusUnit(BaseUnit):
    UNIT_NAME = "status"
    UNIT_DESCRIPTION = "PCやサーバーの稼働状況を確認。「PCは起きてる？」「ステータス確認」など。"

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        try:
            result = await self._check_status()
            self.breaker.record_success()
            self.session_done = True
            return result
        except Exception:
            self.breaker.record_failure()
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
