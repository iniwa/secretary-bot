"""自然言語 → ユニット振り分け（Skill Router）。"""

import json

from src.logger import get_logger, new_trace_id

log = get_logger(__name__)

_ROUTE_PROMPT_TEMPLATE = """\
あなたはスキルルーターです。ユーザーの入力を分析し、最適なスキルを1つ選んでください。
以下のスキル一覧から選び、JSON形式で返却してください。

## スキル一覧
{skills_text}

## 出力形式（厳守）
{{"skill": "スキル名", "parsed": {{...解析した情報...}}}}

JSON以外は返さないでください。

## ユーザー入力
{user_input}
"""


class SkillRouter:
    def __init__(self, bot):
        self.bot = bot

    def _build_skills_text(self) -> str:
        lines = []
        for unit in self.bot.unit_manager.units.values():
            lines.append(f"- {unit.SKILL_NAME}: {unit.SKILL_DESCRIPTION}")
        return "\n".join(lines)

    async def route(self, user_input: str, channel: str = "discord") -> dict:
        trace_id = new_trace_id()
        log.info("Routing input (trace=%s): %.80s", trace_id, user_input)

        prompt = _ROUTE_PROMPT_TEMPLATE.format(
            skills_text=self._build_skills_text(),
            user_input=user_input,
        )

        try:
            response = await self.bot.llm_router.generate(
                prompt,
                purpose="skill_routing",
            )
            result = json.loads(response)
            if "skill" not in result:
                raise ValueError("Missing 'skill' key")
            log.info("Routed to: %s (trace=%s)", result["skill"], trace_id)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Routing failed (%s), falling back to chat (trace=%s)", e, trace_id)
            return {"skill": "chat", "parsed": {"message": user_input}}
