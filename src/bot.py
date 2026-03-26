"""エントリーポイント — グレースフルシャットダウン含む。"""

import asyncio
import os
import signal
import subprocess
import time

import discord
import yaml
from discord.ext import commands

from src.database import Database
from src.heartbeat import Heartbeat
from src.llm.router import LLMRouter
from src.logger import get_logger, setup_logging
from src.memory.chroma_client import ChromaMemory
from src.skill_router import SkillRouter
from src.units import UnitManager
from src.web.app import create_web_app

log = get_logger(__name__)

_start_time = time.monotonic()

# Docker: /app, ローカル: batが設定する作業ディレクトリ
BASE_DIR = os.environ.get("BOT_BASE_DIR", "/app")


def get_commit_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.join(BASE_DIR, "src") if os.path.isdir(os.path.join(BASE_DIR, "src")) else BASE_DIR,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def get_uptime_seconds() -> float:
    return time.monotonic() - _start_time


def load_config(path: str | None = None) -> dict:
    if path is None:
        path = os.path.join(BASE_DIR, "config.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class SecretaryBot(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.config = config
        data_dir = os.path.join(BASE_DIR, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.database = Database(path=os.path.join(data_dir, "bot.db"))
        self.llm_router = LLMRouter(config)
        self.chroma = ChromaMemory(path=os.path.join(data_dir, "chromadb"))
        self.skill_router = SkillRouter(self)
        self.heartbeat = Heartbeat(self)
        self.unit_manager = UnitManager(self)
        self._admin_channel_id = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0"))

    async def setup_hook(self) -> None:
        await self.database.connect()
        await self.llm_router.check_ollama()
        await self.unit_manager.load_units()
        self.heartbeat.start()
        log.info("Bot setup complete")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        # スラッシュコマンド処理
        await self.process_commands(message)
        # 自然言語処理（コマンドでなければ）
        ctx = await self.get_context(message)
        if ctx.valid:
            return

        content = message.content.strip()
        if not content:
            return

        # 会話ログ保存
        await self.database.log_conversation("discord", "user", content)

        # Skill Router
        result = await self.skill_router.route(content)
        skill_name = result.get("skill", "chat")
        parsed = result.get("parsed", {})

        unit = self.unit_manager.get(skill_name)
        if unit is None:
            unit = self.unit_manager.get("chat")

        try:
            response = await unit.execute(ctx, parsed)
            if response:
                await message.channel.send(response)
                mode = "eco" if not self.llm_router.ollama_available else "normal"
                await self.database.log_conversation("discord", "assistant", response, mode=mode, unit=skill_name)
        except Exception as e:
            log.error("Unit execution failed: %s", e, exc_info=True)
            await message.channel.send("ごめんなさい、処理中にエラーが発生しました。")

    async def notify_admin(self, message: str) -> None:
        if self._admin_channel_id:
            channel = self.get_channel(self._admin_channel_id)
            if channel:
                await channel.send(f"[管理通知] {message}")

    async def graceful_shutdown(self) -> None:
        log.info("シャットダウン開始...")
        self.heartbeat.shutdown()
        await self.database.close()
        await self.close()
        log.info("シャットダウン完了")


async def _run_web(bot: SecretaryBot) -> None:
    import uvicorn
    app = create_web_app(bot)
    port = int(os.environ.get("WEBGUI_PORT", "8100"))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    config = load_config()
    setup_logging(verbose=config.get("debug", {}).get("verbose_logging", False))

    bot = SecretaryBot(config)
    token = os.environ["DISCORD_BOT_TOKEN"]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.graceful_shutdown()))
        except NotImplementedError:
            pass  # Windows

    # WebGUIとBotを並行起動
    await asyncio.gather(
        bot.start(token),
        _run_web(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
