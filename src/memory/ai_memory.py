"""AI自身の記憶（Ollama専用）。"""

import uuid

from src.logger import get_logger

log = get_logger(__name__)

COLLECTION = "ai_memory"


class AIMemory:
    def __init__(self, bot):
        self.bot = bot

    async def save(self, text: str, metadata: dict | None = None) -> None:
        """AI自身の記憶を保存（Ollama必須）。"""
        if not self.bot.llm_router.ollama_available:
            log.info("Skipping ai_memory save: Ollama unavailable")
            return

        doc_id = uuid.uuid4().hex[:16]
        self.bot.chroma.add(COLLECTION, doc_id, text, metadata)
        log.info("Saved ai_memory: %.60s", text)

    async def extract_and_save(self, conversation: str) -> None:
        """会話からAI自身の体験・気づきを抽出して保存（Ollama専用）。"""
        if not self.bot.llm_router.ollama_available:
            return

        prompt = (
            "以下の会話から、AI自身の体験・感情・気づきとして記憶すべき内容を抽出してください。\n"
            "記憶すべきものがなければ「なし」と答えてください。\n\n"
            f"{conversation}"
        )
        try:
            result = await self.bot.llm_router.generate(prompt, purpose="memory_extraction", ollama_only=True)
            if result.strip() and result.strip() != "なし":
                await self.save(result.strip())
        except Exception as e:
            log.warning("ai_memory extraction failed: %s", e)

    def recall(self, query: str, n_results: int = 5) -> list[dict]:
        return self.bot.chroma.search(COLLECTION, query, n_results)
