"""ネット検索ユニット。SearXNG で検索し、LLM で要約する。"""

import httpx

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit
from src.logger import get_logger

log = get_logger(__name__)

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## タスク
ユーザーの発言から検索クエリを抽出してください。
質問文はそのまま使わず、検索に適したキーワードに変換してください。

## 出力形式（厳守）
{{"query": "検索キーワード"}}

- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""

_SUMMARIZE_PROMPT = """\
以下の検索結果をもとに、ユーザーの質問に回答してください。

## ルール
- 検索結果の情報を正確にまとめてください
- 情報が不十分な場合はその旨を伝えてください
- 出典URLは含めなくてよいです（別途表示されます）
- 簡潔にまとめてください（3〜5文程度）

## ユーザーの質問
{question}

## 検索結果
{results}
"""

_MAX_RESULTS = 5


class WebSearchUnit(BaseUnit):
    UNIT_NAME = "web_search"
    UNIT_DESCRIPTION = "ネットで調べもの・検索。「〜を調べて」「〜って何？」「最新の〜は？」など。"

    def __init__(self, bot):
        super().__init__(bot)
        search_cfg = bot.config.get("searxng", {})
        self._base_url = search_cfg.get("url", "http://localhost:8888")
        self._max_results = search_cfg.get("max_results", _MAX_RESULTS)

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        # サーキットブレーカーチェック
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        message = parsed.get("message", "")

        try:
            # 1. LLMで検索クエリを抽出
            extracted = await self._extract_query(message)
            query = extracted.get("query", message)

            # 2. SearXNG で検索
            results = await self._search(query)
            if not results:
                result = f"「{query}」の検索結果が見つかりませんでした。"
                self.breaker.record_success()
                self.session_done = True
                result = await self.personalize(result, message, flow_id)
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "query": query, "count": 0}, flow_id)
                return result

            # 3. LLM で要約
            summary = await self._summarize(message, results)

            # 4. 出典リストを付与
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

    async def _extract_query(self, user_input: str) -> dict:
        """ユーザー入力から検索クエリを抽出する。"""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        return await self.llm.extract_json(prompt)

    async def _search(self, query: str) -> list[dict]:
        """SearXNG API で検索を実行する。"""
        url = f"{self._base_url}/search"
        params = {
            "q": query,
            "format": "json",
            "language": "ja",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        raw_results = data.get("results", [])
        results = []
        for r in raw_results[:self._max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            })
        return results

    async def _summarize(self, question: str, results: list[dict]) -> str:
        """検索結果をLLMで要約する。"""
        results_text = ""
        for i, r in enumerate(results, 1):
            results_text += f"[{i}] {r['title']}\n{r['content']}\n\n"

        prompt = _SUMMARIZE_PROMPT.format(
            question=question,
            results=results_text.strip(),
        )
        return await self.llm.generate(prompt)

    def _format_sources(self, results: list[dict]) -> str:
        """出典リストを整形する。"""
        lines = ["📎 出典"]
        for i, r in enumerate(results, 1):
            lines.append(f"  [{i}] {r['title']}: {r['url']}")
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(WebSearchUnit(bot))
