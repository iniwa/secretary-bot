"""自然言語 → ユニット振り分け（Unit Router）。"""

import time

from src.flow_tracker import get_flow_tracker
from src.llm.unit_llm import UnitLLM
from src.logger import get_logger, new_trace_id

log = get_logger(__name__)

# セッションの有効期限（秒）
_SESSION_TIMEOUT = 120

_ROUTE_PROMPT_TEMPLATE = """\
あなたはユニットルーターです。ユーザーの入力を分析し、最適なユニットを1つ選んでください。
ユニットの選択のみを行ってください。パラメータの解析は不要です。

重要: ユーザーの入力が短い場合や指示的な場合（例:「調べて」「検索して」「詳しく」など）は、
直前の会話履歴を参考にして、ユーザーが何を求めているかを判断してください。

## ユニット一覧
{units_text}

## 出力形式（厳守）
{{"unit": "ユニット名"}}

JSON以外は返さないでください。
{context_block}
## ユーザー入力
{user_input}
"""


class UnitRouter:
    def __init__(self, bot):
        self.bot = bot
        self.llm = UnitLLM(bot.llm_router, purpose="unit_routing")
        # チャネルごとの直前ユニットセッション
        # key: channel ("discord:{user_id}" | "webgui")
        # value: {"unit": str, "ts": float}
        self._sessions: dict[str, dict] = {}

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

    def _is_continuation(self, user_input: str) -> bool:
        """短い入力や番号だけなど、前のユニットへの続きと判断できるか。"""
        text = user_input.strip()
        # 数字のみ（ID指定）
        if text.isdigit():
            return True
        # 「はい」「いいえ」「うん」「お願い」「やめて」「全部」「1番」等の短い応答
        if len(text) <= 15:
            return True
        return False

    def _get_session(self, channel: str) -> str | None:
        """有効なセッションがあれば直前のユニット名を返す。"""
        session = self._sessions.get(channel)
        if not session:
            return None
        if time.monotonic() - session["ts"] > _SESSION_TIMEOUT:
            del self._sessions[channel]
            return None
        return session["unit"]

    def _set_session(self, channel: str, unit_name: str) -> None:
        self._sessions[channel] = {"unit": unit_name, "ts": time.monotonic()}

    def clear_session(self, channel: str, user_id: str = "") -> None:
        """ユニットが処理完了時にセッションをクリアする。"""
        session_key = f"{channel}:{user_id}" if user_id else channel
        self._sessions.pop(session_key, None)

    @staticmethod
    def _format_context(conversation_context: list[dict]) -> str:
        """会話履歴をプロンプト用テキストに変換する。"""
        if not conversation_context:
            return ""
        lines = []
        for row in conversation_context:
            role = "ユーザー" if row["role"] == "user" else "アシスタント"
            lines.append(f"{role}: {row['content']}")
        return "\n## 直前の会話履歴\n" + "\n".join(lines) + "\n\n"

    async def route(self, user_input: str, channel: str = "discord", user_id: str = "", flow_id: str | None = None, conversation_context: list[dict] | None = None) -> dict:
        trace_id = new_trace_id()
        log.info("Routing input (trace=%s): %.80s", trace_id, user_input)
        ft = get_flow_tracker()

        session_key = f"{channel}:{user_id}" if user_id else channel

        # 直前のユニットとの会話継続判定
        await ft.emit("SESSION", "active", {"session_key": session_key}, flow_id)
        prev_unit = self._get_session(session_key)
        if prev_unit and prev_unit != "chat" and self._is_continuation(user_input):
            log.info("Continuing with unit: %s (trace=%s)", prev_unit, trace_id)
            self._set_session(session_key, prev_unit)
            await ft.emit("SESSION", "done", {"continued": True, "unit": prev_unit}, flow_id)
            await ft.emit("REUSE", "done", {"unit": prev_unit}, flow_id)
            await ft.emit("UNIT_DECIDE", "done", {"unit": prev_unit}, flow_id)
            return {"unit": prev_unit, "message": user_input, "user_id": user_id}

        await ft.emit("SESSION", "done", {"continued": False}, flow_id)
        await ft.emit("ROUTE_LLM", "active", {}, flow_id)

        user_only = [r for r in (conversation_context or []) if r["role"] == "user"]
        context_block = self._format_context(user_only)
        prompt = _ROUTE_PROMPT_TEMPLATE.format(
            units_text=self._build_units_text(),
            user_input=user_input,
            context_block=context_block,
        )

        try:
            result = await self.llm.extract_json(prompt)
            if "unit" not in result:
                raise ValueError("Missing 'unit' key")
            unit_name = result["unit"]
            log.info("Routed to: %s (trace=%s)", unit_name, trace_id)
            self._set_session(session_key, unit_name)
            await ft.emit("ROUTE_LLM", "done", {"unit": unit_name}, flow_id)
            await ft.emit("UNIT_DECIDE", "done", {"unit": unit_name}, flow_id)
            return {"unit": unit_name, "message": user_input, "user_id": user_id}
        except Exception as e:
            log.warning("Routing failed (%s), falling back to chat (trace=%s)", e, trace_id)
            self._set_session(session_key, "chat")
            await ft.emit("ROUTE_LLM", "error", {"error": str(e)}, flow_id)
            await ft.emit("UNIT_DECIDE", "done", {"unit": "chat", "fallback": True}, flow_id)
            return {"unit": "chat", "message": user_input, "user_id": user_id}
