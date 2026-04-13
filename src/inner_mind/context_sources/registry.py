"""ContextSourceRegistry — ソースの登録・一括収集。"""

import asyncio

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)


class ContextSourceRegistry:
    """コンテキストソースの登録・一括収集を管理。"""

    def __init__(self):
        self._sources: list[ContextSource] = []

    def register(self, source: ContextSource):
        self._sources.append(source)
        self._sources.sort(key=lambda s: s.priority)

    @property
    def sources(self) -> list[ContextSource]:
        return list(self._sources)

    async def _collect_one(self, source: ContextSource, shared: dict) -> dict | None:
        """1つのソースから収集。失敗時は None を返す。"""
        try:
            data = await source.collect(shared)
            if data is not None:
                return {
                    "name": source.name,
                    "data": data,
                    "text": source.format_for_prompt(data),
                }
        except Exception:
            log.warning("ContextSource %s failed, skipping", source.name, exc_info=True)
        return None

    async def _update_one(self, source: ContextSource) -> None:
        try:
            await source.update()
        except Exception:
            log.warning("ContextSource %s update failed", source.name, exc_info=True)

    async def update_all(self) -> None:
        """全ソースの背景更新を並列実行。ハートビートから呼ぶ。"""
        active_sources = [s for s in self._sources if s.enabled]
        if not active_sources:
            return
        await asyncio.gather(
            *[self._update_one(s) for s in active_sources],
            return_exceptions=True,
        )

    async def collect_all(self, shared: dict) -> list[dict]:
        """全ソースから並列収集。失敗/無効なソースはスキップ。

        asyncio.gather() で全ソースを同時実行し、Ollamaマルチインスタンスと
        組み合わせることで複数のLLM呼び出しが異なるインスタンスに分配される。
        """
        active_sources = [s for s in self._sources if s.enabled]
        if not active_sources:
            return []

        raw_results = await asyncio.gather(
            *[self._collect_one(s, shared) for s in active_sources],
            return_exceptions=True,
        )

        results = []
        for source, result in zip(active_sources, raw_results):
            if isinstance(result, Exception):
                log.warning("ContextSource %s raised: %s", source.name, result)
            elif result is not None:
                results.append(result)
        return results
