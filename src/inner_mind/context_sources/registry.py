"""ContextSourceRegistry — ソースの登録・一括収集。"""

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

    async def collect_all(self, shared: dict) -> list[dict]:
        """全ソースから収集。失敗/無効なソースはスキップ。"""
        results = []
        for source in self._sources:
            if not source.enabled:
                continue
            try:
                data = await source.collect(shared)
                if data is not None:
                    results.append({
                        "name": source.name,
                        "data": data,
                        "text": source.format_for_prompt(data),
                    })
            except Exception:
                log.warning("ContextSource %s failed, skipping", source.name, exc_info=True)
        return results
