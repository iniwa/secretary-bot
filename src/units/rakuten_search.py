"""楽天市場商品検索ユニット。ユーザーの要望に合った商品を楽天APIで検索し、LLMでおすすめを提示する。"""

import os
import httpx

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit
from src.logger import get_logger

log = get_logger(__name__)

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## タスク
楽天市場での商品検索に必要な情報を抽出してください。

## 出力形式（厳守）
{{"keyword": "検索キーワード", "sort": "ソート方法"}}

## sortの選択肢
- "-reviewCount": レビュー数が多い順（デフォルト・おすすめ）
- "-reviewAverage": 評価が高い順
- "itemPrice": 価格が安い順
- "-itemPrice": 価格が高い順
- "standard": 楽天標準順

ユーザーが「安い」「安く」「お手頃」「コスパ」と言ったら "itemPrice"
ユーザーが「評価が高い」「高評価」「評判がいい」と言ったら "-reviewAverage"
ユーザーが「人気」「おすすめ」「評判」「売れてる」と言ったら "-reviewCount"
それ以外は "-reviewCount" を使用してください。

## ユーザー入力
{user_input}
"""

_RECOMMEND_PROMPT = """\
以下の楽天市場の検索結果から、ユーザーの要望に合ったおすすめ商品を紹介してください。

## ルール
- 上位3〜5件をおすすめとして紹介してください
- 各商品の特徴・価格・レビュー情報を含めてください
- ユーザーの要望に合った観点で推薦コメントを添えてください
- 価格は円表示にしてください
- URLは含めなくてよいです（別途表示されます）

## ユーザーの要望
{question}

## 検索結果
{results}
"""

_DEFAULT_HITS = 10      # API取得件数
_DEFAULT_DISPLAY = 5    # 表示・LLMに渡す件数


class RakutenSearchUnit(BaseUnit):
    UNIT_NAME = "rakuten_search"
    UNIT_DESCRIPTION = "楽天市場で商品を検索・提案する。「楽天で◯◯を探して」「楽天でおすすめの◯◯は？」「楽天で安い◯◯を教えて」など。"

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("rakuten_search", {})
        self._app_id = os.environ.get("RAKUTEN_APP_ID", "")
        self._hits = cfg.get("hits", _DEFAULT_HITS)
        self._display_hits = cfg.get("display_hits", _DEFAULT_DISPLAY)

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        message = parsed.get("message", "")

        try:
            if not self._app_id:
                result = "楽天APIキー（RAKUTEN_APP_ID）が設定されていないため商品を検索できません。`.env` に `RAKUTEN_APP_ID` を追加してください。"
                self.breaker.record_success()
                self.session_done = True
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "error": "no_api_key"}, flow_id)
                return result

            # 1. LLMでキーワード・ソート方法を抽出
            extracted = await self._extract_params(message)
            keyword = extracted.get("keyword", message)
            sort = extracted.get("sort", "-reviewCount")
            log.info("rakuten_search keyword=%s sort=%s", keyword, sort)

            # 2. 楽天市場商品検索API呼び出し
            items = await self._search(keyword, sort)
            if not items:
                result = f"「{keyword}」の商品が楽天市場で見つかりませんでした。"
                self.breaker.record_success()
                self.session_done = True
                result = await self.personalize(result, message, flow_id)
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "count": 0}, flow_id)
                return result

            # 3. LLMでおすすめをまとめる
            recommendation = await self._recommend(message, items)

            # 4. 商品リンクを付与
            links = self._format_links(items[:self._display_hits])
            result = f"{recommendation}\n\n{links}"

            result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "keyword": keyword, "count": len(items)}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str) -> dict:
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        return await self.llm.extract_json(prompt)

    async def _search(self, keyword: str, sort: str) -> list[dict]:
        """楽天市場商品検索APIを呼び出す。"""
        url = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
        params = {
            "applicationId": self._app_id,
            "keyword": keyword,
            "hits": self._hits,
            "sort": sort,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        items = []
        for r in data.get("Items", [])[:self._display_hits]:
            item = r.get("Item", r)
            items.append({
                "name": item.get("itemName", ""),
                "price": item.get("itemPrice", 0),
                "url": item.get("itemUrl", ""),
                "review_count": item.get("reviewCount", 0),
                "review_average": item.get("reviewAverage", 0.0),
                "shop_name": item.get("shopName", ""),
                "caption": item.get("itemCaption", "")[:200],
            })
        return items

    async def _recommend(self, question: str, items: list[dict]) -> str:
        """LLMで商品を要約・推薦する。"""
        results_text = ""
        for i, item in enumerate(items, 1):
            results_text += (
                f"[{i}] {item['name']}\n"
                f"  価格: {item['price']:,}円\n"
                f"  ショップ: {item['shop_name']}\n"
                f"  レビュー: {item['review_average']}点（{item['review_count']}件）\n"
                f"  説明: {item['caption']}\n\n"
            )
        prompt = _RECOMMEND_PROMPT.format(
            question=question,
            results=results_text.strip(),
        )
        return await self.llm.generate(prompt)

    def _format_links(self, items: list[dict]) -> str:
        lines = ["🛒 楽天市場 商品リンク"]
        for i, item in enumerate(items, 1):
            name = item["name"][:40] + ("…" if len(item["name"]) > 40 else "")
            lines.append(f"  [{i}] {name} ¥{item['price']:,}: {item['url']}")
        return "\n".join(lines)


async def setup(bot) -> None:
    await bot.add_cog(RakutenSearchUnit(bot))
