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
    def __init__(self, model: str = "gemma4", urls: list[str] | None = None, timeout: int = 300):
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

    async def generate(self, prompt: str, system: str | None = None, model: str | None = None) -> tuple[str, dict]:
        """生成結果のテキストとOllamaメトリクスを返す。

        /api/chat エンドポイントを使用（Ollama v0.20+ 対応）。

        Returns:
            (text, metrics) — metricsは eval_count, eval_duration, prompt_eval_count, prompt_eval_duration, tokens_per_sec を含む
        """
        if not self._available_url:
            raise OllamaUnavailableError("No Ollama instance available")

        use_model = model or self.model
        options: dict = {}
        # qwen系モデルの場合のみ思考モード無効化・ChatMLストップトークンを設定
        if "qwen" in use_model.lower():
            options["think"] = False
            options["stop"] = ["<|endoftext|>", "<|im_start|>", "<|im_end|>"]

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": use_model,
            "messages": messages,
            "stream": False,
            "options": options,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self._available_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data.get("message", {}).get("content", "")
                # <think>...</think> タグが残っている場合は除去（qwen系）
                if "<think>" in text:
                    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

                # Ollamaのパフォーマンスメトリクスを抽出
                eval_count = data.get("eval_count", 0)
                eval_duration = data.get("eval_duration", 0)
                prompt_eval_count = data.get("prompt_eval_count", 0)
                prompt_eval_duration = data.get("prompt_eval_duration", 0)
                tokens_per_sec = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0
                metrics = {
                    "eval_count": eval_count,
                    "eval_duration": eval_duration,
                    "prompt_eval_count": prompt_eval_count,
                    "prompt_eval_duration": prompt_eval_duration,
                    "tokens_per_sec": round(tokens_per_sec, 2),
                }

                return self._clean_response(text), metrics
        except Exception as e:
            self._available_url = None
            raise OllamaUnavailableError(f"Ollama generation failed: {e}") from e
