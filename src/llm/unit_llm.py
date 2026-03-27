"""UnitLLM — ユニット・SkillRouter共通のLLMアクセスファサード。"""

import json
import re

from src.errors import LLMJsonParseError
from src.logger import get_logger

log = get_logger(__name__)


def _parse_json(raw: str) -> dict:
    """LLM出力からJSONを抽出してパースする。"""
    text = raw.strip()
    # マークダウンコードフェンス除去
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 先頭/末尾の非JSONテキストを除去（最初の { から最後の } まで）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


class UnitLLM:
    """LLMRouter のファサード。ユニットごとのモデル設定・共通メソッドを提供。"""

    def __init__(
        self,
        llm_router,
        *,
        purpose: str = "conversation",
        ollama_model: str | None = None,
        gemini_model: str | None = None,
        ollama_only: bool = False,
    ):
        self._router = llm_router
        self._purpose = purpose
        self._ollama_model = ollama_model
        self._gemini_model = gemini_model
        self._ollama_only = ollama_only

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        """自由文生成。LLMRouterにインスタンスの設定を渡す。"""
        return await self._router.generate(
            prompt,
            system=system,
            purpose=self._purpose,
            ollama_only=self._ollama_only,
            ollama_model=self._ollama_model,
            gemini_model=self._gemini_model,
        )

    async def extract_json(
        self, prompt: str, *, system: str | None = None, max_retries: int = 2,
    ) -> dict:
        """LLM出力をJSONとしてパース。失敗時はリトライする。"""
        last_error = None
        for attempt in range(max_retries + 1):
            raw = await self.generate(prompt, system=system)
            try:
                return _parse_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                log.warning(
                    "JSON parse attempt %d/%d failed: %s",
                    attempt + 1, max_retries + 1, e,
                )
                if attempt < max_retries:
                    prompt = (
                        f"あなたの前回の出力はJSON解析に失敗しました。エラー: {e}\n"
                        f"前回の出力:\n{raw}\n\n"
                        f"正しいJSONのみを返してください。JSON以外は返さないでください。"
                    )
        raise LLMJsonParseError(
            f"JSON parse failed after {max_retries + 1} attempts: {last_error}"
        )

    @classmethod
    def from_config(
        cls,
        llm_router,
        unit_config: dict,
        global_config: dict,
        *,
        purpose: str = "conversation",
    ) -> "UnitLLM":
        """config.yaml のユニット設定 + グローバル設定からインスタンス生成。"""
        unit_llm_cfg = unit_config.get("llm", {})
        global_llm_cfg = global_config.get("llm", {})
        character_cfg = global_config.get("character", {})

        ollama_model = (
            unit_llm_cfg.get("ollama_model")
            or global_llm_cfg.get("ollama_model")
        )
        gemini_model = unit_llm_cfg.get("gemini_model")
        ollama_only = unit_llm_cfg.get(
            "ollama_only", character_cfg.get("ollama_only", False),
        )

        return cls(
            llm_router,
            purpose=purpose,
            ollama_model=ollama_model,
            gemini_model=gemini_model,
            ollama_only=ollama_only,
        )
