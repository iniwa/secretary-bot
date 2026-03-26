"""雑談・相談ユニット（フォールバック先）。"""

from src.errors import AllLLMsUnavailableError
from src.memory.ai_memory import AIMemory
from src.memory.people_memory import PeopleMemory
from src.units.base_unit import BaseUnit


class ChatUnit(BaseUnit):
    SKILL_NAME = "chat"
    SKILL_DESCRIPTION = "雑談や相談。他のスキルに該当しない場合のフォールバック先。"

    def __init__(self, bot):
        super().__init__(bot)
        self.ai_memory = AIMemory(bot)
        self.people_memory = PeopleMemory(bot)

    async def execute(self, ctx, parsed: dict) -> str | None:
        self.breaker.check()
        message = parsed.get("message", "")
        if not message:
            return None

        try:
            response = await self._generate_response(message)
            self.breaker.record_success()

            # 記憶抽出（非同期・失敗しても無視）
            conversation = f"user: {message}\nassistant: {response}"
            try:
                await self.ai_memory.extract_and_save(conversation)
                await self.people_memory.extract_and_save(conversation)
            except Exception:
                pass

            return response
        except AllLLMsUnavailableError:
            self.breaker.record_failure()
            return "今はちょっと頭が働かないので、また後で話しかけてね。"
        except Exception:
            self.breaker.record_failure()
            raise

    async def _generate_response(self, message: str) -> str:
        config = self.bot.config
        character = config.get("character", {})
        ollama_available = self.bot.llm_router.ollama_available

        # システムプロンプト構築
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

        system = "\n".join(system_parts) if system_parts else None

        response = await self.bot.llm_router.generate(
            message,
            system=system,
            purpose="conversation",
        )

        # 省エネモード時は冒頭文言を付与
        if not ollama_available:
            response = f"現在省エネ稼働中です。{response}"

        return response


async def setup(bot) -> None:
    await bot.add_cog(ChatUnit(bot))
