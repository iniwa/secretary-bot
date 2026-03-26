"""Google Gemini APIクライアント。"""

import os

from src.errors import GeminiError
from src.logger import get_logger

log = get_logger(__name__)


class GeminiClient:
    def __init__(self):
        self._model = None
        self._total_tokens = 0

    def _ensure_model(self):
        if self._model is not None:
            return
        import google.generativeai as genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise GeminiError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel("gemini-2.0-flash")

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    def reset_token_count(self) -> None:
        self._total_tokens = 0

    async def generate(self, prompt: str, system: str | None = None) -> str:
        self._ensure_model()
        try:
            contents = []
            if system:
                contents.append({"role": "user", "parts": [system]})
                contents.append({"role": "model", "parts": ["了解しました。"]})
            contents.append({"role": "user", "parts": [prompt]})

            response = await self._model.generate_content_async(contents)
            text = response.text or ""

            if hasattr(response, "usage_metadata"):
                self._total_tokens += getattr(response.usage_metadata, "total_token_count", 0)

            return text
        except Exception as e:
            raise GeminiError(f"Gemini generation failed: {e}") from e
