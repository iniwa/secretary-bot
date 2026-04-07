"""ConversationSource — 直近の会話履歴。URL内容の取得を含む。"""

import asyncio
import re

from src.fetch_utils import fetch_page_text
from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_MAX_URLS = 3
_MAX_CHARS_PER_URL = 2000


class ConversationSource(ContextSource):
    name = "最近の会話"
    priority = 10

    async def collect(self, shared: dict) -> dict | None:
        messages = await self.bot.database.get_recent_messages(limit=20)
        if not messages:
            return None

        # メッセージ中のURLを抽出（直近メッセージ優先）
        urls: list[str] = []
        seen: set[str] = set()
        for m in messages:
            for url in _URL_RE.findall(m.get("content", "")):
                if url not in seen and len(urls) < _MAX_URLS:
                    seen.add(url)
                    urls.append(url)

        # URL内容を並列取得
        url_contents: dict[str, str] = {}
        if urls:
            tasks = [fetch_page_text(u, max_chars=_MAX_CHARS_PER_URL) for u in urls]
            results = await asyncio.gather(*tasks)
            for url, text in zip(urls, results):
                if text:
                    url_contents[url] = text

        return {"messages": messages, "url_contents": url_contents}

    def format_for_prompt(self, data: dict) -> str:
        lines = []
        for m in reversed(data["messages"]):
            role = "ミミ" if m["role"] == "assistant" else "ユーザー"
            lines.append(f"{role}: {m['content']}")
        text = "\n".join(lines[-20:])

        url_contents = data.get("url_contents", {})
        if url_contents:
            text += "\n\n--- 会話中のURL内容 ---"
            for url, content in url_contents.items():
                text += f"\n\n[{url}]\n{content}"

        return text
