"""Ollama APIクライアント（マルチインスタンス対応）。"""

import asyncio
import re

import httpx

from src.errors import OllamaUnavailableError
from src.logger import get_logger

log = get_logger(__name__)


class OllamaClient:
    def __init__(self, model: str = "gemma4", urls: list[str] | None = None, timeout: int = 300):
        self.model = model
        self.urls = urls or []
        self.timeout = timeout
        # マルチインスタンス管理
        self._available_urls: list[str] = []
        self._active_count: dict[str, int] = {}

    async def check_availability(self) -> bool:
        """全URLを並列チェックし、利用可能なインスタンスを更新する。"""
        results = await asyncio.gather(
            *[self._check_one(url) for url in self.urls],
            return_exceptions=True,
        )
        self._available_urls = []
        for url, result in zip(self.urls, results):
            if result is True:
                self._available_urls.append(url)
                if url not in self._active_count:
                    self._active_count[url] = 0

        # 到達不可になったインスタンスのカウントをクリーンアップ
        for url in list(self._active_count):
            if url not in self._available_urls:
                del self._active_count[url]

        if self._available_urls:
            log.info(
                "Ollama available at %d instance(s): %s",
                len(self._available_urls), self._available_urls,
            )
        else:
            log.info("Ollama unavailable")
        return len(self._available_urls) > 0

    async def _check_one(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    @property
    def is_available(self) -> bool:
        return len(self._available_urls) > 0

    @property
    def available_count(self) -> int:
        return len(self._available_urls)

    def _acquire_instance(self, exclude: list[str] | None = None) -> str:
        """Least-connections で空いているインスタンスを選択する。"""
        exclude = exclude or []
        candidates = [u for u in self._available_urls if u not in exclude]
        if not candidates:
            raise OllamaUnavailableError("No Ollama instance available")
        candidates.sort(key=lambda u: self._active_count.get(u, 0))
        url = candidates[0]
        self._active_count[url] = self._active_count.get(url, 0) + 1
        return url

    def _release_instance(self, url: str) -> None:
        self._active_count[url] = max(0, self._active_count.get(url, 0) - 1)

    def _mark_unavailable(self, url: str) -> None:
        if url in self._available_urls:
            self._available_urls.remove(url)
        self._active_count.pop(url, None)

    async def list_models(self) -> list[str]:
        """利用可能な全インスタンスからモデル名一覧を返す（重複排除）。"""
        if not self._available_urls:
            return []
        models = set()
        for url in self._available_urls:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{url}/api/tags")
                    resp.raise_for_status()
                    data = resp.json()
                    for m in data.get("models", []):
                        models.add(m["name"])
            except Exception:
                pass
        return sorted(models)

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

        マルチインスタンス対応: least-connections で空いているインスタンスに分配する。
        1台が失敗した場合、別のインスタンスでリトライする。

        Returns:
            (text, metrics) -- metricsは eval_count, eval_duration, prompt_eval_count, prompt_eval_duration, tokens_per_sec を含む
        """
        if not self._available_urls:
            raise OllamaUnavailableError("No Ollama instance available")

        url = self._acquire_instance()
        try:
            result = await self._do_generate(url, prompt, system, model)
            return result
        except OllamaUnavailableError:
            self._release_instance(url)
            self._mark_unavailable(url)
            # 他のインスタンスでリトライ
            if self._available_urls:
                url2 = self._acquire_instance()
                try:
                    return await self._do_generate(url2, prompt, system, model)
                finally:
                    self._release_instance(url2)
            raise
        finally:
            # 成功時のみ解放（エラー時は except 内で解放済み・_mark_unavailable で削除済み）
            if url in self._active_count:
                self._release_instance(url)

    async def _do_generate(self, url: str, prompt: str, system: str | None, model: str | None) -> tuple[str, dict]:
        """指定URLのOllamaインスタンスで生成を実行する。"""
        use_model = model or self.model
        options: dict = {}
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
                resp = await client.post(f"{url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data.get("message", {}).get("content", "")
                if "<think>" in text:
                    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

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
                    "instance": url,
                }

                log.debug("Ollama generate on %s: %.1f tok/s", url, tokens_per_sec)
                return self._clean_response(text), metrics
        except Exception as e:
            raise OllamaUnavailableError(f"Ollama generation failed ({url}): {e}") from e
