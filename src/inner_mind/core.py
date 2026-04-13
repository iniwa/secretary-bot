"""InnerMind — ミミの自律思考サイクル。"""

import hashlib
import json
import random
import re
from datetime import datetime

from src.database import JST, jst_now
from src.errors import AllLLMsUnavailableError
from src.inner_mind.context_sources import ContextSourceRegistry
from src.inner_mind.context_sources.activity import ActivitySource
from src.inner_mind.context_sources.calendar import CalendarSource
from src.inner_mind.context_sources.conversation import ConversationSource
from src.inner_mind.context_sources.github import GitHubSource
from src.inner_mind.context_sources.habit import HabitSource
from src.inner_mind.context_sources.memo import MemoSource
from src.inner_mind.context_sources.memory import MemorySource
from src.inner_mind.context_sources.reminder import ReminderSource
from src.inner_mind.context_sources.rss import RSSSource
from src.inner_mind.context_sources.stt import STTSource
from src.inner_mind.context_sources.tavily_news import TavilyNewsSource
from src.inner_mind.context_sources.weather import WeatherSource
from src.inner_mind.discord_activity import DiscordActivityMonitor
from src.inner_mind.prompts import (
    EXTRACT_PROMPT,
    EXTRACT_SYSTEM,
    SPEAK_PROMPT,
    SPEAK_SYSTEM,
    THINK_PROMPT,
    THINK_SYSTEM,
)
from src.logger import get_logger

log = get_logger(__name__)

# 思考レンズ定義: (カテゴリ名, 指示テキスト)
THINKING_LENSES = [
    ("concrete", "具体的な観察 — コンテキストのデータを1つだけ拾って、素朴な感想を言ってみて。分析じゃなくて感想。"),
    ("empathy", "ユーザーへの想像 — ユーザーが今何をしているか、どんな気分か想像してみて。"),
    ("time_space", "時間と空間 — 今の時間帯や季節、天気から何か連想してみて。"),
    ("curiosity", "好奇心の深掘り — コンテキストの中で気になった1つのことについて少しだけ考えてみて。"),
    ("reflection", "振り返り — 最近の出来事やユーザーとのやりとりを軽く振り返ってみて。"),
    ("rest", "休息モード — 今は静かに待機する時間。「特になし」と書いてOK。無理に考えなくていい。"),
]

# 発言ヒントのカテゴリ
SPEAK_HINT_CATEGORIES = [
    "挨拶・声かけ",
    "話題へのコメント",
    "軽い質問",
    "ねぎらい",
    "自分の考えの共有",
]

# mood → energy_level マッピング
_MOOD_ENERGY_MAP = {
    "talkative": "high",
    "curious": "high",
    "calm": "medium",
    "concerned": "low",
    "idle": "low",
}


class InnerMind:
    """ミミの自律思考エンジン。"""

    def __init__(self, bot):
        self.bot = bot
        self.registry = ContextSourceRegistry()
        self.activity_monitor = DiscordActivityMonitor(bot)

        # レンズローテーション用の追跡リスト
        self._last_used_lenses: list[str] = []
        # コンテキスト鮮度追跡
        self._stale_count: int = 0

        # 初期ソース登録
        self.registry.register(ConversationSource(bot))
        self.registry.register(MemoSource(bot))
        self.registry.register(ReminderSource(bot))
        self.registry.register(MemorySource(bot))
        self.registry.register(WeatherSource(bot))
        self.registry.register(RSSSource(bot))
        self.registry.register(STTSource(bot))
        self.registry.register(ActivitySource(bot))
        self.registry.register(HabitSource(bot))
        self.registry.register(CalendarSource(bot))
        self.registry.register(GitHubSource(bot))
        self.registry.register(TavilyNewsSource(bot))

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

        # Discord アクティビティ検出による収集/思考分離
        activity_state = await self.activity_monitor.get_state()
        self._last_activity_state = activity_state
        mode = activity_state.get("mode", "full")
        if mode == "stop":
            log.info(
                "InnerMind: skipped (reason=%s, status=%s)",
                activity_state.get("reason"), activity_state.get("status"),
            )
            return
        if mode == "collect_only":
            log.info(
                "InnerMind: collect-only mode (reason=%s); skipping think phase",
                activity_state.get("reason"),
            )
            return

        log.info("InnerMind: think cycle started")
        try:
            # コンテキスト収集
            context = await self._collect_context()

            # コンテキスト鮮度チェック（段階的スキップ）
            ctx_key = (
                context["discord_status"]
                + "|".join(sr["text"][:200] for sr in context["sources"])
            )
            ctx_hash = hashlib.md5(ctx_key.encode()).hexdigest()

            staleness_note = ""
            if hasattr(self, "_last_ctx_hash") and self._last_ctx_hash == ctx_hash:
                self._stale_count += 1
                if self._stale_count >= 5:
                    # 5回連続不変 → 思考スキップ
                    log.info(
                        "InnerMind: context unchanged %d times, skipping think cycle",
                        self._stale_count,
                    )
                    return
                if self._stale_count >= 3:
                    # 3回連続不変 → 鮮度メモを付与して思考は続行
                    staleness_note = (
                        "コンテキストに大きな変化はありません。"
                        "無理に考える必要はなく、「特になし」でOKです。"
                    )
                    log.debug(
                        "InnerMind: context stale (%d), adding staleness note",
                        self._stale_count,
                    )
            else:
                # コンテキスト変化あり → リセット
                self._stale_count = 0

            self._last_ctx_hash = ctx_hash

            # 思考フェーズ（鮮度メモを渡す）
            thought = await self._think_phase(context, staleness_note)
            if not thought:
                return

            # 保存
            monologue_id = await self._save_thought(thought, context)

            # 発言フェーズ
            await self._speak_phase(thought, monologue_id, context)
        except AllLLMsUnavailableError:
            log.warning("InnerMind: LLM unavailable during think cycle")
        except Exception:
            log.error("InnerMind: think cycle failed", exc_info=True)

    # --- レンズ選択 ---

    def _select_thinking_lens(self) -> tuple[str, str]:
        """未使用のレンズを優先的に選択する。rest は重み付けで出やすくする。"""
        all_categories = [cat for cat, _ in THINKING_LENSES]

        # 未使用レンズを抽出
        unused = [cat for cat in all_categories if cat not in self._last_used_lenses]
        if not unused:
            # 全部使った → リセット
            self._last_used_lenses.clear()
            unused = list(all_categories)

        # rest レンズの重み付け（rest が未使用なら2倍の確率）
        weighted = []
        for cat in unused:
            weighted.append(cat)
            if cat == "rest":
                weighted.append(cat)  # rest を2回追加 → 約2倍の選択確率

        chosen_category = random.choice(weighted)
        self._last_used_lenses.append(chosen_category)

        # カテゴリに対応する指示テキストを取得
        for cat, instruction in THINKING_LENSES:
            if cat == chosen_category:
                return cat, instruction

        # フォールバック（到達しないはず）
        return THINKING_LENSES[0]

    # --- 時間帯コンテキスト ---

    @staticmethod
    def _get_time_context() -> str:
        """現在時刻から時間帯テキストを生成する。"""
        hour = datetime.now(JST).hour
        if 5 <= hour < 10:
            return "朝"
        if 10 <= hour < 12:
            return "午前"
        if 12 <= hour < 14:
            return "昼"
        if 14 <= hour < 17:
            return "午後"
        if 17 <= hour < 20:
            return "夕方"
        if 20 <= hour < 23:
            return "夜"
        return "深夜"

    # --- 発言ヒント生成 ---

    @staticmethod
    def _build_speak_hint(recent_speaks: list[dict]) -> str:
        """直近の発言パターンを分析し、使われていないカテゴリを提案する。"""
        if not recent_speaks:
            return "まだ自発発言がないので、自由に話しかけてOK"

        # 直近発言のテキストからカテゴリを簡易推定
        used_categories: set[str] = set()
        for s in recent_speaks:
            msg = s.get("notified_message", "") or ""
            if any(w in msg for w in ["おはよう", "おつかれ", "おやすみ", "こんにち", "こんばん"]):
                used_categories.add("挨拶・声かけ")
            if any(w in msg for w in ["？", "?", "何", "どう"]):
                used_categories.add("軽い質問")
            if any(w in msg for w in ["頑張", "お疲れ", "無理しないで", "休憩"]):
                used_categories.add("ねぎらい")
            if any(w in msg for w in ["考えてた", "思った", "気になった"]):
                used_categories.add("自分の考えの共有")

        # 未使用カテゴリを提案
        unused = [c for c in SPEAK_HINT_CATEGORIES if c not in used_categories]
        if unused:
            return f"今回は「{unused[0]}」系の発言を試してみて"
        return "バリエーションを意識して、最近と違うトーンで"

    # --- モノローグのサニタイズ ---

    @staticmethod
    def _sanitize_monologue(text: str) -> str:
        """モノローグテキストからJSON構造的ノイズを除去。"""
        # ```json ... ``` ブロック内のテキストを取り出す（閉じなしにも対応）
        m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if not m:
            m = re.search(r'```json\s*(.*)', text, re.DOTALL)
        if m:
            text = m.group(1).strip().rstrip("`")
        # JSON構造の場合、monologue系フィールドを抽出
        text = text.strip()
        if text.startswith('{'):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    # 複数の既知キー名に対応
                    for key in ("monologue", "internal_monologue", "internal_thoughts", "thought"):
                        if key in parsed:
                            val = parsed[key]
                            # ネストされた dict の場合はさらに掘る
                            if isinstance(val, dict):
                                for sub_key in ("thought_process", "text", "content"):
                                    if sub_key in val:
                                        sub = val[sub_key]
                                        text = sub if isinstance(sub, str) else str(sub)
                                        break
                                else:
                                    text = str(val)
                            elif isinstance(val, list):
                                text = " ".join(str(v) for v in val)
                            else:
                                text = str(val)
                            break
            except (json.JSONDecodeError, TypeError):
                pass
        # 先頭の "monologue": " や末尾の不要な引用符を除去
        text = re.sub(r'^["\s]*(?:monologue["\s]*:["\s]*)', '', text)
        return text.strip().strip('"')

    # --- コンテキスト収集 ---

    async def _collect_context(self) -> dict:
        """固定情報 + 全ContextSourceからコンテキストを収集。"""
        now = datetime.now(JST)
        weekdays = ["月", "火", "水", "木", "金", "土", "日"]
        dt_str = now.strftime("%Y-%m-%d %H:%M") + f"（{weekdays[now.weekday()]}曜日）"

        discord_status = await self._get_user_status()
        # アクティビティ情報を status に付加
        activity_state = getattr(self, "_last_activity_state", None)
        if activity_state and activity_state.get("activities"):
            act_text = DiscordActivityMonitor.format_activities(activity_state["activities"])
            if act_text:
                discord_status = f"{discord_status}（{act_text}）"
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

    # --- 思考レンズ → mood ヒントマッピング ---

    _LENS_MOOD_HINTS: dict[str, str] = {
        "empathy": "ユーザーのことを想像しているので、talkative（話しかけたい）寄りかもしれません。",
        "curiosity": "好奇心が刺激されているので、curious が自然です。",
        "rest": "休息モードなので、idle が自然です。",
        "reflection": "振り返りなので、calm か concerned が自然です。",
        "concrete": "具体的な観察なので、curious か calm が自然です。",
        "time_space": "時間や空間の連想なので、calm か idle が自然です。",
    }

    # --- 思考フェーズ（2段階） ---

    async def _think_phase(self, context: dict, staleness_note: str = "") -> dict | None:
        """思考フェーズ: Step1で自由形式モノローグ生成、Step2で構造化抽出。"""
        # --- Step 1: モノローグ生成 ---
        monologue, lens_category = await self._generate_monologue(context, staleness_note)
        if not monologue:
            return None

        # --- Step 2: 構造化抽出 ---
        structured = await self._extract_structure(monologue, lens_category)

        # 結果を統合
        result = {
            "monologue": monologue,
            "mood": structured.get("mood", "calm"),
            "memory_update": structured.get("memory_update"),
            "interest_topic": structured.get("interest_topic"),
            "_lens_category": lens_category,
        }
        return result

    async def _generate_monologue(self, context: dict, staleness_note: str = "") -> tuple[str, str]:
        """Step 1: 自由形式でモノローグを生成。(monologue, lens_category) を返す。"""
        persona = self.bot.config.get("character", {}).get("persona", "")

        # コンテキストソースをプロンプトセクションに変換
        sections = []
        for sr in context["sources"]:
            sections.append(f"[{sr['name']}]\n{sr['text']}")
        context_sections = "\n\n".join(sections) if sections else "（特になし）"

        # 自己モデルをテキスト化
        sm = context["self_model"]
        self_model_text = "\n".join(f"{k}: {v}" for k, v in sm.items()) if sm else "（未形成）"

        # 直近5件のモノローグ要約（重複思考防止用） — mood併記
        recent_monos = await self.bot.database.get_monologues(limit=5)
        if recent_monos:
            mono_lines = []
            for m in recent_monos:
                mono_lines.append(f"- [{m['mood']}] {m['monologue'][:100]}")
            recent_monologues_text = "\n".join(mono_lines)
        else:
            recent_monologues_text = "（初回思考）"

        # 思考レンズ選択
        lens_category, lens_instruction = self._select_thinking_lens()
        thinking_lens = f"カテゴリ: {lens_category}\n{lens_instruction}"

        system = THINK_SYSTEM.format(persona=persona)
        prompt = THINK_PROMPT.format(
            datetime=context["datetime"],
            discord_status=context["discord_status"],
            context_sections=context_sections,
            recent_monologues=recent_monologues_text,
            self_model=self_model_text,
            thinking_lens=thinking_lens,
            staleness_note=staleness_note,
        )

        raw = await self.bot.llm_router.generate(
            prompt, system=system, purpose="inner_mind", ollama_only=True,
        )

        # Step1はプレーンテキスト — JSONラッパーが混入した場合はサニタイズ
        monologue = self._sanitize_monologue(raw)
        if not monologue or monologue == "特になし":
            log.info("InnerMind: monologue is empty/skipped")
            return ("", lens_category)

        return (monologue, lens_category)

    async def _extract_structure(self, monologue: str, lens_category: str) -> dict:
        """Step 2: モノローグからmood/memory_update/interest_topicをJSON抽出。"""
        # mood ヒント
        mood_hint = self._LENS_MOOD_HINTS.get(lens_category, "")
        if mood_hint:
            mood_hint = f"[ヒント] {mood_hint}"

        # 直近mood履歴（同じmoodの連続を防ぐ）
        recent_monos = await self.bot.database.get_monologues(limit=5)
        if recent_monos:
            recent_moods = ", ".join(m["mood"] for m in recent_monos)
        else:
            recent_moods = "（なし）"

        prompt = EXTRACT_PROMPT.format(
            monologue=monologue,
            mood_hint=mood_hint,
            recent_moods=recent_moods,
        )

        raw = await self.bot.llm_router.generate(
            prompt, system=EXTRACT_SYSTEM, purpose="inner_mind", ollama_only=True,
        )

        result = self._extract_json(raw)

        # mood バリデーション
        valid_moods = {"curious", "calm", "talkative", "concerned", "idle"}
        if result.get("mood") not in valid_moods:
            # 間違ったキー名のフォールバック
            for key in ("emotion", "feeling", "state"):
                if result.get(key) in valid_moods:
                    result["mood"] = result[key]
                    break
            else:
                result["mood"] = "calm"

        # "null" 文字列を None に正規化
        for key in ("memory_update", "interest_topic"):
            val = result.get(key)
            if val in ("null", "None", "", None):
                result[key] = None

        return result

    # --- 思考保存 ---

    async def _save_thought(self, thought: dict, context: dict | None = None) -> int:
        """モノローグ・自己モデル・記憶をDBに保存。"""
        monologue = thought.get("monologue", "")
        mood = thought.get("mood", "unknown")

        # モノローグのサニタイズ（JSON構造ノイズ除去）
        monologue = self._sanitize_monologue(monologue)
        thought["monologue"] = monologue

        # コンテキストソースをJSON化（name と text のみ）
        context_json = ""
        if context and context.get("sources"):
            context_json = json.dumps(
                [{"name": s["name"], "text": s["text"]} for s in context["sources"]],
                ensure_ascii=False,
            )

        monologue_id = await self.bot.database.save_monologue(monologue, mood, context_json=context_json)

        # 自己モデル更新（mood）
        if mood and mood != "unknown":
            await self.bot.database.upsert_self_model("mood", mood)

        # interest_topic の保存
        interest = thought.get("interest_topic")
        if interest and interest != "null":
            await self.bot.database.upsert_self_model("interest_topic", interest)

        # last_lens の保存（レンズカテゴリ）
        lens_category = thought.get("_lens_category")
        if lens_category:
            await self.bot.database.upsert_self_model("last_lens", lens_category)

        # energy_level の導出・保存（moodから）
        energy = _MOOD_ENERGY_MAP.get(mood)
        if energy:
            await self.bot.database.upsert_self_model("energy_level", energy)

        # memory_update があれば AIMemory.save() 経由で保存（dedupはsave側が自動処理）
        mem_update = thought.get("memory_update")
        if mem_update and mem_update != "null":
            try:
                from src.memory.ai_memory import AIMemory
                ai_mem = AIMemory(self.bot)
                await ai_mem.save(
                    mem_update,
                    {"source": "inner_mind", "created_at": jst_now()},
                )
                log.info("InnerMind: memory updated: %s", mem_update[:80])
            except Exception as e:
                log.warning("InnerMind: memory_update save failed: %s", e)

        log.info(
            "InnerMind: thought saved (mood=%s, lens=%s): %s",
            mood, lens_category, monologue[:80],
        )
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

        # 時間帯コンテキスト
        time_context = self._get_time_context()

        # 発言ヒント生成（直近発言のパターン分析に基づく）
        speak_hint = self._build_speak_hint(recent_speaks)

        system = SPEAK_SYSTEM.format(persona=persona)
        prompt = SPEAK_PROMPT.format(
            monologue=thought.get("monologue", ""),
            mood=thought.get("mood", "unknown"),
            datetime=context["datetime"],
            time_context=time_context,
            recent_conversation=recent_conv or "（なし）",
            recent_speaks_section=recent_speaks_section,
            speak_hint=speak_hint,
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

        # 2. ```json ... ``` ブロック抽出（閉じなしにも対応）
        m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if not m:
            m = re.search(r"```json\s*(.*)", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip().rstrip("`"))
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
