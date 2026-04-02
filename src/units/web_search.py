"""ネット検索ユニット。SearXNG で検索し、各ページ本文を取得してLLM で要約する。"""

import asyncio
from html.parser import HTMLParser
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


class _HtmlToMarkdown(HTMLParser):
    """HTMLをMarkdownに変換するパーサー。LLMが読みやすい構造を保持する。"""

    _SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "footer", "header", "aside", "form"}
    _HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
    # スキップするrole属性値
    _SKIP_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}
    # スキップするclass名（部分一致）
    _SKIP_CLASS_KEYWORDS = {"nav", "navigation", "breadcrumb", "sidebar", "menu", "cookie", "banner", "toolbar", "topbar"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._skip_tag_stack: list[str] = []  # スキップ中タグ名のスタック
        self._out: list[str] = []
        # インライン要素バッファ（テキスト収集用）
        self._buf: list[str] = []
        # 状態フラグ
        self._heading: str = ""
        self._in_pre = False
        self._in_code = False
        self._bold_depth = 0
        self._em_depth = 0
        # リスト
        self._list_stack: list[str] = []  # "ul" / "ol"
        self._ol_counters: list[int] = []
        self._in_li = False
        # テーブル
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_th = False
        # リンク: ネスト対応のためスタックで管理 [(href, parent_buf), ...]
        self._a_stack: list[tuple[str, list]] = []

    def _should_skip(self, attr: dict) -> bool:
        """role・class属性からナビゲーション系要素を判定する。"""
        role = attr.get("role", "").lower()
        if role in self._SKIP_ROLES:
            return True
        classes = set(attr.get("class", "").lower().split())
        return bool(classes & self._SKIP_CLASS_KEYWORDS)

    # --- パーサーハンドラ ---

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attr = dict(attrs)

        if tag in self._SKIP_TAGS or self._should_skip(attr):
            self._skip_depth += 1
            self._skip_tag_stack.append(tag)
            return
        if self._skip_depth:
            return

        if tag in self._HEADING_TAGS:
            self._flush_buf()
            self._heading = self._HEADING_TAGS[tag]

        elif tag == "p":
            self._flush_buf()

        elif tag == "br":
            self._buf.append("\n")

        elif tag == "pre":
            self._flush_buf()
            self._in_pre = True
            self._out.append("```")

        elif tag == "code" and not self._in_pre:
            self._in_code = True
            self._buf.append("`")

        elif tag in ("strong", "b"):
            self._bold_depth += 1
            if self._bold_depth == 1:
                self._buf.append("**")

        elif tag in ("em", "i"):
            self._em_depth += 1
            if self._em_depth == 1:
                self._buf.append("*")

        elif tag == "a":
            # 現在のバッファをスタックに退避して新しいバッファで収集開始
            self._a_stack.append((attr.get("href", ""), self._buf))
            self._buf = []

        elif tag in ("ul", "ol"):
            self._flush_buf()
            self._list_stack.append(tag)
            self._ol_counters.append(0)

        elif tag == "li":
            self._flush_buf()
            self._in_li = True

        elif tag == "table":
            self._flush_buf()
            self._in_table = True
            self._table_rows = []

        elif tag == "tr":
            self._current_row = []

        elif tag in ("th", "td"):
            self._current_cell = []
            self._in_th = tag == "th"

        elif tag == "hr":
            self._flush_buf()
            self._out.append("\n---\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()

        # スキップスタックの先頭と一致すれば深度を戻す
        if self._skip_depth and self._skip_tag_stack and self._skip_tag_stack[-1] == tag:
            self._skip_depth -= 1
            self._skip_tag_stack.pop()
            return
        if self._skip_depth:
            return

        if tag in self._HEADING_TAGS:
            text = "".join(self._buf).strip()
            if text:
                self._out.append(f"\n{self._heading} {text}\n")
            self._buf.clear()
            self._heading = ""

        elif tag == "p":
            text = "".join(self._buf).strip()
            if text:
                self._out.append(f"\n{text}\n")
            self._buf.clear()

        elif tag == "pre":
            text = "".join(self._buf)
            self._out.append(text)
            self._out.append("```\n")
            self._buf.clear()
            self._in_pre = False

        elif tag == "code" and not self._in_pre:
            self._buf.append("`")
            self._in_code = False

        elif tag in ("strong", "b"):
            if self._bold_depth == 1:
                self._buf.append("**")
            self._bold_depth = max(0, self._bold_depth - 1)

        elif tag in ("em", "i"):
            if self._em_depth == 1:
                self._buf.append("*")
            self._em_depth = max(0, self._em_depth - 1)

        elif tag == "a":
            if not self._a_stack:
                return
            href, parent_buf = self._a_stack.pop()
            text = "".join(self._buf).strip()
            self._buf = parent_buf
            if text and href:
                self._buf.append(f"[{text}]({href})")
            elif text:
                self._buf.append(text)
            # テキストなしリンク（アイコンのみ等）は破棄

        elif tag == "li":
            text = "".join(self._buf).strip()
            self._buf.clear()
            self._in_li = False
            if not self._list_stack:
                return
            list_type = self._list_stack[-1]
            if list_type == "ol":
                self._ol_counters[-1] += 1
                prefix = "  " * (len(self._list_stack) - 1) + f"{self._ol_counters[-1]}. "
            else:
                prefix = "  " * (len(self._list_stack) - 1) + "- "
            self._out.append(f"{prefix}{text}")

        elif tag in ("ul", "ol"):
            self._flush_buf()
            if self._list_stack:
                self._list_stack.pop()
            if self._ol_counters:
                self._ol_counters.pop()
            if not self._list_stack:
                self._out.append("")

        elif tag in ("th", "td"):
            self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = []

        elif tag == "tr":
            if self._current_row:
                self._table_rows.append((self._current_row, self._in_th))
            self._current_row = []

        elif tag == "table":
            self._out.append(self._render_table())
            self._in_table = False
            self._table_rows = []

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        if self._in_pre:
            self._buf.append(data)
            return
        text = data if self._in_code else data.replace("\n", " ").replace("\r", "")
        if self._in_table and self._current_cell is not None:
            self._current_cell.append(text)
        else:
            self._buf.append(text)

    # --- ヘルパー ---

    def _flush_buf(self):
        """バッファに溜まったインラインテキストを出力に移す。"""
        text = "".join(self._buf).strip()
        if text:
            self._out.append(text)
        self._buf.clear()

    def _render_table(self) -> str:
        """収集したテーブル行をMarkdownテーブルに変換する。"""
        if not self._table_rows:
            return ""
        lines = []
        header_done = False
        for row, is_th in self._table_rows:
            line = "| " + " | ".join(cell or " " for cell in row) + " |"
            lines.append(line)
            if (is_th or not header_done) and not header_done:
                sep = "| " + " | ".join("---" for _ in row) + " |"
                lines.append(sep)
                header_done = True
        return "\n" + "\n".join(lines) + "\n"

    def get_markdown(self) -> str:
        self._flush_buf()
        # 連続する空行を1行に圧縮
        text = "\n".join(self._out)
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _extract_text(html: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(html)
    return parser.get_markdown()


class WebSearchUnit(BaseUnit):
    UNIT_NAME = "web_search"
    UNIT_DESCRIPTION = "ネットで調べもの・検索。「〜を調べて」「〜って何？」「最新の〜は？」など。"

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
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; SecretaryBot/1.0)"}
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type:
                    return ""
                text = _extract_text(resp.text)
                log.debug("Fetched %s: %d chars (markdown)", url, len(text))
                return text[:self._max_chars_per_page]
        except Exception as e:
            log.debug("Failed to fetch %s: %s", url, e)
            return ""

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
