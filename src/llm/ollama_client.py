"""Ollama APIクライアント（マルチインスタンス・優先度キュー対応）。"""

import asyncio
import dataclasses
import itertools
import re
import time

import httpx

from src.errors import OllamaUnavailableError
from src.logger import get_logger

log = get_logger(__name__)

# リクエスト優先度（数値が小さいほど高優先）
PRIORITY_HIGH = 0      # ユーザー会話・ルーティング
PRIORITY_MEDIUM = 1    # InnerMind・STT
PRIORITY_LOW = 2       # RSS要約・記憶抽出

# purpose → priority マッピング
PURPOSE_PRIORITY: dict[str, int] = {
    "conversation": PRIORITY_HIGH,
    "unit_routing": PRIORITY_HIGH,
    "inner_mind": PRIORITY_MEDIUM,
    "stt_summary": PRIORITY_MEDIUM,
    "rss_summary": PRIORITY_LOW,
    "memory_extraction": PRIORITY_LOW,
}


@dataclasses.dataclass(order=True)
class _Waiter:
    """優先度キューのエントリ。priority→seq の順でソート。"""
    priority: int
    seq: int
    event: asyncio.Event = dataclasses.field(compare=False)
    result: list = dataclasses.field(compare=False, default_factory=list)
    exclude: frozenset = dataclasses.field(compare=False, default_factory=frozenset)
    model: str | None = dataclasses.field(compare=False, default=None)


class OllamaClient:
    def __init__(self, model: str = "gemma4", urls: list[str] | None = None, timeout: int = 300):
        self.model = model
        self.urls = urls or []
        self.timeout = timeout
        # マルチインスタンス管理
        self._available_urls: list[str] = []  # 優先度順（先頭ほど高優先）
        self._active_count: dict[str, int] = {}
        # モデルキャッシュ（インスタンス別）
        self._instance_models: dict[str, list[str]] = {}
        # 優先度キュー
        self._waiters: list[_Waiter] = []  # heapq
        self._seq = itertools.count()
        # アクティブリクエスト追跡（WebGUI表示用）
        self._active_requests: dict[str, dict] = {}  # url → {purpose, started_at}

    # --- ヘルスチェック ---

    async def check_availability(self) -> bool:
        """全URLを並列チェックし、利用可能なインスタンスとモデル一覧を更新する。"""
        results = await asyncio.gather(
            *[self._check_one(url) for url in self.urls],
            return_exceptions=True,
        )
        self._available_urls = []
        for url, result in zip(self.urls, results):
            if isinstance(result, dict):
                self._available_urls.append(url)
                self._instance_models[url] = result["models"]
                if url not in self._active_count:
                    self._active_count[url] = 0

        # 到達不可になったインスタンスをクリーンアップ
        for url in list(self._active_count):
            if url not in self._available_urls:
                del self._active_count[url]
                self._instance_models.pop(url, None)
                self._active_requests.pop(url, None)

        if self._available_urls:
            log.info(
                "Ollama available at %d instance(s): %s",
                len(self._available_urls), self._available_urls,
            )
        else:
            log.info("Ollama unavailable")
        return len(self._available_urls) > 0

    async def _check_one(self, url: str) -> dict | None:
        """1インスタンスをチェックし、モデル一覧を含む結果を返す。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{url}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    return {"models": models}
        except Exception:
            pass
        return None

    @property
    def is_available(self) -> bool:
        return len(self._available_urls) > 0

    @property
    def available_count(self) -> int:
        return len(self._available_urls)

    # --- モデルマッチング ---

    def _has_model(self, url: str, model: str) -> bool:
        """インスタンスが指定モデルを持っているか（プレフィックスマッチ対応）。"""
        models = self._instance_models.get(url, [])
        if not models:
            return True  # モデル一覧未取得ならスキップせず許可
        for m in models:
            if m == model or m.startswith(f"{model}:") or model.startswith(f"{m.split(':')[0]}"):
                return True
        return False

    # --- インスタンス取得・解放（優先度キュー） ---

    def _try_acquire(self, exclude: frozenset = frozenset(), model: str | None = None) -> str | None:
        """空きインスタンスを取得する（ノンブロッキング）。_available_urlsの順序で優先。"""
        for url in self._available_urls:
            if url in exclude:
                continue
            if model and not self._has_model(url, model):
                continue
            if self._active_count.get(url, 0) == 0:
                self._active_count[url] = 1
                return url
        return None

    async def _acquire_instance(
        self, priority: int = PRIORITY_LOW, exclude: frozenset = frozenset(), model: str | None = None,
    ) -> str:
        """優先度キュー付きでインスタンスを取得する。全インスタンスがビジーなら待機。"""
        # 即座に取得を試みる
        url = self._try_acquire(exclude, model)
        if url:
            return url

        # 利用可能なインスタンスが存在しない場合は即エラー
        candidates = [u for u in self._available_urls if u not in exclude]
        if model:
            candidates = [u for u in candidates if self._has_model(u, model)]
        if not candidates:
            raise OllamaUnavailableError("No Ollama instance available")

        # 全インスタンスがビジー → 優先度キューで待機
        event = asyncio.Event()
        waiter = _Waiter(
            priority=priority, seq=next(self._seq),
            event=event, exclude=exclude, model=model,
        )
        import heapq
        heapq.heappush(self._waiters, waiter)
        log.debug("Queued request (priority=%d, queue_size=%d)", priority, len(self._waiters))
        await event.wait()

        if waiter.result:
            return waiter.result[0]
        raise OllamaUnavailableError("No Ollama instance available")

    def _release_instance(self, url: str) -> None:
        """インスタンスを解放し、最高優先度の待機リクエストをディスパッチする。"""
        self._active_count[url] = 0
        self._active_requests.pop(url, None)
        self._dispatch_next()

    def _dispatch_next(self) -> None:
        """空きインスタンスがあれば、最高優先度の待機リクエストに割り当てる。"""
        import heapq
        retry = []
        dispatched = False
        while self._waiters and not dispatched:
            waiter = heapq.heappop(self._waiters)
            url = self._try_acquire(waiter.exclude, waiter.model)
            if url:
                waiter.result.append(url)
                waiter.event.set()
                dispatched = True
            else:
                retry.append(waiter)
        for w in retry:
            heapq.heappush(self._waiters, w)

    def _mark_unavailable(self, url: str) -> None:
        if url in self._available_urls:
            self._available_urls.remove(url)
        self._active_count.pop(url, None)
        self._instance_models.pop(url, None)
        self._active_requests.pop(url, None)

    # --- モデル一覧 ---

    async def list_models(self) -> list[str]:
        """キャッシュ済みモデル一覧を返す（重複排除）。"""
        models = set()
        for url in self._available_urls:
            for m in self._instance_models.get(url, []):
                models.add(m)
        if not models and self._available_urls:
            # キャッシュが空なら再取得
            await self.check_availability()
            for url in self._available_urls:
                for m in self._instance_models.get(url, []):
                    models.add(m)
        return sorted(models)

    # --- レスポンスクリーニング ---

    @staticmethod
    def _clean_response(text: str) -> str:
        """特殊トークン除去・連続重複除去。"""
        text = re.sub(r"<\|[a-z_]+\|>", "", text).strip()
        paragraphs = text.split("\n\n")
        deduped = []
        for p in paragraphs:
            if not deduped or p.strip() != deduped[-1].strip():
                deduped.append(p)
        text = "\n\n".join(deduped)
        lines = text.split("\n")
        result = []
        for line in lines:
            if not result or line.strip() != result[-1].strip():
                result.append(line)
        return "\n".join(result).strip()

    # --- 生成 ---

    async def generate(
        self, prompt: str, system: str | None = None,
        model: str | None = None, priority: int = PRIORITY_LOW,
        purpose: str = "",
    ) -> tuple[str, dict]:
        """生成結果のテキストとOllamaメトリクスを返す。

        優先度キュー対応: 全インスタンスがビジーの場合、高優先度リクエストが先に処理される。
        1台が失敗した場合、別のインスタンスでリトライする。
        """
        if not self._available_urls:
            raise OllamaUnavailableError("No Ollama instance available")

        use_model = model or self.model
        url = await self._acquire_instance(priority, model=use_model)
        self._active_requests[url] = {"purpose": purpose, "started_at": time.time()}
        try:
            result = await self._do_generate(url, prompt, system, model)
            return result
        except OllamaUnavailableError:
            self._release_instance(url)
            self._mark_unavailable(url)
            # 他のインスタンスでリトライ
            if self._available_urls:
                url2 = await self._acquire_instance(priority, exclude=frozenset({url}), model=use_model)
                self._active_requests[url2] = {"purpose": purpose, "started_at": time.time()}
                try:
                    return await self._do_generate(url2, prompt, system, model)
                finally:
                    self._release_instance(url2)
            raise
        finally:
            # 成功時のみ解放（エラー時は except 内で解放済み）
            if self._active_count.get(url, 0) > 0:
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

    # --- ステータス（WebGUI用） ---

    def get_status(self) -> dict:
        """Ollamaインスタンスの現在の状態を返す。"""
        instances = []
        for url in self.urls:
            available = url in self._available_urls
            active = self._active_count.get(url, 0)
            active_req = self._active_requests.get(url)
            instances.append({
                "url": url,
                "available": available,
                "active": active,
                "models": self._instance_models.get(url, []),
                "current_request": {
                    "purpose": active_req["purpose"],
                    "elapsed_sec": round(time.time() - active_req["started_at"], 1),
                } if active_req and active > 0 else None,
            })
        return {
            "instances": instances,
            "queue_size": len(self._waiters),
            "queue_detail": [
                {"priority": w.priority, "purpose": ""}
                for w in sorted(self._waiters)
            ],
            "model": self.model,
        }
