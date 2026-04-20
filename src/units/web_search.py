"""ネット検索ユニット。SearXNG で検索し、各ページ本文を取得してLLM で要約する。"""

import asyncio

import httpx

from src.fetch_utils import fetch_page_text
from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit

log = get_logger(__name__)

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## タスク
ユーザーの発言から検索クエリを抽出してください。
質問文はそのまま使わず、検索に適したキーワードに変換してください。

重要: ユーザーの入力が「調べて」「検索して」のような短い指示の場合、
直前の会話履歴から検索すべきトピックを読み取ってクエリを生成してください。

## 出力形式（厳守）
{{"query": "検索キーワード"}}

- JSON1つだけを返してください。
{context_block}
## ユーザー入力
{user_input}
"""

_SUMMARIZE_PROMPT = """\
以下の検索結果をもとに、ユーザーの質問に詳しく回答してください。

## ルール
- 検索結果の情報を正確にまとめてください
- 情報が不十分な場合はその旨を伝えてください
- 出典URLは含めなくてよいです（別途表示されます）
- 重要な情報は省略せず、具体的な数値・仕様・事実を含めてください

## ユーザーの質問
{question}

## 検索結果
{results}
"""

_MAX_RESULTS = 5
_FETCH_PAGES = 3        # ページ本文を取得するURL数
_MAX_CHARS_PER_PAGE = 3000  # 1ページあたりの最大文字数


class WebSearchUnit(BaseUnit):
    UNIT_NAME = "web_search"
    UNIT_DESCRIPTION = "ネットで調べもの・検索。「〜を調べて」「〜って何？」「最新の〜は？」など。"
    AUTONOMY_TIER = 3
    AUTONOMOUS_ACTIONS = ["search"]
    AUTONOMY_HINT = "search: params={\"query\":str}。ユーザーの未解決の疑問や『調べておいて』に類する発言があった時に提案。"

    def __init__(self, bot):
        super().__init__(bot)
        search_cfg = bot.config.get("searxng", {})
        self._base_url = search_cfg.get("url", "http://localhost:8888")
        self._max_results = search_cfg.get("max_results", _MAX_RESULTS)
        self._fetch_pages = search_cfg.get("fetch_pages", _FETCH_PAGES)
        self._max_chars_per_page = search_cfg.get("max_chars_per_page", _MAX_CHARS_PER_PAGE)

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        message = parsed.get("message", "")
        conversation_context = parsed.get("conversation_context", [])

        try:
            # 1. LLMで検索クエリを抽出（会話コンテキスト付き）
            extracted = await self._extract_query(message, conversation_context)
            query = extracted.get("query", message)
            log.info("web_search query: %s", query)

            # 2. SearXNG で検索
            results = await self._search(query)
            if not results:
                result = f"「{query}」の検索結果が見つかりませんでした。"
                self.breaker.record_success()
                self.session_done = True
                result = await self.personalize(result, message, flow_id)
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "query": query, "count": 0}, flow_id)
                return result

            # 3. 上位N件のページ本文を並列フェッチ
            fetch_targets = results[:self._fetch_pages]
            page_texts = await self._fetch_pages_parallel(fetch_targets)
            for i, text in enumerate(page_texts):
                if text:
                    results[i]["page_text"] = text

            # 4. LLM で要約（会話コンテキスト付き）
            summary = await self._summarize(message, results, conversation_context)

            # 5. 出典リストを付与
            sources = self._format_sources(results)
            result = f"{summary}\n\n{sources}"

            result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "query": query, "count": len(results)}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_query(self, user_input: str, conversation_context: list[dict] | None = None) -> dict:
        context_block = ""
        if conversation_context:
            lines = [f"ユーザー: {r['content']}" for r in conversation_context]
            context_block = "\n## 直前の会話履歴（ユーザーの発言のみ）\n" + "\n".join(lines) + "\n\n"
        prompt = _EXTRACT_PROMPT.format(user_input=user_input, context_block=context_block)
        return await self.llm.extract_json(prompt)

    async def _search(self, query: str) -> list[dict]:
        """SearXNG API で検索を実行する。"""
        url = f"{self._base_url}/search"
        params = {"q": query, "format": "json", "language": "ja"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for r in data.get("results", [])[:self._max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),  # SearXNGのスニペット
                "page_text": "",
            })
        return results

    async def _fetch_page_text(self, url: str) -> str:
        """URLのページ本文を取得してテキスト化する。失敗時は空文字を返す。"""
        return await fetch_page_text(url, max_chars=self._max_chars_per_page)

    async def _fetch_pages_parallel(self, results: list[dict]) -> list[str]:
        """複数URLを並列フェッチする。"""
        tasks = [self._fetch_page_text(r["url"]) for r in results]
        return await asyncio.gather(*tasks)

    async def _summarize(self, question: str, results: list[dict], conversation_context: list[dict] | None = None) -> str:
        """検索結果（スニペット + ページ本文）をLLMで要約する。"""
        results_text = ""
        for i, r in enumerate(results, 1):
            # ページ本文があればそちらを優先、なければスニペット
            body = r.get("page_text") or r.get("content", "")
            results_text += f"[{i}] {r['title']}\nURL: {r['url']}\n{body}\n\n"

        # 短い指示（「調べて」等）の場合、会話履歴から元の質問を復元
        effective_question = question
        if conversation_context and len(question) <= 20:
            ctx_lines = [f"ユーザー: {r['content']}" for r in conversation_context]
            effective_question = "\n".join(ctx_lines) + f"\nユーザー: {question}"

        prompt = _SUMMARIZE_PROMPT.format(
            question=effective_question,
            results=results_text.strip(),
        )
        return await self.llm.generate(prompt)

    def _format_sources(self, results: list[dict]) -> str:
        lines = ["📎 出典"]
        for i, r in enumerate(results, 1):
            fetched = " ✓" if r.get("page_text") else ""
            lines.append(f"  [{i}] {r['title']}{fetched}: {r['url']}")
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(WebSearchUnit(bot))
