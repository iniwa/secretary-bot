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
from src.flow_tracker import get_flow_tracker
from src.heartbeat import Heartbeat
from src.llm.router import LLMRouter
from src.logger import get_logger, setup_logging
from src.memory.chroma_client import ChromaMemory
from src.unit_router import UnitRouter
from src.units import UnitManager
from src.web.app import create_web_app

log = get_logger(__name__)

_start_time = time.monotonic()

# Docker: /app, ローカル: batが設定する作業ディレクトリ
BASE_DIR = os.environ.get("BOT_BASE_DIR", "/app")


def _git_dir() -> str:
    env = os.environ.get("GIT_REPO_DIR")
    if env:
        return env
    src_dir = os.path.join(BASE_DIR, "src")
    return src_dir if os.path.isdir(os.path.join(src_dir, ".git")) else BASE_DIR


def get_commit_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_git_dir(),
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
        self.unit_router = UnitRouter(self)
        self.heartbeat = Heartbeat(self)
        self.unit_manager = UnitManager(self)
        self._admin_channel_id = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0"))
        # チャネル+ユーザーごとのメッセージ処理ロック（直列化）
        self._user_locks: dict[str, asyncio.Lock] = {}

    async def setup_hook(self) -> None:
        # Discord接続時に呼ばれるが、main()で既に初期化済みなのでスキップ
        pass

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

        user_id = str(message.author.id)
        is_dm = isinstance(message.channel, discord.DMChannel)
        channel_tag = "discord_dm" if is_dm else "discord"

        # DMでなく、メンションなしのメッセージは会話ログのみ保存して終了
        if not is_dm and self.user not in message.mentions:
            await self.database.log_conversation(channel_tag, "user", content, user_id=user_id)
            return

        # メンション部分をテキストから除去（DMではメンション不要だが念のため）
        content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
        if not content:
            return

        # ユーザーごとにロックを取得（同一ユーザーのメッセージを直列化）
        lock_key = f"{channel_tag}:{user_id}"
        if lock_key not in self._user_locks:
            self._user_locks[lock_key] = asyncio.Lock()

        async with self._user_locks[lock_key]:
            ft = get_flow_tracker()
            flow_id = await ft.start_flow()
            await ft.emit("MSG", "done", {"content": content[:80], "channel": channel_tag}, flow_id)
            await ft.emit("LOCK", "done", {"user_id": user_id}, flow_id)

            # 会話ログ保存
            await self.database.log_conversation(channel_tag, "user", content, user_id=user_id)

            # 直近の会話履歴を取得（ルーティング・ユニット実行の文脈として使う）
            history_minutes = self.config.get("units", {}).get("chat", {}).get("history_minutes", 60)
            recent_rows = await self.database.get_recent_channel_messages(
                channel_tag, limit=6, user_id=user_id,
                minutes=history_minutes,
            )
            # 現在のメッセージは既にログ保存済みなので除外
            conversation_context = [
                r for r in recent_rows if r["content"] != content
            ][-4:]  # 直近4件（2往復分）

            # Unit Router（typing表示中に処理）
            async with message.channel.typing():
                result = await self.unit_router.route(content, channel=channel_tag, user_id=user_id, flow_id=flow_id, conversation_context=conversation_context)
                unit_name = result.get("unit", "chat")
                user_message = result.get("message", content)

                unit = self.unit_manager.get(unit_name)
                if unit is None:
                    unit = self.unit_manager.get("chat")

                # chatユニットは会話キャッチボールのため全履歴、それ以外はユーザー発言のみ
                if unit_name == "chat":
                    exec_context = conversation_context
                else:
                    exec_context = [r for r in conversation_context if r["role"] == "user"]

                try:
                    actual_unit = getattr(unit, "unit", unit)
                    actual_unit.session_done = False
                    response = await unit.execute(ctx, {"message": user_message, "channel": lock_key, "user_id": user_id, "flow_id": flow_id, "conversation_context": exec_context})
                    if actual_unit.session_done:
                        self.unit_router.clear_session(channel_tag, user_id)
                        actual_unit.clear_exchange(lock_key)
                        await ft.emit("SESSION_UPDATE", "done", {"action": "cleared"}, flow_id)
                    elif response:
                        actual_unit.save_exchange(lock_key, user_message, response)
                        self.unit_router.refresh_session(channel_tag, user_id)
                        await ft.emit("SESSION_UPDATE", "done", {"action": "saved"}, flow_id)
                except Exception as e:
                    log.error("Unit execution failed: %s", e, exc_info=True)
                    response = "ごめんなさい、処理中にエラーが発生しました。"
                    await ft.emit("SESSION_UPDATE", "error", {"error": str(e)}, flow_id)

            if response:
                await message.channel.send(response)
                mode = "eco" if not self.llm_router.ollama_available else "normal"
                await self.database.log_conversation(channel_tag, "assistant", response, mode=mode, unit=unit_name, user_id=user_id)
                await ft.emit("DB_LOG", "done", {"mode": mode, "unit": unit_name}, flow_id)
                await ft.emit("REPLY", "done", {"channel": channel_tag}, flow_id)
            await ft.end_flow(flow_id)

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


async def _restore_settings(bot: SecretaryBot) -> None:
    """DBに保存されたWebGUI設定をconfigに復元する。"""
    import json as _json
    saved = await bot.database.get_all_settings("gemini.")
    if saved:
        gemini_cfg = bot.config.setdefault("gemini", {})
        for key, value in saved.items():
            short_key = key.removeprefix("gemini.")
            try:
                gemini_cfg[short_key] = _json.loads(value)
            except (ValueError, _json.JSONDecodeError):
                gemini_cfg[short_key] = value
        bot.llm_router._gemini_config = gemini_cfg
        log.info("Restored gemini settings from DB")

    # LLMモデル設定
    saved_gemini_model = await bot.database.get_setting("llm.gemini_model")
    if saved_gemini_model:
        bot.config.setdefault("llm", {})["gemini_model"] = saved_gemini_model
        bot.llm_router.gemini.model = saved_gemini_model
        log.info("Restored gemini model from DB: %s", saved_gemini_model)

    saved_model = await bot.database.get_setting("llm.ollama_model")
    if saved_model:
        bot.config.setdefault("llm", {})["ollama_model"] = saved_model
        bot.llm_router.ollama.model = saved_model
        log.info("Restored ollama model from DB: %s", saved_model)

    saved_timeout = await bot.database.get_setting("llm.ollama_timeout")
    if saved_timeout:
        t = int(saved_timeout)
        bot.config.setdefault("llm", {})["ollama_timeout"] = t
        bot.llm_router.ollama.timeout = t
        log.info("Restored ollama timeout from DB: %s", t)

    unit_llm = await bot.database.get_all_settings("unit_llm.")
    if unit_llm:
        for key, value in unit_llm.items():
            unit_name = key.removeprefix("unit_llm.")
            ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
            ucfg.setdefault("llm", {})["ollama_model"] = value
        log.info("Restored unit llm models from DB")

    # ユニット別Gemini許可設定
    unit_gemini = await bot.database.get_all_settings("unit_gemini.")
    if unit_gemini:
        for key, value in unit_gemini.items():
            unit_name = key.removeprefix("unit_gemini.")
            allowed = value == "true"
            ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
            ucfg.setdefault("llm", {})["gemini_allowed"] = allowed
        log.info("Restored unit gemini settings from DB")

    # ハートビート設定
    hb_settings = await bot.database.get_all_settings("heartbeat.")
    if hb_settings:
        hb_cfg = bot.config.setdefault("heartbeat", {})
        for key, value in hb_settings.items():
            short_key = key.removeprefix("heartbeat.")
            try:
                hb_cfg[short_key] = int(value)
            except ValueError:
                hb_cfg[short_key] = value
        log.info("Restored heartbeat settings from DB")

    # 楽天検索設定
    rakuten_settings = await bot.database.get_all_settings("rakuten_search.")
    if rakuten_settings:
        r_cfg = bot.config.setdefault("rakuten_search", {})
        for key, value in rakuten_settings.items():
            short_key = key.removeprefix("rakuten_search.")
            try:
                r_cfg[short_key] = _json.loads(value)
            except (ValueError, _json.JSONDecodeError):
                r_cfg[short_key] = value
        log.info("Restored rakuten_search settings from DB")

    # 委託モード
    delegation_modes = await bot.database.get_all_settings("delegation_mode.")
    if delegation_modes:
        for key, value in delegation_modes.items():
            agent_id = key.removeprefix("delegation_mode.")
            bot.unit_manager.agent_pool.set_mode(agent_id, value)
        log.info("Restored delegation modes from DB")

    # 会話履歴設定
    chat_settings = await bot.database.get_all_settings("units.chat.")
    if chat_settings:
        chat_cfg = bot.config.setdefault("units", {}).setdefault("chat", {})
        for key, value in chat_settings.items():
            short_key = key.removeprefix("units.chat.")
            try:
                chat_cfg[short_key] = _json.loads(value)
            except (ValueError, _json.JSONDecodeError):
                chat_cfg[short_key] = value
        log.info("Restored chat settings from DB")

    # ペルソナ
    saved_persona = await bot.database.get_setting("character.persona")
    if saved_persona is not None:
        bot.config.setdefault("character", {})["persona"] = saved_persona
        log.info("Restored persona from DB")


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
    token = os.environ.get("DISCORD_BOT_TOKEN", "")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.graceful_shutdown()))
        except NotImplementedError:
            pass  # Windows

    # DB/LLM/Unit の初期化（Discord接続前に実行）
    await bot.database.connect()
    bot.llm_router.set_database(bot.database)
    await _restore_settings(bot)
    await bot.llm_router.check_ollama()
    await bot.unit_manager.load_units()
    await bot.heartbeat.sync_summaries_to_chroma()
    bot.heartbeat.start()
    await bot.heartbeat.restore_reminders()
    await bot.heartbeat.restore_weather_subscriptions()
    log.info("Bot setup complete")

    if token:
        # WebGUIとDiscord Botを並行起動
        log.info("Starting with Discord + WebGUI")
        await asyncio.gather(
            bot.start(token),
            _run_web(bot),
        )
    else:
        # Discord Token なし → WebGUIのみ起動
        log.info("No DISCORD_BOT_TOKEN set, starting WebGUI only")
        await _run_web(bot)


if __name__ == "__main__":
    asyncio.run(main())
