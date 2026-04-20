"""ContextSourceRegistry — ソースの登録・一括収集・salienceによる注意フィルタ。"""

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
                    "source": source,
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

    async def _score_one(self, source: ContextSource, data: dict, shared: dict) -> float:
        try:
            score = await source.salience(data, shared)
            return max(0.0, min(1.0, float(score)))
        except Exception:
            log.debug("salience failed for %s", source.name, exc_info=True)
            return 0.5

    async def update_all(self) -> None:
        """全ソースの背景更新を並列実行。ハートビートから呼ぶ。"""
        active_sources = [s for s in self._sources if s.enabled]
        if not active_sources:
            return
        await asyncio.gather(
            *[self._update_one(s) for s in active_sources],
            return_exceptions=True,
        )

    async def collect_all(
        self,
        shared: dict,
        top_n: int | None = None,
        threshold: float = 0.0,
    ) -> list[dict]:
        """全ソースから並列収集し、salience でフィルタする。

        返り値の各 dict に "salience" キーを付与する。
        top_n が None なら閾値のみで絞る。
        always_include=True のソースは閾値に関わらず常に通す。
        """
        active_sources = [s for s in self._sources if s.enabled]
        if not active_sources:
            return []

        raw_results = await asyncio.gather(
            *[self._collect_one(s, shared) for s in active_sources],
            return_exceptions=True,
        )

        collected: list[dict] = []
        for source, result in zip(active_sources, raw_results, strict=False):
            if isinstance(result, Exception):
                log.warning("ContextSource %s raised: %s", source.name, result)
            elif result is not None:
                collected.append(result)

        if not collected:
            return []

        # salience 並列計算
        scores = await asyncio.gather(
            *[self._score_one(r["source"], r["data"], shared) for r in collected],
            return_exceptions=True,
        )
        for r, sc in zip(collected, scores, strict=False):
            r["salience"] = sc if isinstance(sc, float) else 0.5

        # always_include 分離
        forced = [r for r in collected if r["source"].always_include]
        candidates = [r for r in collected if not r["source"].always_include]

        # 閾値で絞り込み → salience 降順
        candidates = [r for r in candidates if r["salience"] >= threshold]
        candidates.sort(key=lambda r: r["salience"], reverse=True)

        if top_n is not None and top_n > 0:
            candidates = candidates[:top_n]

        selected = forced + candidates
        # プロンプト側の可読性のため、内部 "source" 参照はここで落とす
        for r in selected:
            r.pop("source", None)
        return selected
