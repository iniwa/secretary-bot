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
            "以下の会話から、ユーザー本人が明言した情報・好み・特徴を箇条書きで抽出してください。\n"
            "【ルール】\n"
            "- ユーザー(user)の発言のみを対象とし、assistant側の発言は無視すること\n"
            "- 抽出する情報の例: 名前、職業、趣味、好きなもの、苦手なもの、習慣、予定など\n"
            "- 推測・解釈・説明は一切書かず、事実だけを短く箇条書きにすること\n"
            "- 記憶すべき情報がなければ「なし」とだけ答えること\n"
            "- 必ず日本語で出力すること\n\n"
            f"{conversation}"
        )
        try:
            result = await self.bot.llm_router.generate(prompt, purpose="memory_extraction")
            cleaned = result.strip()
            if not cleaned or "なし" in cleaned and len(cleaned) < 20:
                return
            metadata = {"user_id": user_id} if user_id else None
            await self.save(cleaned, metadata)
        except Exception as e:
            log.warning("people_memory extraction failed: %s", e)

    def recall(self, query: str, n_results: int = 5, user_id: str = "") -> list[dict]:
        where = {"user_id": user_id} if user_id else None
        return self.bot.chroma.search(COLLECTION, query, n_results, where=where)
