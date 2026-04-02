"""雑談・相談ユニット（フォールバック先）。"""

import asyncio

from src.errors import AllLLMsUnavailableError
from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.memory.ai_memory import AIMemory
from src.memory.people_memory import PeopleMemory
from src.units.base_unit import BaseUnit

log = get_logger(__name__)


class ChatUnit(BaseUnit):
    UNIT_NAME = "chat"
    UNIT_DESCRIPTION = "雑談や相談。他のユニットに該当しない場合のフォールバック先。"

    def __init__(self, bot):
        super().__init__(bot)
        self.ai_memory = AIMemory(bot)
        self.people_memory = PeopleMemory(bot)

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        message = parsed.get("message", "")
        user_id = parsed.get("user_id", "")
        if not message:
            return None

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            response = await self._generate_response(message, flow_id, user_id=user_id)
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)

            # 記憶抽出をバックグラウンドで実行（返答速度に影響させない）
            conversation = f"user: {message}\nassistant: {response}"
            asyncio.create_task(self._extract_memories_bg(conversation, user_id, flow_id))

            return response
        except AllLLMsUnavailableError:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"reason": "all_llms_unavailable"}, flow_id)
            return "今はちょっと頭が働かないので、また後で話しかけてね。"
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {}, flow_id)
            raise

    async def _extract_memories_bg(self, conversation: str, user_id: str, flow_id: str | None) -> None:
        """記憶抽出をバックグラウンドで実行する。失敗しても無視。"""
        ft = get_flow_tracker()
        await ft.emit("MEM_WRITE", "active", {}, flow_id)
        try:
            await self.ai_memory.extract_and_save(conversation)
            await self.people_memory.extract_and_save(conversation, user_id=user_id)
            await ft.emit("MEM_WRITE", "done", {}, flow_id)
        except Exception as e:
            log.warning("Background memory extraction failed: %s", e)
            await ft.emit("MEM_WRITE", "error", {}, flow_id)

    async def _generate_response(self, message: str, flow_id: str | None = None, user_id: str = "") -> str:
        ft = get_flow_tracker()
        config = self.bot.config
        character = config.get("character", {})
        ollama_available = self.bot.llm_router.ollama_available

        # 記憶検索
        await ft.emit("MEM_SEARCH", "active", {}, flow_id)
        system_parts = []

        if ollama_available:
            # フルキャラクター + 全記憶
            persona = character.get("persona", "")
            if persona:
                system_parts.append(persona)

            # AI記憶
            ai_memories = self.ai_memory.recall(message, n_results=3)
            if ai_memories:
                system_parts.append("【あなたの記憶】")
                for m in ai_memories:
                    system_parts.append(f"- {m['text']}")

            # 人物記憶（ユーザーごと）
            people_memories = self.people_memory.recall(message, n_results=3, user_id=user_id)
            if people_memories:
                system_parts.append("【ユーザーについて】")
                for m in people_memories:
                    system_parts.append(f"- {m['text']}")
        else:
            # 省エネモード: people_memory のみ（ユーザーごと）
            people_memories = self.people_memory.recall(message, n_results=3, user_id=user_id)
            if people_memories:
                system_parts.append("【ユーザーについて】")
                for m in people_memories:
                    system_parts.append(f"- {m['text']}")

        await ft.emit("MEM_SEARCH", "done", {"count": len(system_parts)}, flow_id)

        system = "\n".join(system_parts) if system_parts else None

        # 直近の会話履歴をプロンプトに付加（現在のメッセージは除く）
        history_limit = config.get("chat", {}).get("history_limit", 8)
        history_minutes = config.get("chat", {}).get("history_minutes", 60)
        history_rows = await self.bot.database.get_recent_channel_messages(
            "discord", limit=history_limit + 1, user_id=user_id,
            minutes=history_minutes,
        )
        # 末尾が今保存したばかりのユーザー発言と一致する場合は除外（重複防止）
        if history_rows and history_rows[-1]["role"] == "user" and history_rows[-1]["content"] == message:
            history_rows = history_rows[:-1]

        if history_rows:
            history_text = "\n".join(
                f"{'ユーザー' if r['role'] == 'user' else 'アシスタント'}: {r['content']}"
                for r in history_rows
            )
            prompt = f"【過去の会話履歴】\n{history_text}\n\n【現在のメッセージ】\n{message}"
        else:
            prompt = message

        response = await self.llm.generate(prompt, system=system)

        # 省エネモード時は冒頭文言を付与
        if not ollama_available:
            response = f"現在省エネ稼働中です。{response}"

        return response


async def setup(bot) -> None:
    await bot.add_cog(ChatUnit(bot))
