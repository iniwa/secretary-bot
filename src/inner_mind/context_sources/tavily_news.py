"""TavilyNewsSource — Webニュースを Tavily Search API 経由で取得。

- TAVILY_API_KEY 環境変数が必要（未設定なら is_configured=False）
- config: inner_mind.tavily_news
    queries: list[str]        検索クエリ（例: ["生成AI", "ゲーム業界"]）
    max_results_per_query: 3  クエリごとに取得する最大件数
    lookback_days: 2          news フィルタの日数
    topic: "news"             Tavily の topic 指定
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import httpx

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)

_API_URL = "https://api.tavily.com/search"


class TavilyNewsSource(ContextSource):
    name = "Webニュース"
    priority = 100

    def __init__(self, bot):
        super().__init__(bot)
        self._cache: list[dict] = []
        self._cache_at: datetime | None = None
        self._lock = asyncio.Lock()

    def _cfg(self) -> dict:
        return self.bot.config.get("inner_mind", {}).get("tavily_news", {}) or {}

    def _api_key(self) -> str:
        return os.environ.get("TAVILY_API_KEY", "")

    async def _queries(self) -> list[str]:
        """DBに保存された queries を優先。無ければ config.yaml を使用。"""
        raw = await self.bot.database.get_setting("inner_mind.tavily_news.queries")
        if raw:
            # CSV形式で保存（JSONより堅牢、WebGUIでも扱いやすい）
            items = [s.strip() for s in raw.split(",")]
        else:
            items = self._cfg().get("queries", []) or []
        return [s for s in items if isinstance(s, str) and s.strip()]

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key())

    async def update(self) -> None:
        if not self.is_configured:
            return
        async with self._lock:
            cfg = self._cfg()
            queries = await self._queries()
            if not queries:
                self._cache = []
                return
            max_per_q = int(cfg.get("max_results_per_query", 3))
            lookback_days = int(cfg.get("lookback_days", 2))
            topic = cfg.get("topic", "news")

            results = await asyncio.gather(
                *[self._search_one(q, max_per_q, lookback_days, topic) for q in queries],
                return_exceptions=True,
            )
            aggregated: list[dict] = []
            for q, res in zip(queries, results):
                if isinstance(res, Exception):
                    log.warning("TavilyNewsSource query '%s' failed: %s", q, res)
                    continue
                for item in res or []:
                    item["_query"] = q
                    aggregated.append(item)
            # 重複URL除去
            seen: set[str] = set()
            deduped: list[dict] = []
            for item in aggregated:
                url = item.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    deduped.append(item)
            self._cache = deduped
            self._cache_at = datetime.now(timezone.utc)

    async def _search_one(self, query: str, max_results: int, days: int, topic: str) -> list[dict]:
        payload = {
            "api_key": self._api_key(),
            "query": query,
            "topic": topic,
            "search_depth": "basic",
            "max_results": max_results,
            "days": days,
            "include_answer": False,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data.get("results", []) or []

    async def collect(self, shared: dict) -> dict | None:
        if not self.is_configured or not self._cache:
            return None
        return {"results": list(self._cache)}

    def format_for_prompt(self, data: dict) -> str:
        results = data.get("results", [])
        if not results:
            return ""
        lines = []
        for r in results:
            title = (r.get("title", "") or "")[:100]
            q = r.get("_query", "")
            content = (r.get("content", "") or "").replace("\n", " ")[:120]
            head = f"[{q}] {title}" if q else title
            lines.append(f"- {head}: {content}")
        return "\n".join(lines)
