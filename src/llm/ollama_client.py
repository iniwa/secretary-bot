"""Ollama APIクライアント。"""

import re

import httpx

from src.errors import OllamaUnavailableError
from src.logger import get_logger

log = get_logger(__name__)

DEFAULT_OLLAMA_URLS = [
    # config.yaml の windows_agents から動的に構築される
]


class OllamaClient:
    def __init__(self, model: str = "qwen3", urls: list[str] | None = None, timeout: int = 300):
        self.model = model
        self.urls = urls or []
        self.timeout = timeout
        self._available_url: str | None = None

    async def check_availability(self) -> bool:
        for url in self.urls:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{url}/api/tags")
                    if resp.status_code == 200:
                        self._available_url = url
                        log.info("Ollama available at %s", url)
                        return True
            except Exception:
                continue
        self._available_url = None
        log.info("Ollama unavailable")
        return False

    @property
    def is_available(self) -> bool:
        return self._available_url is not None

    async def list_models(self) -> list[str]:
        """利用可能なOllamaモデル名一覧を返す。"""
        if not self._available_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._available_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    @staticmethod
    def _clean_response(text: str) -> str:
        """特殊トークン除去・連続重複除去。"""
        # ChatML特殊トークンを除去
        text = re.sub(r"<\|[a-z_]+\|>", "", text).strip()
        # 連続する同一段落の重複除去
        paragraphs = text.split("\n\n")
        deduped = []
        for p in paragraphs:
            if not deduped or p.strip() != deduped[-1].strip():
                deduped.append(p)
        text = "\n\n".join(deduped)
        # 連続する同一行の重複除去
        lines = text.split("\n")
        result = []
        for line in lines:
            if not result or line.strip() != result[-1].strip():
                result.append(line)
        return "\n".join(result).strip()

    async def generate(self, prompt: str, system: str | None = None, model: str | None = None) -> str:
        if not self._available_url:
            raise OllamaUnavailableError("No Ollama instance available")

        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "think": False,  # qwen3思考モード無効化
                "stop": ["<|endoftext|>", "<|im_start|>", "<|im_end|>"],
            },
        }
        if system:
            payload["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self._available_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data.get("response", "")
                # <think>...</think> タグが残っている場合は除去
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                return self._clean_response(text)
        except Exception as e:
            self._available_url = None
            raise OllamaUnavailableError(f"Ollama generation failed: {e}") from e
