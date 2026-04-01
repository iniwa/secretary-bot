"""Google Gemini APIクライアント。"""

import os

from src.errors import GeminiError
from src.logger import get_logger

log = get_logger(__name__)


class GeminiClient:
    _DEFAULT_MODEL = "gemini-2.5-flash-preview-04-17"

    def __init__(self):
        self._client = None
        self._total_tokens = 0

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as e:
                raise GeminiError("google-genai パッケージがインストールされていません") from e
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                raise GeminiError("GEMINI_API_KEY not set")
            self._client = genai.Client(api_key=api_key)
        return self._client

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    def reset_token_count(self) -> None:
        self._total_tokens = 0

    async def generate(self, prompt: str, system: str | None = None, model: str | None = None) -> str:
        try:
            from google.genai import types
            client = self._get_client()
            model_name = model or self._DEFAULT_MODEL

            config = None
            if system:
                config = types.GenerateContentConfig(system_instruction=system)

            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            text = response.text or ""

            if hasattr(response, "usage_metadata") and response.usage_metadata:
                self._total_tokens += getattr(response.usage_metadata, "total_token_count", 0)

            return text
        except GeminiError:
            raise
        except Exception as e:
            raise GeminiError(f"Gemini generation failed: {e}") from e
