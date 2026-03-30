"""雑談・相談ユニット（フォールバック先）。"""

from src.errors import AllLLMsUnavailableError
from src.flow_tracker import get_flow_tracker
from src.memory.ai_memory import AIMemory
from src.memory.people_memory import PeopleMemory
from src.units.base_unit import BaseUnit


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
        if not message:
            return None

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            response = await self._generate_response(message, flow_id)
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)

            # 記憶抽出（非同期・失敗しても無視）
            await ft.emit("MEM_WRITE", "active", {}, flow_id)
            conversation = f"user: {message}\nassistant: {response}"
            try:
                await self.ai_memory.extract_and_save(conversation)
                await self.people_memory.extract_and_save(conversation)
                await ft.emit("MEM_WRITE", "done", {}, flow_id)
            except Exception:
                await ft.emit("MEM_WRITE", "error", {}, flow_id)

            return response
        except AllLLMsUnavailableError:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"reason": "all_llms_unavailable"}, flow_id)
            return "今はちょっと頭が働かないので、また後で話しかけてね。"
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {}, flow_id)
            raise

    async def _generate_response(self, message: str, flow_id: str | None = None) -> str:
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

            # 人物記憶
            people_memories = self.people_memory.recall(message, n_results=3)
            if people_memories:
                system_parts.append("【ユーザーについて】")
                for m in people_memories:
                    system_parts.append(f"- {m['text']}")
        else:
            # 省エネモード: people_memory のみ
            people_memories = self.people_memory.recall(message, n_results=3)
            if people_memories:
                system_parts.append("【ユーザーについて】")
                for m in people_memories:
                    system_parts.append(f"- {m['text']}")

        await ft.emit("MEM_SEARCH", "done", {"count": len(system_parts)}, flow_id)

        system = "\n".join(system_parts) if system_parts else None

        response = await self.llm.generate(message, system=system)

        # 省エネモード時は冒頭文言を付与
        if not ollama_available:
            response = f"現在省エネ稼働中です。{response}"

        return response


async def setup(bot) -> None:
    await bot.add_cog(ChatUnit(bot))
