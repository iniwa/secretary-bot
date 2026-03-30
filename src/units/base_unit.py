"""BaseUnit — 全ユニットの基底クラス。discord.py の Cog を継承。"""

import os

from discord.ext import commands

from src.circuit_breaker import CircuitBreaker
from src.flow_tracker import get_flow_tracker
from src.llm.unit_llm import UnitLLM
from src.logger import get_logger

log = get_logger(__name__)


class BaseUnit(commands.Cog):
    UNIT_NAME: str = ""
    UNIT_DESCRIPTION: str = ""
    DELEGATE_TO: str | None = None
    PREFERRED_AGENT: str | None = None

    def __init__(self, bot):
        self.bot = bot
        self._breaker = CircuitBreaker(name=self.UNIT_NAME)
        self._admin_channel_id = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0"))
        # セッション終了フラグ（execute内でTrueにするとルーターのセッションがクリアされる）
        self.session_done = False
        # セッション継続用: チャネルごとの直前のやり取りを保持
        self._last_exchange: dict[str, dict] = {}

        # ユニット別LLMファサード
        unit_cfg = bot.config.get("units", {}).get(self.UNIT_NAME, {})
        self.llm = UnitLLM.from_config(
            bot.llm_router,
            unit_config=unit_cfg,
            global_config=bot.config,
            purpose="conversation",
        )

    async def execute(self, ctx, parsed: dict) -> str | None:
        """ユニットの主処理。サブクラスでオーバーライドする。

        サブクラスは _do_execute() をオーバーライドしてください。
        """
        raise NotImplementedError

    async def on_heartbeat(self) -> None:
        """ハートビート時に呼ばれる。必要に応じてオーバーライド。"""
        pass

    # --- Discord通知ヘルパー ---

    async def notify(self, message: str) -> None:
        if self._admin_channel_id:
            channel = self.bot.get_channel(self._admin_channel_id)
            if channel:
                await channel.send(message)

    async def notify_user(self, message: str, user_id: str = "") -> None:
        """ユーザーメンション付きで管理チャンネルに通知する。"""
        if user_id and user_id != "webgui":
            message = f"<@{user_id}> {message}"
        await self.notify(message)

    async def notify_error(self, message: str) -> None:
        await self.notify(f"[Error] {message}")

    # --- キャラクター変換 ---

    async def personalize(self, raw_result: str, user_message: str, flow_id: str | None = None) -> str:
        """Ollama稼働中のみペルソナを注入して返答を生成。省エネ時は定型文をそのまま返す。"""
        ft = get_flow_tracker()
        await ft.emit("PERSONA", "active", {}, flow_id)
        if not self.bot.llm_router.ollama_available:
            await ft.emit("PERSONA", "done", {"skipped": True}, flow_id)
            await ft.emit("SKIP_PERSONA", "done", {}, flow_id)
            return raw_result
        persona = self.bot.config.get("character", {}).get("persona", "")
        if not persona:
            await ft.emit("PERSONA", "done", {"skipped": True, "reason": "no_persona"}, flow_id)
            await ft.emit("SKIP_PERSONA", "done", {}, flow_id)
            return raw_result
        await ft.emit("PERSONA_GEN", "active", {}, flow_id)
        system = (
            f"{persona}\n\n"
            "以下の処理結果をユーザーに伝えてください。"
            "内容・事実は変えず、キャラクターらしい口調で自然に伝えてください。"
        )
        prompt = (
            f"ユーザーの発言: {user_message}\n"
            f"処理結果: {raw_result}\n\n"
            "この処理結果をキャラクターらしい口調でユーザーに伝えてください。"
        )
        result = await self.llm.generate(prompt, system=system)
        await ft.emit("PERSONA_GEN", "done", {}, flow_id)
        return result

    async def personalize_list(self, formatted_list: str, user_message: str, flow_id: str | None = None) -> str:
        """リスト表示用: 整形済みリストを壊さず、前置きコメントをペルソナで生成して結合する。"""
        ft = get_flow_tracker()
        await ft.emit("PERSONA", "active", {}, flow_id)
        if not self.bot.llm_router.ollama_available:
            await ft.emit("PERSONA", "done", {"skipped": True}, flow_id)
            return formatted_list
        persona = self.bot.config.get("character", {}).get("persona", "")
        if not persona:
            await ft.emit("PERSONA", "done", {"skipped": True, "reason": "no_persona"}, flow_id)
            return formatted_list
        await ft.emit("PERSONA_GEN", "active", {}, flow_id)
        system = (
            f"{persona}\n\n"
            "ユーザーのリスト表示リクエストに対して、リストを渡す前の一言コメントだけを生成してください。"
            "リストの内容そのものは出力しないでください。1〜2文で簡潔に。"
        )
        prompt = (
            f"ユーザーの発言: {user_message}\n\n"
            "このリスト表示に対するキャラクターらしい短い前置きコメントを生成してください。"
            "（リスト本体は別途表示されます）"
        )
        intro = await self.llm.generate(prompt, system=system)
        await ft.emit("PERSONA_GEN", "done", {}, flow_id)
        return f"{intro}\n{formatted_list}"

    # --- セッション文脈 ---

    def save_exchange(self, channel: str, user_msg: str, bot_response: str) -> None:
        """直前のやり取りを保存（セッション継続時に文脈として使う）。"""
        self._last_exchange[channel] = {"user": user_msg, "bot": bot_response}

    def get_context(self, channel: str) -> str:
        """直前のやり取りがあればプロンプト用の文脈テキストを返す。"""
        ex = self._last_exchange.get(channel)
        if not ex:
            return ""
        return f"\n## 直前のやり取り（文脈）\nユーザー: {ex['user']}\nアシスタント: {ex['bot']}\n"

    def clear_exchange(self, channel: str) -> None:
        self._last_exchange.pop(channel, None)

    # --- サーキットブレーカー ---

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker
