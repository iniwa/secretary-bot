"""Google Gemini APIクライアント。"""

import os

from src.errors import GeminiError
from src.logger import get_logger

log = get_logger(__name__)


class GeminiClient:
    _DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(self):
        self._models: dict[str, object] = {}
        self._total_tokens = 0
        self._configured = False

    def _get_model(self, model_name: str | None = None):
        name = model_name or self._DEFAULT_MODEL
        if name not in self._models:
            if not self._configured:
                import google.generativeai as genai
                api_key = os.environ.get("GEMINI_API_KEY", "")
                if not api_key:
                    raise GeminiError("GEMINI_API_KEY not set")
                genai.configure(api_key=api_key)
                self._configured = True
            import google.generativeai as genai
            self._models[name] = genai.GenerativeModel(name)
        return self._models[name]

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    def reset_token_count(self) -> None:
        self._total_tokens = 0

    async def generate(self, prompt: str, system: str | None = None, model: str | None = None) -> str:
        m = self._get_model(model)
        try:
            contents = []
            if system:
                contents.append({"role": "user", "parts": [system]})
                contents.append({"role": "model", "parts": ["了解しました。"]})
            contents.append({"role": "user", "parts": [prompt]})

            response = await m.generate_content_async(contents)
            text = response.text or ""

            if hasattr(response, "usage_metadata"):
                self._total_tokens += getattr(response.usage_metadata, "total_token_count", 0)

            return text
        except Exception as e:
            raise GeminiError(f"Gemini generation failed: {e}") from e
