"""楽天市場商品検索ユニット。SearXNGで楽天市場を検索し、LLMでおすすめを提示する。"""

import asyncio
import httpx

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit
from src.units.web_search import _HtmlToMarkdown
from src.logger import get_logger

log = get_logger(__name__)

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## タスク
楽天市場での商品検索に必要なキーワードを抽出してください。

## 出力形式（厳守）
{{"keyword": "検索キーワード"}}

- keywordは商品名・カテゴリ名などコアなキーワードにしてください
- 「安い」「評価が高い」などの修飾語は除いてください
- JSON1つだけを返してください

## ユーザー入力
{user_input}
"""

_RECOMMEND_PROMPT = """\
以下は楽天市場の商品ページの内容です。
各ページから商品情報を抽出し、ユーザーの要望に合わせておすすめ商品を紹介してください。

## 抽出ルール
- 各商品について以下の情報を抽出してください：
  - 【商品名】: ページタイトルや見出しから
  - 【価格】: 円表示（税込・税別が分かれば記載）
  - 【レビュー】: 評価（★X.X）と件数（X件）
  - 【特徴】: 商品説明から主要な特徴を2〜3点
- 情報が見つからない項目は「情報なし」と記載
- URLは含めなくてよいです（別途表示されます）

## ユーザーの要望
{question}

## 商品ページの内容（{count}件）
{results}
"""

_DEFAULT_MAX_RESULTS = 5
_DEFAULT_FETCH_PAGES = 5
_DEFAULT_MAX_CHARS_PER_PAGE = 5000


def _parse_html(html: str) -> str:
    """HTMLをMarkdownに変換する（_HtmlToMarkdownを使用）。"""
    parser = _HtmlToMarkdown()
    parser.feed(html)
    return parser.get_text()


class RakutenSearchUnit(BaseUnit):
    UNIT_NAME = "rakuten_search"
    UNIT_DESCRIPTION = "楽天市場で商品を検索・提案する。「楽天で◯◯を探して」「楽天でおすすめの◯◯は？」「楽天で安い◯◯を教えて」など。"

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("rakuten_search", {})
        searxng_cfg = bot.config.get("searxng", {})
        self._base_url = searxng_cfg.get("url", "http://localhost:8888")
        self._max_results = cfg.get("max_results", _DEFAULT_MAX_RESULTS)
        self._fetch_pages = cfg.get("fetch_pages", _DEFAULT_FETCH_PAGES)
        self._max_chars_per_page = cfg.get("max_chars_per_page", _DEFAULT_MAX_CHARS_PER_PAGE)
        # デバッグ用: 最後の実行データを保持
        self.last_debug: dict = {}

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        message = parsed.get("message", "")

        try:
            # 1. LLMでキーワードを抽出
            extracted = await self._extract_keyword(message)
            keyword = extracted.get("keyword", message)
            log.info("rakuten_search keyword=%s", keyword)

            # 2. SearXNGで item.rakuten.co.jp（個別商品ページ）を検索
            query = f"site:item.rakuten.co.jp {keyword}"
            results = await self._search(query)
            if not results:
                result = f"「{keyword}」の楽天市場商品が見つかりませんでした。"
                self.last_debug = {"keyword": keyword, "query": query, "search_results": [], "error": "no_results"}
                self.breaker.record_success()
                self.session_done = True
                result = await self.personalize(result, message, flow_id)
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "count": 0}, flow_id)
                return result

            # 3. 上位N件のページ本文を並列フェッチ
            fetch_targets = results[:self._fetch_pages]
            page_texts = await self._fetch_pages_parallel(fetch_targets)
            for i, text in enumerate(page_texts):
                if text:
                    results[i]["page_text"] = text

            # 4. LLMでおすすめをまとめる（プロンプトも返してデバッグ保存）
            recommendation, llm_prompt = await self._recommend(message, keyword, results)

            # 5. 出典リストを付与
            sources = self._format_sources(results)
            result = f"{recommendation}\n\n{sources}"

            # デバッグデータ保存（WebGUIで閲覧可能）
            self.last_debug = {
                "keyword": keyword,
                "query": query,
                "search_results": [
                    {
                        "title": r["title"],
                        "url": r["url"],
                        "snippet": r["content"],
                        "page_text_chars": len(r.get("page_text", "")),
                        "page_text_preview": r.get("page_text", "")[:500],
                    }
                    for r in results
                ],
                "llm_prompt": llm_prompt,
                "llm_response": recommendation,
                "final_output": result,
            }

            result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "keyword": keyword, "count": len(results)}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_keyword(self, user_input: str) -> dict:
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
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
                "content": r.get("content", ""),
                "page_text": "",
            })
        return results

    async def _fetch_page_text(self, url: str) -> str:
        """URLのページ本文を取得してテキスト化する。失敗時は空文字を返す。"""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; SecretaryBot/1.0)"}
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type:
                    return ""
                text = _parse_html(resp.text)
                log.debug("Fetched %s: %d chars", url, len(text))
                return text[:self._max_chars_per_page]
        except Exception as e:
            log.debug("Failed to fetch %s: %s", url, e)
            return ""

    async def _fetch_pages_parallel(self, results: list[dict]) -> list[str]:
        tasks = [self._fetch_page_text(r["url"]) for r in results]
        return await asyncio.gather(*tasks)

    async def _recommend(self, question: str, keyword: str, results: list[dict]) -> tuple[str, str]:
        """LLMで商品を要約・推薦する。(レスポンス, プロンプト) を返す。"""
        results_text = ""
        for i, r in enumerate(results, 1):
            body = r.get("page_text") or r.get("content", "")
            results_text += f"[{i}] {r['title']}\nURL: {r['url']}\n{body}\n\n"

        prompt = _RECOMMEND_PROMPT.format(
            question=question,
            count=len(results),
            results=results_text.strip(),
        )
        response = await self.llm.generate(prompt)
        return response, prompt

    def _format_sources(self, results: list[dict]) -> str:
        lines = ["🛒 楽天市場 検索結果リンク"]
        for i, r in enumerate(results, 1):
            fetched = " ✓" if r.get("page_text") else ""
            name = r["title"][:50] + ("…" if len(r["title"]) > 50 else "")
            lines.append(f"  [{i}] {name}{fetched}: {r['url']}")
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(RakutenSearchUnit(bot))
