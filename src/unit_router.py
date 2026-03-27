"""自然言語 → ユニット振り分け（Unit Router）。"""

from src.llm.unit_llm import UnitLLM
from src.logger import get_logger, new_trace_id

log = get_logger(__name__)

_ROUTE_PROMPT_TEMPLATE = """\
あなたはユニットルーターです。ユーザーの入力を分析し、最適なユニットを1つ選んでください。
ユニットの選択のみを行ってください。パラメータの解析は不要です。

## ユニット一覧
{units_text}

## 出力形式（厳守）
{{"unit": "ユニット名"}}

JSON以外は返さないでください。

## ユーザー入力
{user_input}
"""


class UnitRouter:
    def __init__(self, bot):
        self.bot = bot
        self.llm = UnitLLM(bot.llm_router, purpose="unit_routing")

    def _build_units_text(self) -> str:
        lines = []
        for unit in self.bot.unit_manager.units.values():
            # RemoteUnitProxy の場合は内部ユニットを参照
            actual = getattr(unit, "unit", unit)
            name = getattr(actual, "UNIT_NAME", "")
            desc = getattr(actual, "UNIT_DESCRIPTION", "")
            if name:
                lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    async def route(self, user_input: str, channel: str = "discord") -> dict:
        trace_id = new_trace_id()
        log.info("Routing input (trace=%s): %.80s", trace_id, user_input)

        prompt = _ROUTE_PROMPT_TEMPLATE.format(
            units_text=self._build_units_text(),
            user_input=user_input,
        )

        try:
            result = await self.llm.extract_json(prompt)
            if "unit" not in result:
                raise ValueError("Missing 'unit' key")
            log.info("Routed to: %s (trace=%s)", result["unit"], trace_id)
            return {"unit": result["unit"], "message": user_input}
        except Exception as e:
            log.warning("Routing failed (%s), falling back to chat (trace=%s)", e, trace_id)
            return {"unit": "chat", "message": user_input}
