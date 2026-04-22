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
    DECIDE_PROMPT,
    DECIDE_SYSTEM,
    EXTRACT_PROMPT,
    EXTRACT_SYSTEM,
    SPEAK_TEXT_PROMPT,
    SPEAK_TEXT_SYSTEM,
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

# mood → energy_level マッピング
_MOOD_ENERGY_MAP = {
    "talkative": "high",
    "curious": "high",
    "calm": "medium",
    "concerned": "low",
    "idle": "low",
}

# モノローグの NG パターン（抽象語の羅列・禁止表現）。
# 1 個でもマッチした monologue は「特になし」扱いでスキップする。
# ※ 実運用で連発しすぎて問題になっている表現に限定しているので、
#   単独マッチでも reject する方が効果が高い。
_MONOLOGUE_NG_PATTERNS = [
    re.compile(r"情報が(飛び交|流れ込|流れ去|まとまっ|整理さ|次々と)"),
    re.compile(r"(静けさ|静寂)の中"),
    re.compile(r"穏やかな(時間|待機)"),
    re.compile(r"次の展開"),
    re.compile(r"何かが始まる前"),
    re.compile(r"一つに(繋が|まとまっ)"),
    re.compile(r"(心地いい|落ち着いた感覚|落ち着いた時間)"),
    re.compile(r"(全て|すべて)が(一つ|今|ここ)に"),
    re.compile(r"色々な(情報|話題|出来事)が飛び"),
    re.compile(r"静かな時間が流れ"),
    re.compile(r"情報(交換|が洪水)"),
]
_MONOLOGUE_NG_THRESHOLD = 1

# speak テキストの類似度判定しきい値（bigram Jaccard）。
# 直近5件の notified_message と比べて、これ以上なら no_op に降格。
_SPEAK_SIMILARITY_THRESHOLD = 0.35


class InnerMind:
    """ミミの自律思考エンジン。"""

    def __init__(self, bot):
        self.bot = bot
        self.registry = ContextSourceRegistry()
        self.activity_monitor = DiscordActivityMonitor(bot)

        # レンズローテーション用の追跡リスト（起動後に _restore_lens_history で DB から復元）
        self._last_used_lenses: list[str] = []
        self._lens_history_loaded: bool = False
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

        # 初回のみ DB からレンズ履歴を復元（起動後にリセットされないよう）
        await self._restore_lens_history()

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
            # 先にレンズを選ぶ（salience 計算の shared に lens を載せるため）
            lens_category, lens_instruction = self._select_thinking_lens()
            await self._persist_lens_history()

            # コンテキスト収集（lens を踏まえた salience フィルタ込み）
            context = await self._collect_context(lens_category)
            context["_lens_category"] = lens_category
            context["_lens_instruction"] = lens_instruction

            # コンテキスト鮮度チェック（段階的スキップ）
            sources = context["sources"]
            current_mood = str((context.get("self_model") or {}).get("mood", ""))
            ctx_key = (
                context["discord_status"]
                + str(len(sources))
                + current_mood
                + "".join(sr["text"] for sr in sources)
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
                if self._stale_count >= 2:
                    # 2回連続不変 → 鮮度メモを付与して思考は続行
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

            # 保存（decision 結果も一緒に記録するため、先に decide する）
            decision = await self._decide_phase(thought, context)

            # speak が選ばれたら、別プロンプトで短文を生成して params.text に入れる
            if decision and decision.get("action") == "speak":
                speak_text = await self._generate_speak_text(thought, context)
                if speak_text:
                    params = decision.get("params") or {}
                    params["text"] = speak_text
                    decision["params"] = params
                else:
                    # 本文生成失敗 → no_op に降格
                    log.info("InnerMind: speak text empty, demoting to no_op")
                    decision["action"] = "no_op"
                    decision["params"] = {}

            monologue_id = await self._save_thought(thought, context, decision)

            # Actuator に決定を委譲
            if decision and decision.get("action") not in (None, "", "no_op"):
                try:
                    dispatch_result = await self.bot.actuator.dispatch(
                        decision, monologue_id=monologue_id,
                    )
                    log.info(
                        "InnerMind: dispatched action=%s status=%s",
                        decision.get("action"), dispatch_result.get("status"),
                    )
                    # pending 化した場合は pending_id を monologue に記録
                    pid = dispatch_result.get("pending_id")
                    if pid:
                        await self.bot.database.execute(
                            "UPDATE mimi_monologue SET pending_id = ? WHERE id = ?",
                            (pid, monologue_id),
                        )
                except Exception:
                    log.error("InnerMind: actuator dispatch failed", exc_info=True)
        except AllLLMsUnavailableError:
            log.warning("InnerMind: LLM unavailable during think cycle")
        except Exception:
            log.error("InnerMind: think cycle failed", exc_info=True)

    # --- レンズ選択 ---

    async def _restore_lens_history(self) -> None:
        """self_model に保存された recent_lenses を in-memory に復元する。"""
        if self._lens_history_loaded:
            return
        try:
            sm = await self.bot.database.get_self_model() or {}
            raw = sm.get("recent_lenses", "")
            if isinstance(raw, str) and raw:
                valid = {cat for cat, _ in THINKING_LENSES}
                lenses = [s.strip() for s in raw.split(",") if s.strip() in valid]
                # 直近 len(THINKING_LENSES) 件までに絞る
                self._last_used_lenses = lenses[-len(THINKING_LENSES):]
        except Exception:
            log.debug("restore lens history failed", exc_info=True)
        self._lens_history_loaded = True

    async def _persist_lens_history(self) -> None:
        """in-memory のレンズ履歴を self_model に保存する。"""
        try:
            csv = ",".join(self._last_used_lenses)
            await self.bot.database.upsert_self_model("recent_lenses", csv)
        except Exception:
            log.debug("persist lens history failed", exc_info=True)

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

    async def _collect_context(self, lens_category: str = "") -> dict:
        """固定情報 + 全ContextSourceからコンテキストを収集。

        shared にミミの現在の内的状態（mood/interest_topic/lens/時間帯/energy_level）
        を載せ、各ソースが salience() でそれを参照して注目度を返す。
        """
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
        self_model = await self.bot.database.get_self_model() or {}

        last_monologue_text = last_mono["monologue"] if last_mono else ""

        # shared: ミミの現在の内的状態
        shared = {
            "last_monologue": last_monologue_text,
            "now": dt_str,
            "hour": now.hour,
            "time_context": self._get_time_context(),
            "mood": str(self_model.get("mood", "") or ""),
            "interest_topic": str(self_model.get("interest_topic", "") or ""),
            "energy_level": str(self_model.get("energy_level", "") or ""),
            "lens": lens_category,
            "discord_status": discord_status,
        }

        # salience 設定
        cfg = self._get_config().get("salience", {}) or {}
        top_n = int(cfg.get("top_n", 5))
        threshold = float(cfg.get("threshold", 0.25))

        # 全ソースから収集 → salience フィルタ
        source_results = await self.registry.collect_all(
            shared, top_n=top_n, threshold=threshold,
        )

        # recent_summary を抽出（ConversationSource があれば）
        for sr in source_results:
            if sr["name"] == "最近の会話":
                msgs = sr["data"].get("messages", [])
                if msgs:
                    shared["recent_summary"] = msgs[0].get("content", "")[:200]
                break

        log.info(
            "InnerMind: collected %d sources (lens=%s mood=%s): %s",
            len(source_results), lens_category, shared["mood"],
            ", ".join(f"{r['name']}:{r.get('salience', 0):.2f}" for r in source_results),
        )

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
        lens_category = context.get("_lens_category", "")
        # --- Step 1: モノローグ生成 ---
        monologue = await self._generate_monologue(context, staleness_note)
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

    async def _generate_monologue(self, context: dict, staleness_note: str = "") -> str:
        """Step 1: 自由形式でモノローグを生成。モノローグ文字列を返す。"""
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

        # レンズは think() で事前選択済み
        lens_category = context.get("_lens_category", "")
        lens_instruction = context.get("_lens_instruction", "")
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
            return ""

        # NG 表現ガード: 抽象語テンプレに該当するモノローグは捨てる
        ng_hits = self._count_ng_matches(monologue)
        if ng_hits >= _MONOLOGUE_NG_THRESHOLD:
            log.info(
                "InnerMind: monologue rejected by NG filter (hits=%d): %s",
                ng_hits, monologue[:100],
            )
            return ""

        return monologue

    @staticmethod
    def _count_ng_matches(text: str) -> int:
        """モノローグ内の NG パターンのマッチ数を返す。"""
        return sum(1 for pat in _MONOLOGUE_NG_PATTERNS if pat.search(text))

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

    async def _save_thought(
        self, thought: dict, context: dict | None = None,
        decision: dict | None = None,
    ) -> int:
        """モノローグ・自己モデル・記憶をDBに保存。decision があれば action/reasoning も一緒に。"""
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

        action = (decision or {}).get("action") or None
        reasoning = (decision or {}).get("reasoning") or None
        params = (decision or {}).get("params")
        action_params = json.dumps(params, ensure_ascii=False) if params else None

        monologue_id = await self.bot.database.save_monologue(
            monologue, mood, context_json=context_json,
            action=action, reasoning=reasoning, action_params=action_params,
        )

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

    # --- 決定フェーズ ---

    async def _decide_phase(self, thought: dict, context: dict) -> dict | None:
        """モノローグ・コンテキストから次の行動を決める。autonomy.mode=off ならスキップ。"""
        mode = await self.bot.database.get_setting("inner_mind.autonomy.mode")
        if not mode or mode == "off":
            return None

        persona = self.bot.config.get("character", {}).get("persona", "")

        # 直近会話テキスト
        recent_conv = ""
        for sr in context["sources"]:
            if sr["name"] == "最近の会話":
                recent_conv = sr["text"]
                break

        # 直近の自律アクション履歴（重複抑制）
        recent_actions_section = ""
        try:
            recent = await self.bot.database.fetchall(
                "SELECT action, reasoning, notified_message FROM mimi_monologue "
                "WHERE action IS NOT NULL AND action != 'no_op' "
                "ORDER BY id DESC LIMIT 5",
            )
            if recent:
                lines = ["[直近の自律アクション（繰り返しを避けること）]"]
                for r in recent:
                    act = r.get("action") or ""
                    msg = r.get("notified_message") or r.get("reasoning") or ""
                    lines.append(f"- {act}: {msg[:80]}")
                recent_actions_section = "\n".join(lines)
        except Exception as e:
            log.debug("InnerMind: recent actions fetch failed: %s", e)

        # Tier2/Tier3 許可メニュー
        tier2_menu = await self._build_tier_menu(2)
        tier3_menu = await self._build_tier_menu(3)

        system = DECIDE_SYSTEM.format(persona=persona)
        prompt = DECIDE_PROMPT.format(
            monologue=thought.get("monologue", ""),
            mood=thought.get("mood", "unknown"),
            datetime=context["datetime"],
            time_context=self._get_time_context(),
            discord_status=context.get("discord_status", "unknown"),
            recent_conversation=recent_conv or "（なし）",
            recent_actions_section=recent_actions_section or "（履歴なし）",
            tier2_menu=tier2_menu,
            tier3_menu=tier3_menu,
        )

        try:
            raw = await self.bot.llm_router.generate(
                prompt, system=system, purpose="inner_mind", ollama_only=True,
            )
        except Exception:
            log.error("InnerMind: decide LLM failed", exc_info=True)
            return None

        result = self._extract_json(raw)
        action = result.get("action") or "no_op"

        # action を unit.method に分解
        unit_name, method = "", ""
        if "." in action and action not in ("no_op",):
            unit_name, method = action.split(".", 1)

        decision = {
            "action": action,
            "unit": unit_name,
            "method": method,
            "params": result.get("params") or {},
            "reasoning": result.get("reasoning") or "",
            "summary": result.get("summary") or "",
        }
        return decision

    async def _generate_speak_text(self, thought: dict, context: dict) -> str:
        """action=speak 選択後、モノローグから短い発言本文を生成する。"""
        persona = self.bot.config.get("character", {}).get("persona", "")
        user_name = await self._get_user_name()

        recent_conv = ""
        for sr in context["sources"]:
            if sr["name"] == "最近の会話":
                recent_conv = sr["text"]
                break

        # 直近の自発発言（繰り返し抑制・類似度ガード用）
        recent_rows = await self.bot.database.fetchall(
            "SELECT notified_message FROM mimi_monologue "
            "WHERE did_notify = 1 AND notified_message IS NOT NULL "
            "ORDER BY id DESC LIMIT 5",
        )
        recent_texts = [
            (r.get("notified_message") or "").strip()
            for r in (recent_rows or [])
        ]
        recent_texts = [t for t in recent_texts if t]
        if recent_texts:
            recent_speaks = "\n".join(f"- {t[:100]}" for t in recent_texts)
        else:
            recent_speaks = "（過去の自発発言なし）"

        system = SPEAK_TEXT_SYSTEM.format(persona=persona, user_name=user_name)
        prompt = SPEAK_TEXT_PROMPT.format(
            monologue=thought.get("monologue", ""),
            time_context=self._get_time_context(),
            discord_status=context.get("discord_status", "unknown"),
            recent_conversation=recent_conv or "（なし）",
            recent_speaks=recent_speaks,
            user_name=user_name,
        )
        try:
            raw = await self.bot.llm_router.generate(
                prompt, system=system, purpose="inner_mind", ollama_only=True,
            )
        except Exception:
            log.warning("speak text generation failed", exc_info=True)
            return ""

        text = (raw or "").strip()
        # 囲み記号や引用符を剥がす
        text = text.strip("`").strip('"').strip("'").strip()
        if not text:
            return ""
        # 長すぎる場合は先頭60文字でトリム（プロンプトと揃える）
        if len(text) > 60:
            text = text[:60]

        # 類似度ガード: 直近の発言とほぼ同じなら沈黙させる（no_op に降格される）
        if recent_texts:
            sim = max(self._bigram_jaccard(text, prev) for prev in recent_texts)
            if sim >= _SPEAK_SIMILARITY_THRESHOLD:
                log.info(
                    "InnerMind: speak text too similar (jaccard=%.2f) to recent, demoting: %s",
                    sim, text,
                )
                return ""

        return text

    async def _get_user_name(self) -> str:
        """ユーザーの呼称を取得。DB settings → config → フォールバック順。"""
        val = await self.bot.database.get_setting("character.user_name")
        if not val:
            val = self.bot.config.get("character", {}).get("user_name", "")
        val = (val or "").strip()
        return val or "あなた"

    @staticmethod
    def _bigram_jaccard(a: str, b: str) -> float:
        """2文字 n-gram の Jaccard 係数で日本語文の類似度を測る。"""
        def bigrams(s: str) -> set[str]:
            s = re.sub(r"[\s、。！？!?「」『』\"'`]", "", s)
            if len(s) < 2:
                return set()
            return {s[i:i + 2] for i in range(len(s) - 1)}

        ga, gb = bigrams(a), bigrams(b)
        if not ga or not gb:
            return 0.0
        return len(ga & gb) / len(ga | gb)

    async def _build_tier_menu(self, tier: int) -> str:
        """Tier2/Tier3 の許可ユニット一覧をプロンプト用文字列に整形。"""
        allowed_csv = await self.bot.database.get_setting(
            f"inner_mind.autonomy.t{tier}_allowed_units",
        )
        if not allowed_csv:
            return f"（Tier{tier} の許可アクションなし）"
        lines = []
        for key in allowed_csv.split(","):
            key = key.strip()
            if not key or "." not in key:
                continue
            unit_name, method = key.split(".", 1)
            cog = self.bot.get_cog(unit_name)
            desc = getattr(cog, "UNIT_DESCRIPTION", "") if cog else ""
            hint = getattr(cog, "AUTONOMY_HINT", "") if cog else ""
            entry = f"- {key}: {desc[:60]}"
            if hint:
                entry += f"\n  hint: {hint}"
            lines.append(entry)
        return "\n".join(lines) if lines else f"（Tier{tier} の許可アクションなし）"

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
