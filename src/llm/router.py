"""LLMルーター — Ollama優先・Geminiフォールバック。"""

import time

from src.errors import AllLLMsUnavailableError, GeminiError, OllamaUnavailableError
from src.flow_tracker import get_flow_tracker
from src.llm.gemini_client import GeminiClient
from src.llm.gpu_monitor import GpuMemoryMonitor
from src.llm.ollama_client import PURPOSE_PRIORITY, OllamaClient
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

        # Ollama URLs を優先度順に構築
        # windows_agents の priority が小さいほど高優先
        agents = config.get("windows_agents", [])
        agents_sorted = sorted(agents, key=lambda a: a.get("priority", 99))

        ollama_urls = []
        # 直接指定URL（ローカルOllama等）
        direct_url = config.get("llm", {}).get("ollama_url", "")
        if direct_url:
            ollama_urls.append(direct_url.rstrip("/"))

        # windows_agents から優先度順にURL構築
        for agent in agents_sorted:
            host = agent.get("host", "")
            if host:
                url = f"http://{host}:11434"
                if url not in ollama_urls:
                    ollama_urls.append(url)

        # URLが空の場合のみローカルフォールバック
        if not ollama_urls:
            ollama_urls.append("http://localhost:11434")

        # URL → エージェント名のマッピング（WebGUI表示用）
        self._url_to_name: dict[str, str] = {}
        for agent in agents:
            host = agent.get("host", "")
            if host:
                url = f"http://{host}:11434"
                self._url_to_name[url] = agent.get("name", agent.get("id", host))
        if direct_url:
            self._url_to_name[direct_url.rstrip("/")] = "サブPC"

        model = config.get("llm", {}).get("ollama_model", "gemma4:e2b")
        timeout = int(config.get("llm", {}).get("ollama_timeout", 150))

        # GPUメモリ監視: 他プロセスがGPUを占有中のインスタンスをOllamaから除外
        metrics_url = config.get("metrics", {}).get("victoria_metrics_url", "")
        gpu_threshold = int(config.get("llm", {}).get("gpu_memory_skip_bytes", 0))
        url_to_instance: dict[str, str] = {}
        for agent in agents:
            host = agent.get("host", "")
            inst = agent.get("metrics_instance", "")
            if host and inst:
                url_to_instance[f"http://{host}:11434"] = inst
        self.gpu_monitor = GpuMemoryMonitor(metrics_url, url_to_instance, gpu_threshold)

        self.ollama = OllamaClient(
            model=model, urls=ollama_urls, timeout=timeout,
            gpu_monitor=self.gpu_monitor,
        )
        self.gemini = GeminiClient()
        self.ollama_available = False
        self._database = None  # bot.pyから設定される
        # Ollama 500 等のゾンビ状態を検出したら一定秒クールダウン。/api/tags 誤判定を避ける
        self._ollama_cooldown_sec = int(config.get("llm", {}).get("ollama_cooldown_sec", 60))
        self._ollama_cooldown_until: float = 0.0

    def set_database(self, database) -> None:
        self._database = database

    def get_url_name(self, url: str) -> str:
        """URL からエージェント名を返す。"""
        return self._url_to_name.get(url, url)

    async def _log_llm_call(
        self, provider: str, model: str, purpose: str,
        prompt_len: int, response_len: int, duration_ms: int,
        success: bool = True, error: str | None = None,
        prompt_text: str | None = None, system_text: str | None = None,
        response_text: str | None = None,
        tokens_per_sec: float | None = None,
        eval_count: int | None = None,
        prompt_eval_count: int | None = None,
        instance: str | None = None,
    ) -> None:
        if self._database:
            try:
                await self._database.log_llm_call(
                    provider, model, purpose,
                    prompt_len, response_len, duration_ms,
                    success, error,
                    prompt_text=prompt_text, system_text=system_text,
                    response_text=response_text,
                    tokens_per_sec=tokens_per_sec,
                    eval_count=eval_count,
                    prompt_eval_count=prompt_eval_count,
                    instance=instance,
                )
            except Exception as e:
                log.debug("Failed to log LLM call: %s", e)

    async def check_ollama(self, force: bool = False) -> bool:
        # クールダウン中は /api/tags を叩かずに False を返す（ゾンビ状態で True に戻るのを防ぐ）
        if not force and time.monotonic() < self._ollama_cooldown_until:
            self.ollama_available = False
            return False
        self.ollama_available = await self.ollama.check_availability()
        return self.ollama_available

    def _is_gemini_allowed(self, purpose: str) -> bool:
        toggle_key = _PURPOSE_TO_TOGGLE.get(purpose)
        if not toggle_key:
            return False
        if not self._gemini_config.get(toggle_key, False):
            return False
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
        gemini_allowed: bool = True,
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

        # Ollama未接続なら再チェック（復帰検出）
        if not self.ollama_available:
            await self.check_ollama()

        # purpose → 優先度
        priority = PURPOSE_PRIORITY.get(purpose, 2)

        # Ollama を優先
        if self.ollama_available:
            _model = ollama_model or self.ollama.model
            t0 = time.monotonic()
            try:
                await ft.emit("OLLAMA", "active", {"model": _model}, flow_id)
                result, metrics = await self.ollama.generate(
                    prompt, system=system, model=ollama_model,
                    priority=priority, purpose=purpose,
                )
                instance_url = metrics.get("instance")
                instance_name = self.get_url_name(instance_url) if instance_url else None
                dur = int((time.monotonic() - t0) * 1000)
                await self._log_llm_call(
                    "ollama", _model, purpose, len(prompt), len(result), dur,
                    prompt_text=prompt, system_text=system, response_text=result,
                    tokens_per_sec=metrics.get("tokens_per_sec"),
                    eval_count=metrics.get("eval_count"),
                    prompt_eval_count=metrics.get("prompt_eval_count"),
                    instance=instance_name,
                )
                await ft.emit("OLLAMA", "done", {"model": _model, "instance": instance_name}, flow_id)
                await ft.emit("LLM_SELECT", "done", {"selected": "ollama"}, flow_id)
                return result
            except OllamaUnavailableError as e:
                dur = int((time.monotonic() - t0) * 1000)
                await self._log_llm_call(
                    "ollama", _model, purpose, len(prompt), 0, dur, False, str(e),
                    prompt_text=prompt, system_text=system,
                )
                self.ollama_available = False
                self._ollama_cooldown_until = time.monotonic() + self._ollama_cooldown_sec
                log.warning(
                    "Ollama became unavailable, cooldown %ds, checking Gemini fallback",
                    self._ollama_cooldown_sec,
                )
                await ft.emit("OLLAMA", "error", {"reason": "unavailable"}, flow_id)

        if ollama_only:
            await ft.emit("LLM_SELECT", "error", {"reason": "ollama_only_but_unavailable"}, flow_id)
            raise AllLLMsUnavailableError("Ollama required but unavailable")

        # Gemini フォールバック
        if gemini_allowed and self._is_gemini_allowed(purpose):
            _gmodel = gemini_model or self.gemini.model or "gemini"
            t0 = time.monotonic()
            try:
                await ft.emit("GEMINI", "active", {"purpose": purpose}, flow_id)
                result = await self.gemini.generate(prompt, system=system, model=gemini_model)
                dur = int((time.monotonic() - t0) * 1000)
                await self._log_llm_call(
                    "gemini", _gmodel, purpose, len(prompt), len(result), dur,
                    prompt_text=prompt, system_text=system, response_text=result,
                )
                await ft.emit("GEMINI", "done", {"purpose": purpose}, flow_id)
                await ft.emit("LLM_SELECT", "done", {"selected": "gemini"}, flow_id)
                return result
            except GeminiError as e:
                dur = int((time.monotonic() - t0) * 1000)
                await self._log_llm_call(
                    "gemini", _gmodel, purpose, len(prompt), 0, dur, False, str(e),
                    prompt_text=prompt, system_text=system,
                )
                log.error("Gemini also failed: %s", e)
                await ft.emit("GEMINI", "error", {"error": str(e)}, flow_id)

        await ft.emit("ECO", "done", {"reason": "all_llms_unavailable"}, flow_id)
        await ft.emit("LLM_SELECT", "error", {"reason": "all_unavailable"}, flow_id)
        raise AllLLMsUnavailableError(f"No LLM available for purpose={purpose}")
