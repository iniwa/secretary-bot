"""LLMルーター — Ollama優先・Geminiフォールバック。"""

from src.errors import AllLLMsUnavailableError, GeminiError, OllamaUnavailableError
from src.llm.gemini_client import GeminiClient
from src.llm.ollama_client import OllamaClient
from src.logger import get_logger

log = get_logger(__name__)

# purpose → gemini config key
_PURPOSE_TO_TOGGLE = {
    "conversation": "conversation",
    "skill_routing": "skill_routing",
    "memory_extraction": "memory_extraction",
}


class LLMRouter:
    def __init__(self, config: dict):
        self._config = config
        self._gemini_config = config.get("gemini", {})

        # Ollama URLs を windows_agents から構築
        ollama_urls = []
        for agent in config.get("windows_agents", []):
            host = agent.get("host", "")
            if host:
                ollama_urls.append(f"http://{host}:11434")

        model = config.get("llm", {}).get("ollama_model", "qwen3")
        self.ollama = OllamaClient(model=model, urls=ollama_urls)
        self.gemini = GeminiClient()
        self.ollama_available = False

    async def check_ollama(self) -> bool:
        self.ollama_available = await self.ollama.check_availability()
        return self.ollama_available

    def _is_gemini_allowed(self, purpose: str) -> bool:
        toggle_key = _PURPOSE_TO_TOGGLE.get(purpose)
        if not toggle_key:
            return False
        if not self._gemini_config.get(toggle_key, False):
            return False
        # 月間トークン上限チェック
        limit = self._gemini_config.get("monthly_token_limit", 0)
        if limit > 0 and self.gemini.total_tokens_used >= limit:
            log.warning("Gemini monthly token limit reached")
            return False
        return True

    async def generate(
        self, prompt: str, *,
        system: str | None = None,
        purpose: str = "conversation",
        ollama_only: bool = False,
    ) -> str:
        dry_run = self._config.get("debug", {}).get("dry_run", False)
        if dry_run:
            return f"[dry_run] purpose={purpose}"

        # Ollama を優先
        if self.ollama_available:
            try:
                return await self.ollama.generate(prompt, system=system)
            except OllamaUnavailableError:
                self.ollama_available = False
                log.warning("Ollama became unavailable, checking Gemini fallback")

        if ollama_only:
            raise AllLLMsUnavailableError("Ollama required but unavailable")

        # Gemini フォールバック
        if self._is_gemini_allowed(purpose):
            try:
                return await self.gemini.generate(prompt, system=system)
            except GeminiError as e:
                log.error("Gemini also failed: %s", e)

        raise AllLLMsUnavailableError(f"No LLM available for purpose={purpose}")
