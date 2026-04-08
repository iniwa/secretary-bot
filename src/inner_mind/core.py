"""InnerMind — ミミの自律思考サイクル。"""

import hashlib
import json
import random
import re
import uuid
from datetime import datetime, timedelta, timezone

from src.database import JST, jst_now
from src.errors import AllLLMsUnavailableError
from src.inner_mind.context_sources import ContextSourceRegistry
from src.inner_mind.context_sources.conversation import ConversationSource
from src.inner_mind.context_sources.memo import MemoSource
from src.inner_mind.context_sources.memory import MemorySource
from src.inner_mind.context_sources.reminder import ReminderSource
from src.inner_mind.context_sources.weather import WeatherSource
from src.inner_mind.prompts import SPEAK_PROMPT, SPEAK_SYSTEM, THINK_PROMPT, THINK_SYSTEM
from src.logger import get_logger

log = get_logger(__name__)


class InnerMind:
    """ミミの自律思考エンジン。"""

    def __init__(self, bot):
        self.bot = bot
        self.registry = ContextSourceRegistry()

        # 初期ソース登録
        self.registry.register(ConversationSource(bot))
        self.registry.register(MemoSource(bot))
        self.registry.register(ReminderSource(bot))
        self.registry.register(MemorySource(bot))
        self.registry.register(WeatherSource(bot))

    def register_source(self, source) -> None:
        """外部からコンテキストソースを追加する。"""
        self.registry.register(source)

    def _get_config(self) -> dict:
        return self.bot.config.get("inner_mind", {})

    async def _get_setting(self, key: str, default=None):
        """DB settings 優先 → config フォールバック。"""
        db_val = await self.bot.database.get_setting(f"inner_mind.{key}")
        if db_val is not None:
            return db_val
        return self._get_config().get(key, default)

    async def _get_setting_float(self, key: str, default: float = 0.0) -> float:
        val = await self._get_setting(key, default)
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    async def _get_setting_int(self, key: str, default: int = 0) -> int:
        val = await self._get_setting(key, default)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    # --- メインエントリ ---

    async def think(self) -> None:
        """思考サイクルのメインエントリ。heartbeat からバックグラウンドで呼ばれる。"""
        # Ollama 必須チェック
        if not self.bot.llm_router.ollama_available:
            log.debug("InnerMind: Ollama unavailable, skipping think cycle")
            return

        enabled = await self._get_setting("enabled", False)
        if not _to_bool(enabled):
            return

        log.info("InnerMind: think cycle started")
        try:
            # コンテキスト収集
            context = await self._collect_context()

            # コンテキスト変化チェック（不変ならスキップ）
            ctx_key = (
                context["discord_status"]
                + "|".join(sr["text"][:200] for sr in context["sources"])
            )
            ctx_hash = hashlib.md5(ctx_key.encode()).hexdigest()
            if hasattr(self, "_last_ctx_hash") and self._last_ctx_hash == ctx_hash:
                log.info("InnerMind: context unchanged, skipping think cycle")
                return
            self._last_ctx_hash = ctx_hash

            # 思考フェーズ
            thought = await self._think_phase(context)
            if not thought:
                return

            # 保存
            monologue_id = await self._save_thought(thought)

            # 発言フェーズ
            await self._speak_phase(thought, monologue_id, context)
        except AllLLMsUnavailableError:
            log.warning("InnerMind: LLM unavailable during think cycle")
        except Exception:
            log.error("InnerMind: think cycle failed", exc_info=True)

    # --- コンテキスト収集 ---

    async def _collect_context(self) -> dict:
        """固定情報 + 全ContextSourceからコンテキストを収集。"""
        now = datetime.now(JST)
        weekdays = ["月", "火", "水", "木", "金", "土", "日"]
        dt_str = now.strftime("%Y-%m-%d %H:%M") + f"（{weekdays[now.weekday()]}曜日）"

        discord_status = await self._get_user_status()
        last_mono = await self.bot.database.get_last_monologue()
        self_model = await self.bot.database.get_self_model()

        last_monologue_text = last_mono["monologue"] if last_mono else ""

        # shared コンテキスト（各ソースに渡す）
        shared = {
            "last_monologue": last_monologue_text,
            "now": dt_str,
        }

        # 全ソースから収集
        source_results = await self.registry.collect_all(shared)

        # recent_summary を抽出（ConversationSource があれば）
        for sr in source_results:
            if sr["name"] == "最近の会話":
                msgs = sr["data"].get("messages", [])
                if msgs:
                    shared["recent_summary"] = msgs[0].get("content", "")[:200]
                break

        return {
            "datetime": dt_str,
            "discord_status": discord_status,
            "last_monologue": last_monologue_text,
            "self_model": self_model,
            "sources": source_results,
        }

    # --- 思考フェーズ ---

    async def _think_phase(self, context: dict) -> dict | None:
        """LLM呼び出し①: 思考プロンプトでモノローグを生成。"""
        persona = self.bot.config.get("character", {}).get("persona", "")

        # コンテキストソースをプロンプトセクションに変換
        sections = []
        for sr in context["sources"]:
            sections.append(f"[{sr['name']}]\n{sr['text']}")
        context_sections = "\n\n".join(sections) if sections else "（特になし）"

        # 自己モデルをテキスト化
        sm = context["self_model"]
        self_model_text = "\n".join(f"{k}: {v}" for k, v in sm.items()) if sm else "（未形成）"

        # 直近5件のモノローグ要約（重複思考防止用）
        recent_monos = await self.bot.database.get_monologues(limit=5)
        if recent_monos:
            mono_lines = []
            for m in recent_monos:
                mono_lines.append(f"- [{m['mood']}] {m['monologue'][:100]}")
            recent_monologues_text = "\n".join(mono_lines)
        else:
            recent_monologues_text = "（初回思考）"

        system = THINK_SYSTEM.format(persona=persona)
        prompt = THINK_PROMPT.format(
            datetime=context["datetime"],
            discord_status=context["discord_status"],
            context_sections=context_sections,
            recent_monologues=recent_monologues_text,
            self_model=self_model_text,
        )

        raw = await self.bot.llm_router.generate(
            prompt, system=system, purpose="inner_mind", ollama_only=True,
        )
        return self._parse_think_response(raw)

    # --- 思考保存 ---

    async def _save_thought(self, thought: dict) -> int:
        """モノローグ・自己モデル・記憶をDBに保存。"""
        monologue = thought.get("monologue", "")
        mood = thought.get("mood", "unknown")

        monologue_id = await self.bot.database.save_monologue(monologue, mood)

        # 自己モデル更新（mood）
        if mood and mood != "unknown":
            await self.bot.database.upsert_self_model("mood", mood)

        # interest_topic の保存
        interest = thought.get("interest_topic")
        if interest and interest != "null":
            await self.bot.database.upsert_self_model("interest_topic", interest)

        # memory_update があれば ChromaDB に保存（重複検出付き）
        mem_update = thought.get("memory_update")
        if mem_update and mem_update != "null":
            existing = self.bot.chroma.search("ai_memory", query=mem_update, n_results=1)
            if existing and existing[0].get("distance", 1.0) < 0.3:
                log.info("InnerMind: memory_update too similar to existing, skipped")
            else:
                doc_id = uuid.uuid4().hex[:16]
                self.bot.chroma.add(
                    "ai_memory", doc_id, mem_update,
                    {"source": "inner_mind", "created_at": jst_now()},
                )
                log.info("InnerMind: memory updated: %s", mem_update[:80])

        log.info("InnerMind: thought saved (mood=%s): %s", mood, monologue[:80])
        return monologue_id

    # --- 発言フェーズ ---

    async def _speak_phase(self, thought: dict, monologue_id: int, context: dict) -> None:
        """条件を満たした場合にDiscordに自発発言する。"""
        if not await self._check_speak_conditions(context):
            return

        message = await self._generate_message(thought, context)
        if message:
            await self._send_to_discord(message, monologue_id)

    async def _check_speak_conditions(self, context: dict) -> bool:
        """発言条件をチェック: インターバル × オンライン状態 × 確率。"""
        # 最低発言インターバル
        min_interval = await self._get_setting_int("min_speak_interval_minutes", 0)
        if min_interval > 0:
            last_speak = await self.bot.database.get_monologues(limit=1, did_notify_only=True)
            if last_speak:
                last_time = datetime.fromisoformat(last_speak[0]["created_at"])
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=JST)
                elapsed = (datetime.now(JST) - last_time).total_seconds() / 60
                if elapsed < min_interval:
                    log.debug("InnerMind: speak skipped (interval: %.0f < %d min)", elapsed, min_interval)
                    return False

        # Discord ステータス
        status = context.get("discord_status", "online")
        if status in ("offline", "dnd"):
            log.debug("InnerMind: speak skipped (user status: %s)", status)
            return False

        # 確率判定
        prob = await self._get_setting_float("speak_probability", 0.20)
        roll = random.random()
        if roll >= prob:
            log.debug("InnerMind: speak skipped (probability: %.2f >= %.2f)", roll, prob)
            return False

        return True

    async def _generate_message(self, thought: dict, context: dict) -> str | None:
        """LLM呼び出し②: 発言メッセージを生成。"""
        persona = self.bot.config.get("character", {}).get("persona", "")

        # 直近の自発発言を取得（重複防止）
        recent_speaks = await self.bot.database.get_monologues(limit=5, did_notify_only=True)
        recent_speaks_section = ""
        if recent_speaks:
            lines = ["[最近の自発発言（同じ話題を繰り返さないこと）]"]
            for s in recent_speaks:
                lines.append(f"- {s['notified_message']}")
            recent_speaks_section = "\n".join(lines)

        # 直近会話テキスト
        recent_conv = ""
        for sr in context["sources"]:
            if sr["name"] == "最近の会話":
                recent_conv = sr["text"]
                break

        system = SPEAK_SYSTEM.format(persona=persona)
        prompt = SPEAK_PROMPT.format(
            monologue=thought.get("monologue", ""),
            mood=thought.get("mood", "unknown"),
            datetime=context["datetime"],
            recent_conversation=recent_conv or "（なし）",
            recent_speaks_section=recent_speaks_section,
        )

        raw = await self.bot.llm_router.generate(
            prompt, system=system, purpose="inner_mind", ollama_only=True,
        )
        result = self._parse_speak_response(raw)
        return result.get("message")

    async def _send_to_discord(self, message: str, monologue_id: int) -> None:
        """Discord に自発発言を送信し、DBを更新する。"""
        channel_id = await self._get_setting("speak_channel_id", "")
        if not channel_id:
            log.warning("InnerMind: speak_channel_id not configured, skipping send")
            return

        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                log.warning("InnerMind: channel %s not found", channel_id)
                return

            await channel.send(message)
            log.info("InnerMind: sent message to Discord: %s", message[:80])

            # モノローグの発言情報を更新
            await self.bot.database.update_monologue_notify(monologue_id, message)

            # conversation_log にも記録（次回の会話文脈に含まれるように）
            await self.bot.database.log_conversation(
                "discord", "assistant", message,
                unit="inner_mind",
            )
        except Exception:
            log.error("InnerMind: failed to send to Discord", exc_info=True)

    # --- Discord ステータス取得 ---

    async def _get_user_status(self) -> str:
        """Discordユーザーのオンライン状態を取得。取得不能時は 'online' 扱い。"""
        user_id = await self._get_setting("target_user_id", "")
        if not user_id:
            return "online"
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return "online"

        for guild in self.bot.guilds:
            member = guild.get_member(uid)
            if member:
                return str(member.status)
        return "online"

    # --- JSON パース ---

    def _parse_think_response(self, raw: str) -> dict:
        """LLM応答からthink JSONを抽出。4段階フォールバック + 二次検証。"""
        result = self._extract_json(raw)
        # monologue が JSON 文字列の場合、再パースを試みる
        mono = result.get("monologue", "")
        if isinstance(mono, str) and mono.strip().startswith("{"):
            try:
                inner = json.loads(mono)
                if isinstance(inner, dict) and "monologue" in inner:
                    result = inner
            except (json.JSONDecodeError, TypeError):
                pass
        # 最低限の構造を保証
        if "monologue" not in result:
            result["monologue"] = raw
        if "mood" not in result:
            result["mood"] = "unknown"
        if "memory_update" not in result:
            result["memory_update"] = None
        return result

    def _parse_speak_response(self, raw: str) -> dict:
        """LLM応答からspeak JSONを抽出。"""
        result = self._extract_json(raw)
        msg = result.get("message")
        # "null" 文字列を None に変換
        if msg == "null" or msg is None:
            result["message"] = None
        return result

    def _extract_json(self, raw: str) -> dict:
        """段階的にJSONを抽出する。"""
        # 1. そのまま json.loads
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

        # 2. ```json ... ``` ブロック抽出
        m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 3. 最初の { から最後の } を抽出
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            candidate = m.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 余分な括弧を修復して再試行
                repaired = self._repair_json_braces(candidate)
                if repaired:
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        pass

        # 4. すべて失敗
        log.warning("InnerMind: failed to parse JSON, saving raw response")
        return {"monologue": raw, "mood": "unknown", "memory_update": None}

    @staticmethod
    def _repair_json_braces(s: str) -> str | None:
        """括弧の不一致を修復する。末尾の余分な } や先頭の余分な { を除去。"""
        # 括弧の深さをカウント
        depth = 0
        for ch in s:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth == 0:
            return None  # バランスしているのに失敗 → 修復不能
        if depth < 0:
            # } が多い → 末尾から余分な } を除去
            result = s
            for _ in range(-depth):
                idx = result.rfind("}")
                if idx >= 0:
                    result = result[:idx] + result[idx + 1:]
            return result
        # { が多い → 先頭から余分な { を除去
        result = s
        for _ in range(depth):
            idx = result.find("{", 1)
            if idx >= 0:
                result = result[idx:]
        return result


def _to_bool(val) -> bool:
    """設定値をboolに変換する。"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)
