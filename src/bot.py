"""エントリーポイント — グレースフルシャットダウン含む。"""

import asyncio
import os
import signal
import subprocess
import time

import discord
import yaml
from discord.ext import commands

from src.activity.collector import ActivityCollector
from src.activity.detector import ActivityDetector
from src.database import Database
from src.flow_tracker import get_flow_tracker
from src.heartbeat import Heartbeat
from src.inner_mind.actuator import Actuator
from src.inner_mind.approval_view import (
    PersistentApprovalView,
    dispatch_persistent_interaction,
)
from src.llm.router import LLMRouter
from src.logger import get_logger, setup_logging
from src.memory.chroma_client import ChromaMemory
from src.memory.people_memory import PeopleMemory
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
    except Exception as e:
        log.warning("Failed to get commit hash: %s", e)
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
        intents.presences = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        self.config = config
        data_dir = os.path.join(BASE_DIR, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.database = Database(path=os.path.join(data_dir, "bot.db"))
        self.llm_router = LLMRouter(config)
        self.chroma = ChromaMemory(path=os.path.join(data_dir, "chromadb"))
        self.people_memory = PeopleMemory(self)
        self.unit_router = UnitRouter(self)
        self.heartbeat = Heartbeat(self)
        self.inner_mind = self.heartbeat.inner_mind
        self.actuator = Actuator(self)
        self.unit_manager = UnitManager(self)
        self.activity_detector = ActivityDetector(self, config)
        self.unit_manager.agent_pool.set_activity_detector(self.activity_detector)
        self.activity_collector = ActivityCollector(self)
        from src.status_collector import StatusCollector
        self.status_collector = StatusCollector(self)
        self._admin_channel_id = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0"))
        # ボットメッセージID → ユニット名マッピング（返信ルーティング用）
        self._reply_units: dict[int, str] = {}
        # チャネル+ユーザーごとのメッセージ処理ロック（直列化）
        self._user_locks: dict[str, asyncio.Lock] = {}

    async def setup_hook(self) -> None:
        # Discord接続時に呼ばれるが、main()で既に初期化済みなのでスキップ
        # persistent_view 登録（approval:* custom_id を受けるため）
        try:
            self.add_view(PersistentApprovalView())
        except Exception as e:
            log.debug("add_view failed: %s", e)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # approval:ok:<id> / approval:ng:<id> を拾う
        try:
            handled = await dispatch_persistent_interaction(self, interaction)
            if handled:
                return
        except Exception as e:
            log.warning("approval interaction dispatch failed: %s", e)

    async def _fetch_discord_history(
        self, message: discord.Message, minutes: int, limit: int,
    ) -> list[dict]:
        """Discordチャンネルから直近のメッセージ履歴を取得する。"""
        from datetime import datetime, timedelta, timezone
        after = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        history: list[dict] = []
        try:
            async for msg in message.channel.history(limit=limit + 1, after=after, oldest_first=True):
                if msg.id == message.id:
                    continue  # 現在のメッセージは除外
                if not msg.content.strip():
                    continue
                # メンション部分を除去
                text = msg.content
                if self.user:
                    text = text.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
                if not text:
                    continue
                role = "assistant" if msg.author.id == self.user.id else "user"
                name = msg.author.display_name if role == "user" else None
                entry = {"role": role, "content": text}
                if name:
                    entry["name"] = name
                history.append(entry)
        except Exception as e:
            log.warning("Failed to fetch Discord history: %s", e)
        return history[-limit:]

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
        channel_name = "" if is_dm else getattr(message.channel, "name", "")

        # ボットメッセージへの返信を検出（返信ルーティング用）
        reply_unit = None
        is_bot_reply = False
        if message.reference and message.reference.message_id:
            ref_id = message.reference.message_id
            reply_unit = self._reply_units.get(ref_id)
            if reply_unit:
                is_bot_reply = True
            else:
                # メモリにない場合はキャッシュからボットのメッセージか確認
                ref_msg = message.reference.resolved
                if ref_msg and ref_msg.author.id == self.user.id:
                    is_bot_reply = True

        # DMでなく、メンションなしのメッセージは会話ログのみ保存して終了
        # ただし、ボットメッセージへの返信は処理を続行
        if not is_dm and self.user not in message.mentions and not is_bot_reply:
            await self.database.log_conversation(channel_tag, "user", content, user_id=user_id, channel_name=channel_name)
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
            await self.database.log_conversation(channel_tag, "user", content, user_id=user_id, channel_name=channel_name)

            # 直近の会話履歴を取得（ルーティング・ユニット実行の文脈として使う）
            history_minutes = self.config.get("units", {}).get("chat", {}).get("history_minutes", 60)
            history_limit = self.config.get("units", {}).get("chat", {}).get("history_limit", 8)
            conversation_context = await self._fetch_discord_history(
                message, history_minutes, history_limit,
            )

            # Unit Router（typing表示中に処理）
            async with message.channel.typing():
                # ボットメッセージへの返信 → chatを除く機能ユニットはLLMルーティングをバイパス
                if reply_unit and reply_unit != "chat":
                    unit_name = reply_unit
                    user_message = content
                    log.info("Reply-based routing to: %s", unit_name)
                    await ft.emit("UNIT_DECIDE", "done", {"unit": unit_name, "reply": True}, flow_id)
                else:
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
                sent = await message.channel.send(response)
                # chatは汎用フォールバックなのでバイパス対象外、記録しない
                if unit_name != "chat":
                    self._reply_units[sent.id] = unit_name
                mode = "eco" if not self.llm_router.ollama_available else "normal"
                await self.database.log_conversation(channel_tag, "assistant", response, mode=mode, unit=unit_name, user_id=user_id, channel_name=channel_name)
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
        for name, coro in [
            ("heartbeat", lambda: asyncio.to_thread(self.heartbeat.shutdown)),
            ("activity_collector", self.activity_collector.stop_polling),
            ("activity_detector", self.activity_detector.close),
            ("agent_pool", self.unit_manager.agent_pool.close),
            ("database", self.database.close),
            ("discord", self.close),
        ]:
            try:
                await coro()
            except Exception as e:
                log.error("Shutdown error in %s: %s", name, e)
        log.info("シャットダウン完了")


# config.yaml → DB settings への初期シード対象
# ネストは "." で flatten。リスト/dict 値は JSON で保存。
# 構造が複雑で GUI 化の範囲外なものは含めない（windows_agents, rss.presets,
# docker_monitor.containers, units.*.enabled, wol, metrics, debug, tools 等）。
_SEED_KEYS: tuple[tuple[str, ...], ...] = (
    ("llm", "ollama_model"),
    ("llm", "ollama_url"),
    ("llm", "ollama_timeout"),
    ("llm", "gemini_model"),
    ("gemini", "conversation"),
    ("gemini", "memory_extraction"),
    ("gemini", "unit_routing"),
    ("gemini", "monthly_token_limit"),
    ("heartbeat", "interval_with_ollama_minutes"),
    ("heartbeat", "interval_without_ollama_minutes"),
    ("heartbeat", "compact_threshold_messages"),
    ("inner_mind", "enabled"),
    ("inner_mind", "thinking_interval_ticks"),
    ("inner_mind", "min_speak_interval_minutes"),
    ("inner_mind", "speak_channel_id"),
    ("inner_mind", "target_user_id"),
    ("inner_mind", "active_threshold_minutes"),
    ("inner_mind", "github", "username"),
    ("inner_mind", "github", "lookback_hours"),
    ("inner_mind", "github", "max_items"),
    ("inner_mind", "tavily_news", "max_results_per_query"),
    ("inner_mind", "tavily_news", "lookback_days"),
    ("inner_mind", "tavily_news", "topic"),
    ("character", "name"),
    ("character", "persona"),
    ("character", "ollama_only"),
    ("rss", "fetch_interval_minutes"),
    ("rss", "digest_hour"),
    ("rss", "article_retention_days"),
    ("rss", "max_articles_per_category"),
    ("weather", "default_location"),
    ("weather", "umbrella_threshold"),
    ("searxng", "url"),
    ("searxng", "max_results"),
    ("searxng", "fetch_pages"),
    ("searxng", "max_chars_per_page"),
    ("rakuten_search", "max_results"),
    ("rakuten_search", "fetch_details"),
    ("rakuten_search", "detail_concurrency"),
    ("rakuten_search", "detail_max_desc_chars"),
    ("stt", "enabled"),
    ("stt", "polling_interval_minutes"),
    ("stt", "processing", "summary_threshold_chars"),
    ("delegation", "thresholds", "cpu_percent"),
    ("delegation", "thresholds", "memory_percent"),
    ("delegation", "thresholds", "gpu_percent"),
    ("activity", "enabled"),
    ("activity", "block_rules", "obs_streaming"),
    ("activity", "block_rules", "obs_recording"),
    ("activity", "block_rules", "obs_replay_buffer"),
    ("activity", "block_rules", "gaming_on_main"),
    ("activity", "block_rules", "discord_vc"),
    ("docker_monitor", "enabled"),
    ("docker_monitor", "check_interval_seconds"),
    ("docker_monitor", "cooldown_minutes"),
    ("docker_monitor", "max_lines_per_check"),
    ("memory", "sweep_enabled"),
    ("memory", "sweep_stale_days"),
)


def _dig(d: dict, path: tuple[str, ...]):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _serialize_setting(val) -> str:
    """スカラは素の文字列で保存（既存 _restore_settings の互換維持）。
    bool は 'true'/'false'、数値は str()、dict/list は JSON 文字列。"""
    import json as _json
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return val
    return _json.dumps(val, ensure_ascii=False)


async def _seed_settings_from_config(bot: "SecretaryBot") -> None:
    """初回起動時に config.yaml の内容を settings テーブルへ書き写す。
    既に DB に存在するキーは上書きしない。"""
    version_key = "_seed_version"
    current_version = "1"
    saved_version = await bot.database.get_setting(version_key)
    if saved_version == current_version:
        return

    cfg = bot.config
    written = 0
    for path in _SEED_KEYS:
        val = _dig(cfg, path)
        if val is None:
            continue
        key = ".".join(path)
        existing = await bot.database.get_setting(key)
        if existing is not None:
            continue
        await bot.database.set_setting(key, _serialize_setting(val))
        written += 1

    await bot.database.set_setting(version_key, current_version)
    log.info("Seeded %d settings from config.yaml (version=%s)", written, current_version)


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

    # InnerMind設定
    im_settings = await bot.database.get_all_settings("inner_mind.")
    if im_settings:
        im_cfg = bot.config.setdefault("inner_mind", {})
        for key, value in im_settings.items():
            short_key = key.removeprefix("inner_mind.")
            if short_key == "enabled":
                im_cfg[short_key] = value == "true"
            elif short_key in ("speak_probability",):
                try:
                    im_cfg[short_key] = float(value)
                except ValueError:
                    im_cfg[short_key] = value
            elif short_key in ("min_speak_interval_minutes",):
                try:
                    im_cfg[short_key] = int(value)
                except ValueError:
                    im_cfg[short_key] = value
            else:
                im_cfg[short_key] = value
        log.info("Restored inner_mind settings from DB")

    # ペルソナ
    saved_persona = await bot.database.get_setting("character.persona")
    if saved_persona is not None:
        bot.config.setdefault("character", {})["persona"] = saved_persona
        log.info("Restored persona from DB")

    # 汎用 domain 復元（seed 対応 domain をまとめて config に反映）
    # キャラクター/Chat/Weather/SearXNG/STT/RSS/Activity/Docker Monitor/Memory/Delegation thresholds
    def _coerce(val: str):
        # bool → 'true'/'false'
        if val == "true":
            return True
        if val == "false":
            return False
        # 数値
        try:
            if "." in val:
                return float(val)
            return int(val)
        except (ValueError, TypeError):
            pass
        # JSON
        try:
            return _json.loads(val)
        except (ValueError, _json.JSONDecodeError, TypeError):
            return val

    # 単純 domain（prefix → config セクション）
    _GENERIC_DOMAINS = (
        ("weather.", "weather"),
        ("searxng.", "searxng"),
        ("stt.", "stt"),
        ("rss.", "rss"),
        ("activity.", "activity"),
        ("docker_monitor.", "docker_monitor"),
        ("memory.", "memory"),
        ("delegation.", "delegation"),
        ("character.", "character"),
    )
    for prefix, root in _GENERIC_DOMAINS:
        flat = await bot.database.get_all_settings(prefix)
        if not flat:
            continue
        section = bot.config.setdefault(root, {})
        for key, value in flat.items():
            path = key.removeprefix(prefix).split(".")
            cur = section
            for seg in path[:-1]:
                cur = cur.setdefault(seg, {})
            cur[path[-1]] = _coerce(value)
        log.info("Restored %s settings from DB", root)


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
    await _seed_settings_from_config(bot)
    await _restore_settings(bot)
    await bot.llm_router.check_ollama()
    await bot.unit_manager.load_units()
    await bot.activity_collector.restore_open_sessions()
    await bot.activity_collector.start_polling()
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
