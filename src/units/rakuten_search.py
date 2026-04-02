"""楽天市場商品検索ユニット。楽天検索結果ページを直接取得し、商品情報を抽出してLLMでおすすめを提示する。"""

import asyncio
import re
import urllib.parse

import httpx

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit
from src.logger import get_logger

log = get_logger(__name__)

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## タスク
楽天市場での商品検索に必要なキーワードを抽出してください。

重要: ユーザーの入力が前の検索を修正する指示の場合（例:「詰替え用を除外して」「もっと安いの」「別のブランドで」など）、
直前の会話履歴から元の検索キーワードを読み取り、新しい条件を組み合わせたキーワードを生成してください。

## 出力形式（厳守）
{{"keyword": "検索キーワード"}}

- keywordは商品名・カテゴリ名などコアなキーワードにしてください
- 「安い」「評価が高い」などの修飾語は除いてください
- 除外条件がある場合はマイナス検索（例: 洗顔フォーム -詰替え）を使ってください
- JSON1つだけを返してください
{context_block}
## ユーザー入力
{user_input}
"""

_RECOMMEND_PROMPT = """\
以下は楽天市場の検索結果から抽出した商品データです。
ユーザーの要望に合わせて、おすすめ商品を紹介してください。

## ルール
- 各商品について【商品名】【価格】【レビュー】【特徴】を整理して紹介
- レビューがない商品はその旨記載
- 広告（[PR]）商品はその旨を軽く触れてよい
- URLは含めなくてよいです（別途表示されます）
- 商品の特徴は商品名から読み取れる情報を要約する

## ユーザーの要望
{question}

## 商品データ（{count}件）
{results}
"""

_DEFAULT_MAX_RESULTS = 10

# ブラウザに偽装したヘッダーセット
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _parse_search_results(html: str) -> list[dict]:
    """楽天検索結果ページのHTMLから商品情報を抽出する。

    data属性とHTML内容の両方から情報を取得し、構造化データとして返す。
    """
    items = []

    # searchresultitem の各カードを分割して処理
    card_pattern = re.compile(
        r'<div\s+class="[^"]*searchresultitem[^"]*"'
        r'[^>]*?data-id="([^"]*)"'
        r'[^>]*?data-shop-id="([^"]*)"'
        r'[^>]*?data-track-price="([^"]*)"'
        r'[^>]*?data-card-type="([^"]*)"'
        r'[^>]*?>'
        r'(.*?)(?=<div\s+class="[^"]*searchresultitem|$)',
        re.DOTALL,
    )
    # data属性の順序が異なる場合に対応するため、個別にも抽出
    card_split = re.compile(
        r'(<div\s+class="[^"]*searchresultitem[^"]*"[^>]*>.*?)(?=<div\s+class="[^"]*searchresultitem|$)',
        re.DOTALL,
    )

    for card_match in card_split.finditer(html):
        card_html = card_match.group(1)
        item = _extract_item_from_card(card_html)
        if item and item.get("title"):
            items.append(item)

    return items


def _extract_item_from_card(card_html: str) -> dict | None:
    """1つの商品カードHTMLから情報を抽出する。"""

    def _attr(name: str) -> str:
        m = re.search(rf'{name}="([^"]*)"', card_html)
        return m.group(1) if m else ""

    item_id = _attr("data-id")
    shop_id = _attr("data-shop-id")
    raw_price = _attr("data-track-price")
    card_type = _attr("data-card-type")

    # 商品タイトル: <a ... title="..." data-link="item"> または title-link クラス
    title = ""
    title_match = re.search(r'<a[^>]*?title="([^"]*)"[^>]*?data-link="item"', card_html)
    if not title_match:
        title_match = re.search(r'<a[^>]*?class="[^"]*title-link[^"]*"[^>]*?title="([^"]*)"', card_html)
    if title_match:
        title = title_match.group(1)
        title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')

    # レビュー評価
    rating = ""
    rating_match = re.search(r'class="score"[^>]*>([^<]+)', card_html)
    if rating_match:
        rating = rating_match.group(1).strip()

    # レビュー件数
    review_count = ""
    review_match = re.search(r'class="legend"[^>]*>([^<]+)', card_html)
    if review_match:
        review_count = review_match.group(1).strip().strip("()")

    # ショップ名: content merchant 内の <a> テキスト
    shop = ""
    shop_match = re.search(
        r'class="content merchant[^"]*"[^>]*>.*?<a[^>]*>([^<]+)',
        card_html,
        re.DOTALL,
    )
    if shop_match:
        shop = shop_match.group(1).strip()

    # 価格表示テキスト（「円〜」等を含む表示用）
    price_display = ""
    price_match = re.search(r'class="[^"]*price--[^"]*"[^>]*>(.*?)</div>', card_html, re.DOTALL)
    if price_match:
        price_text = re.sub(r"<[^>]+>", "", price_match.group(1))
        price_display = price_text.strip()
    elif raw_price:
        price_display = f"{int(raw_price):,}円"

    # 送料情報
    shipping = ""
    ship_match = re.search(r'free-shipping-label[^"]*"[^>]*>([^<]+)', card_html)
    if ship_match:
        shipping = ship_match.group(1).strip()

    # 商品URL構築
    url = ""
    if shop_id and item_id:
        url = f"https://item.rakuten.co.jp/{shop_id}/{item_id}/"

    is_pr = card_type == "cpc"

    return {
        "title": title,
        "price": price_display,
        "rating": rating,
        "review_count": review_count,
        "shop": shop,
        "shipping": shipping,
        "url": url,
        "is_pr": is_pr,
    }


class RakutenSearchUnit(BaseUnit):
    UNIT_NAME = "rakuten_search"
    UNIT_DESCRIPTION = "楽天市場で商品を検索・提案する。「楽天で◯◯を探して」「楽天でおすすめの◯◯は？」「楽天で安い◯◯を教えて」など。"

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("rakuten_search", {})
        self._max_results = cfg.get("max_results", _DEFAULT_MAX_RESULTS)
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
        conversation_context = parsed.get("conversation_context", [])

        try:
            # 1. LLMでキーワードを抽出（会話履歴から元のキーワードを保持）
            extracted = await self._extract_keyword(message, conversation_context)
            keyword = extracted.get("keyword", message)
            log.info("rakuten_search keyword=%s", keyword)

            # 2. 楽天検索結果ページを直接取得・解析
            items = await self._search_rakuten(keyword)
            if not items:
                result = f"「{keyword}」の楽天市場商品が見つかりませんでした。"
                self.last_debug = {"keyword": keyword, "items": [], "error": "no_results"}
                self.breaker.record_success()
                self.session_done = True
                result = await self.personalize(result, message, flow_id)
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "count": 0}, flow_id)
                return result

            # 3. LLMでおすすめをまとめる（会話履歴からユーザーの要望全体を反映）
            recommendation, llm_prompt = await self._recommend(message, items, conversation_context)

            # 4. 出典リストを付与
            sources = self._format_sources(items)
            result = f"{recommendation}\n\n{sources}"

            # デバッグデータ保存（WebGUIで閲覧可能）
            self.last_debug = {
                "keyword": keyword,
                "item_count": len(items),
                "items": items,
                "llm_prompt": llm_prompt,
                "llm_response": recommendation,
                "final_output": result,
            }

            result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "keyword": keyword, "count": len(items)}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_keyword(self, user_input: str, conversation_context: list[dict] | None = None) -> dict:
        context_block = ""
        if conversation_context:
            lines = [f"ユーザー: {r['content']}" for r in conversation_context]
            context_block = "\n## 直前の会話履歴\n" + "\n".join(lines) + "\n\n"
        prompt = _EXTRACT_PROMPT.format(user_input=user_input, context_block=context_block)
        return await self.llm.extract_json(prompt)

    async def _search_rakuten(self, keyword: str) -> list[dict]:
        """楽天検索結果ページを直接取得して商品情報を抽出する。"""
        encoded = urllib.parse.quote(keyword)
        url = f"https://search.rakuten.co.jp/search/mall/{encoded}/"

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=_BROWSER_HEADERS)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type:
                    log.warning("rakuten search: unexpected content-type=%s", content_type)
                    return []
                html = resp.text
        except Exception as e:
            log.error("rakuten search fetch failed: %s", e)
            return []

        items = _parse_search_results(html)
        log.info("rakuten search: parsed %d items for keyword=%s", len(items), keyword)

        # 重複除去（同一URLの商品はPR版を除外して通常版を優先）
        seen_urls: dict[str, int] = {}
        unique_items: list[dict] = []
        for item in items:
            item_url = item.get("url", "")
            if item_url in seen_urls:
                # 既に通常版があればPR版はスキップ
                existing_idx = seen_urls[item_url]
                if item["is_pr"] and not unique_items[existing_idx]["is_pr"]:
                    continue
                # 既にPR版があり通常版が来た場合は置き換え
                if not item["is_pr"] and unique_items[existing_idx]["is_pr"]:
                    unique_items[existing_idx] = item
                    continue
            seen_urls[item_url] = len(unique_items)
            unique_items.append(item)

        return unique_items[:self._max_results]

    async def _recommend(self, question: str, items: list[dict], conversation_context: list[dict] | None = None) -> tuple[str, str]:
        """LLMで商品を要約・推薦する。(レスポンス, プロンプト) を返す。"""
        results_text = ""
        for i, item in enumerate(items, 1):
            pr_tag = " [PR]" if item["is_pr"] else ""
            rating_str = f"★{item['rating']}（{item['review_count']}）" if item["rating"] else "レビューなし"
            results_text += (
                f"[{i}]{pr_tag} {item['title']}\n"
                f"  価格: {item['price']}"
                f"{' / ' + item['shipping'] if item['shipping'] else ''}\n"
                f"  レビュー: {rating_str}\n"
                f"  ショップ: {item['shop']}\n\n"
            )

        # 短いフォローアップの場合、会話履歴からユーザーの要望全体を復元
        effective_question = question
        if conversation_context and len(question) <= 30:
            ctx_lines = [f"ユーザー: {r['content']}" for r in conversation_context]
            effective_question = "\n".join(ctx_lines) + f"\nユーザー: {question}"

        prompt = _RECOMMEND_PROMPT.format(
            question=effective_question,
            count=len(items),
            results=results_text.strip(),
        )
        response = await self.llm.generate(prompt)
        return response, prompt

    def _format_sources(self, items: list[dict]) -> str:
        lines = ["🛒 楽天市場 検索結果リンク"]
        for i, item in enumerate(items, 1):
            pr_tag = " [PR]" if item["is_pr"] else ""
            name = item["title"][:50] + ("…" if len(item["title"]) > 50 else "")
            lines.append(f"  [{i}]{pr_tag} {name}: {item['url']}")
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(RakutenSearchUnit(bot))
