"""URL取得・HTML→テキスト変換の共有ユーティリティ。"""

import re
from html.parser import HTMLParser

import httpx

from src.logger import get_logger

log = get_logger(__name__)

_DEFAULT_MAX_CHARS = 3000
_USER_AGENT = "Mozilla/5.0 (compatible; SecretaryBot/1.0)"


class HtmlToMarkdown(HTMLParser):
    """HTMLをMarkdownに変換するパーサー。LLMが読みやすい構造を保持する。"""

    _SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "footer", "header", "aside", "form"}
    _HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
    _SKIP_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}
    _SKIP_CLASS_KEYWORDS = {"nav", "navigation", "breadcrumb", "sidebar", "menu", "cookie", "banner", "toolbar", "topbar"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._skip_tag_stack: list[str] = []
        self._out: list[str] = []
        self._buf: list[str] = []
        self._heading: str = ""
        self._in_pre = False
        self._in_code = False
        self._bold_depth = 0
        self._em_depth = 0
        self._list_stack: list[str] = []
        self._ol_counters: list[int] = []
        self._in_li = False
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_th = False
        self._a_stack: list[tuple[str, list]] = []

    def _should_skip(self, attr: dict) -> bool:
        role = attr.get("role", "").lower()
        if role in self._SKIP_ROLES:
            return True
        classes = set(attr.get("class", "").lower().split())
        return bool(classes & self._SKIP_CLASS_KEYWORDS)

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

    def _flush_buf(self):
        text = "".join(self._buf).strip()
        if text:
            self._out.append(text)
        self._buf.clear()

    def _render_table(self) -> str:
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
        text = "\n".join(self._out)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_text(html: str) -> str:
    """HTML文字列をMarkdownテキストに変換する。"""
    parser = HtmlToMarkdown()
    parser.feed(html)
    return parser.get_markdown()


async def fetch_page_text(url: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """URLのページ本文を取得してテキスト化する。失敗時は空文字を返す。"""
    try:
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type:
                return ""
            text = extract_text(resp.text)
            log.debug("Fetched %s: %d chars", url, len(text))
            return text[:max_chars]
    except Exception as e:
        log.debug("Failed to fetch %s: %s", url, e)
        return ""
