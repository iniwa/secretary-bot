"""人物記憶（Geminiフォールバック可）。"""

import uuid

from src.logger import get_logger

log = get_logger(__name__)

COLLECTION = "people_memory"


class PeopleMemory:
    def __init__(self, bot):
        self.bot = bot

    async def save(self, text: str, metadata: dict | None = None) -> None:
        doc_id = uuid.uuid4().hex[:16]
        self.bot.chroma.add(COLLECTION, doc_id, text, metadata)
        log.info("Saved people_memory: %.60s", text)

    async def extract_and_save(self, conversation: str, user_id: str = "") -> None:
        """会話から人物情報を抽出して保存。"""
        prompt = (
            "以下の会話から、ユーザーの情報・好み・特徴として記憶すべき内容を抽出してください。\n"
            "記憶すべきものがなければ「なし」と答えてください。\n\n"
            f"{conversation}"
        )
        try:
            result = await self.bot.llm_router.generate(prompt, purpose="memory_extraction")
            if result.strip() and result.strip() != "なし":
                metadata = {"user_id": user_id} if user_id else None
                await self.save(result.strip(), metadata)
        except Exception as e:
            log.warning("people_memory extraction failed: %s", e)

    def recall(self, query: str, n_results: int = 5, user_id: str = "") -> list[dict]:
        where = {"user_id": user_id} if user_id else None
        return self.bot.chroma.search(COLLECTION, query, n_results, where=where)
