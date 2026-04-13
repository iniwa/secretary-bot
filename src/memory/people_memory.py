"""人物記憶（Geminiフォールバック可）。"""

from src.logger import get_logger

log = get_logger(__name__)

COLLECTION = "people_memory"

_DEFAULT_SKIP = 0.92
_DEFAULT_MERGE = 0.80


class PeopleMemory:
    def __init__(self, bot):
        self.bot = bot

    def _thresholds(self) -> tuple[float, float]:
        mem_cfg = self.bot.config.get("memory", {}) if hasattr(self.bot, "config") else {}
        skip = float(mem_cfg.get("dedup_skip_threshold", _DEFAULT_SKIP))
        merge = float(mem_cfg.get("dedup_merge_threshold", _DEFAULT_MERGE))
        return skip, merge

    async def save(self, text: str, metadata: dict | None = None) -> None:
        """人物情報を保存。意味的重複は dedup で処理。"""
        skip, merge = self._thresholds()
        status = self.bot.chroma.add_with_dedup(
            COLLECTION, text, metadata,
            skip_threshold=skip, merge_threshold=merge,
        )
        log.info("Saved people_memory (%s): %.60s", status, text)

    async def extract_and_save(self, conversation: str, user_id: str = "") -> None:
        """会話から人物情報を抽出して保存。具体的な固有名詞とタグを必ず残す。"""
        prompt = (
            "以下の会話から、ユーザー本人が明言した情報・好み・特徴を抽出してください。\n"
            "【抽出対象】\n"
            "- 名前、職業、住所、所属などの基本情報\n"
            "- 具体的な好み・興味（ゲームタイトル・ジャンル・技術領域・食べ物などの固有名詞を必ず残すこと）\n"
            "- 苦手なもの・嫌いなもの\n"
            "- 習慣の時間帯・曜日パターン（例: 夜型、平日夜プレイ、週末まとめ買い等）\n"
            "- 予定・目標\n"
            "【ルール】\n"
            "- ユーザー(user)の発言のみを対象とし、assistant側の発言は無視すること\n"
            "- 後で検索しやすいよう、固有名詞（ゲームタイトル・製品名・技術名など）は省略せずそのまま残すこと\n"
            "- 推測・解釈・説明は一切書かず、事実だけを短く箇条書きにすること\n"
            "- 箇条書きの最後に、必ず以下の形式でタグ行を追加すること:\n"
            "  【タグ】FPS, Tarkov, 夜型, コーヒー\n"
            "  （短い名詞タグをカンマ区切り。他モジュールで検索に使うので必ず付けること）\n"
            "- 記憶すべき情報がなければ「なし」とだけ答えること\n"
            "- 必ず日本語で出力すること\n\n"
            f"{conversation}"
        )
        try:
            result = await self.bot.llm_router.generate(prompt, purpose="memory_extraction")
            cleaned = result.strip()
            if not cleaned or ("なし" in cleaned and len(cleaned) < 20):
                return
            metadata = {"user_id": user_id} if user_id else None
            await self.save(cleaned, metadata)
        except Exception as e:
            log.warning("people_memory extraction failed: %s", e)

    def recall(self, query: str, n_results: int = 5, user_id: str = "") -> list[dict]:
        where = {"user_id": user_id} if user_id else None
        return self.bot.chroma.search(COLLECTION, query, n_results, where=where)
