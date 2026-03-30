"""LLMルーター — Ollama優先・Geminiフォールバック。"""

from src.errors import AllLLMsUnavailableError, GeminiError, OllamaUnavailableError
from src.flow_tracker import get_flow_tracker
from src.llm.gemini_client import GeminiClient
from src.llm.ollama_client import OllamaClient
from src.logger import get_logger

log = get_logger(__name__)

# purpose → gemini config key
_PURPOSE_TO_TOGGLE = {
    "conversation": "conversation",
    "unit_routing": "unit_routing",
    "memory_extraction": "memory_extraction",
}


class LLMRouter:
    def __init__(self, config: dict):
        self._config = config
        self._gemini_config = config.get("gemini", {})

        # Ollama URLs を構築
        # 1. config の llm.ollama_url（ローカル含む直接指定）
        # 2. windows_agents のホストから自動構築
        ollama_urls = []
        direct_url = config.get("llm", {}).get("ollama_url", "")
        if direct_url:
            ollama_urls.append(direct_url.rstrip("/"))
        else:
            # デフォルトでローカルOllamaを追加
            ollama_urls.append("http://localhost:11434")
        for agent in config.get("windows_agents", []):
            host = agent.get("host", "")
            if host:
                url = f"http://{host}:11434"
                if url not in ollama_urls:
                    ollama_urls.append(url)

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
        ollama_model: str | None = None,
        gemini_model: str | None = None,
        flow_id: str | None = None,
    ) -> str:
        ft = get_flow_tracker()
        debug_cfg = self._config.get("debug", {})
        dry_run = debug_cfg.get("dry_run", False)
        if dry_run:
            responses = debug_cfg.get("dry_run_responses", {})
            if purpose in responses:
                return responses[purpose]
            return f"[dry_run] purpose={purpose}"

        await ft.emit("LLM_SELECT", "active", {"purpose": purpose}, flow_id)

        # Ollama を優先
        if self.ollama_available:
            try:
                await ft.emit("OLLAMA", "active", {"model": ollama_model or self.ollama.model}, flow_id)
                result = await self.ollama.generate(prompt, system=system, model=ollama_model)
                await ft.emit("OLLAMA", "done", {"model": ollama_model or self.ollama.model}, flow_id)
                await ft.emit("LLM_SELECT", "done", {"selected": "ollama"}, flow_id)
                return result
            except OllamaUnavailableError:
                self.ollama_available = False
                log.warning("Ollama became unavailable, checking Gemini fallback")
                await ft.emit("OLLAMA", "error", {"reason": "unavailable"}, flow_id)

        if ollama_only:
            await ft.emit("LLM_SELECT", "error", {"reason": "ollama_only_but_unavailable"}, flow_id)
            raise AllLLMsUnavailableError("Ollama required but unavailable")

        # Gemini フォールバック
        if self._is_gemini_allowed(purpose):
            try:
                await ft.emit("GEMINI", "active", {"purpose": purpose}, flow_id)
                result = await self.gemini.generate(prompt, system=system, model=gemini_model)
                await ft.emit("GEMINI", "done", {"purpose": purpose}, flow_id)
                await ft.emit("LLM_SELECT", "done", {"selected": "gemini"}, flow_id)
                return result
            except GeminiError as e:
                log.error("Gemini also failed: %s", e)
                await ft.emit("GEMINI", "error", {"error": str(e)}, flow_id)

        await ft.emit("ECO", "done", {"reason": "all_llms_unavailable"}, flow_id)
        await ft.emit("LLM_SELECT", "error", {"reason": "all_unavailable"}, flow_id)
        raise AllLLMsUnavailableError(f"No LLM available for purpose={purpose}")
