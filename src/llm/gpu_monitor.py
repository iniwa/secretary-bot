"""GPUメモリ監視 — VictoriaMetrics経由でOllamaインスタンスのGPUメモリ使用量を取得。

閾値超のインスタンスは「他の処理が使用中」と判定し、Ollamaルーティングから除外する。
ハートビート等から定期的に update() を呼び、OllamaClient._try_acquire() は
同期的に is_busy() を参照する設計。
"""

import asyncio

import httpx

from src.logger import get_logger

log = get_logger(__name__)


class GpuMemoryMonitor:
    """Ollamaインスタンス別のGPUメモリ使用量を背景で監視。

    Parameters
    ----------
    metrics_url:
        VictoriaMetrics のベースURL（例: ``http://localhost:8428``）。
    url_to_instance:
        OllamaのURL（例: ``http://192.168.1.101:11434``）→
        VictoriaMetrics ラベル ``instance``（例: ``192.168.1.101:9182``）のマッピング。
    threshold_bytes:
        このバイト数を超えたら「ビジー」と判定する。
    """

    def __init__(
        self,
        metrics_url: str,
        url_to_instance: dict[str, str],
        threshold_bytes: int,
    ) -> None:
        self._metrics_url = metrics_url.rstrip("/") if metrics_url else ""
        self._url_to_instance = dict(url_to_instance)
        self._threshold = int(threshold_bytes)
        # url → 最後に観測した使用バイト数。値が無ければ不明扱い（ビジーと見なさない）
        self._used_bytes: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._metrics_url) and bool(self._url_to_instance) and self._threshold > 0

    def is_busy(self, url: str) -> bool:
        """同期的に判定。閾値超のみ True、未観測や閾値以下は False。"""
        if not self.enabled:
            return False
        used = self._used_bytes.get(url)
        if used is None:
            return False
        return used > self._threshold

    def snapshot(self) -> dict[str, int]:
        """現在の観測値を辞書で返す（WebGUI/デバッグ用）。"""
        return dict(self._used_bytes)

    async def update(self) -> None:
        """全インスタンスのGPUメモリ使用量を並列取得してキャッシュ更新。"""
        if not self.enabled:
            return
        async with self._lock:
            urls = list(self._url_to_instance.keys())
            results = await asyncio.gather(
                *[self._fetch_one(self._url_to_instance[u]) for u in urls],
                return_exceptions=True,
            )
            for url, result in zip(urls, results):
                if isinstance(result, Exception):
                    # 取得失敗時は古い値を保持せず削除（フェイルセーフ＝許可）
                    self._used_bytes.pop(url, None)
                elif result is None:
                    self._used_bytes.pop(url, None)
                else:
                    self._used_bytes[url] = result

    async def _fetch_one(self, instance: str) -> int | None:
        """指定インスタンスのGPUメモリ使用量（bytes）を返す。取得失敗時は None。"""
        query = f'nvidia_smi_memory_used_bytes{{instance="{instance}"}}'
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self._metrics_url}/api/v1/query",
                    params={"query": query},
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("data", {}).get("result", [])
                if not results:
                    return None
                # 複数GPU搭載時は最大値を採用
                return int(max(float(r["value"][1]) for r in results))
        except Exception as e:
            log.debug("GPU memory fetch failed for %s: %s", instance, e)
            return None
