"""AI自身の記憶（Ollama専用）。"""

from src.logger import get_logger

log = get_logger(__name__)

COLLECTION = "ai_memory"

# デフォルト閾値（config未設定時のフォールバック）
_DEFAULT_SKIP = 0.92
_DEFAULT_MERGE = 0.80


class AIMemory:
    def __init__(self, bot):
        self.bot = bot

    def _thresholds(self) -> tuple[float, float]:
        mem_cfg = self.bot.config.get("memory", {}) if hasattr(self.bot, "config") else {}
        skip = float(mem_cfg.get("dedup_skip_threshold", _DEFAULT_SKIP))
        merge = float(mem_cfg.get("dedup_merge_threshold", _DEFAULT_MERGE))
        return skip, merge

    async def save(self, text: str, metadata: dict | None = None) -> None:
        """AI自身の記憶を保存（Ollama必須）。意味的重複は dedup で処理。"""
        if not self.bot.llm_router.ollama_available:
            log.info("Skipping ai_memory save: Ollama unavailable")
            return

        skip, merge = self._thresholds()
        status = self.bot.chroma.add_with_dedup(
            COLLECTION, text, metadata,
            skip_threshold=skip, merge_threshold=merge,
        )
        log.info("Saved ai_memory (%s): %.60s", status, text)

    async def extract_and_save(self, conversation: str) -> None:
        """会話からAI自身の体験・気づき・反応・主観的発見を抽出して保存（Ollama専用）。"""
        if not self.bot.llm_router.ollama_available:
            return

        prompt = (
            "以下の会話から、あなた(assistant)自身の体験・気づき・学び、そして感じた印象を"
            "一人称視点で抽出してください。\n"
            "【抽出対象】\n"
            "1. あなたが感じたこと・学んだこと・次に活かせること\n"
            "2. ユーザーからの反応（ツッコまれた／褒められた／嫌がられた／喜ばれた等）への印象\n"
            "3. 次回同じパターンで気をつけたいこと（言わない方がよかった・もっと突っ込んでよかった等）\n"
            "4. いにわに関するあなた自身の主観的な発見（好き嫌いの傾向への感情、違和感、驚きなど）\n"
            "【ルール】\n"
            "- assistantの立場からの一人称視点で書くこと\n"
            "- 事実の箇条書きにしつつ、感じたこと(印象)も一緒に書くこと\n"
            "- 形式は `- 事実: ... / 印象: ...` のように複数行で構わない\n"
            "- 推測・解釈・一般論は書かず、この会話から実際に得られた気づきだけを書くこと\n"
            "- 記憶すべきものがなければ「なし」とだけ答えること\n"
            "- 必ず日本語で出力すること\n\n"
            f"{conversation}"
        )
        try:
            result = await self.bot.llm_router.generate(prompt, purpose="memory_extraction", ollama_only=True)
            cleaned = result.strip()
            if not cleaned or ("なし" in cleaned and len(cleaned) < 20):
                return
            await self.save(cleaned)
        except Exception as e:
            log.warning("ai_memory extraction failed: %s", e)

    def recall(self, query: str, n_results: int = 5) -> list[dict]:
        return self.bot.chroma.search(COLLECTION, query, n_results)
