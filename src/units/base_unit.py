"""BaseUnit — 全ユニットの基底クラス。discord.py の Cog を継承。"""

import os

from discord.ext import commands

from src.circuit_breaker import CircuitBreaker
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

        # ユニット別LLMファサード
        unit_cfg = bot.config.get("units", {}).get(self.UNIT_NAME, {})
        self.llm = UnitLLM.from_config(
            bot.llm_router,
            unit_config=unit_cfg,
            global_config=bot.config,
            purpose="conversation",
        )

    async def execute(self, ctx, parsed: dict) -> str | None:
        """ユニットの主処理。サブクラスでオーバーライドする。"""
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

    async def notify_error(self, message: str) -> None:
        await self.notify(f"[Error] {message}")

    # --- キャラクター変換 ---

    async def personalize(self, raw_result: str, user_message: str) -> str:
        """Ollama稼働中のみペルソナを注入して返答を生成。省エネ時は定型文をそのまま返す。"""
        if not self.bot.llm_router.ollama_available:
            return raw_result
        persona = self.bot.config.get("character", {}).get("persona", "")
        if not persona:
            return raw_result
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
        return await self.llm.generate(prompt, system=system)

    # --- サーキットブレーカー ---

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker
