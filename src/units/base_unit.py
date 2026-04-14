"""BaseUnit — 全ユニットの基底クラス。discord.py の Cog を継承。"""

import asyncio
import os
import re

from discord.ext import commands

from src.circuit_breaker import CircuitBreaker
from src.flow_tracker import get_flow_tracker
from src.llm.unit_llm import UnitLLM
from src.logger import get_logger

log = get_logger(__name__)

_MENTION_RE = re.compile(r"<@!?\d+>\s*")


class BaseUnit(commands.Cog):
    UNIT_NAME: str = ""
    UNIT_DESCRIPTION: str = ""
    DELEGATE_TO: str | None = None
    PREFERRED_AGENT: str | None = None
    # 自律行動の既定階層。T4=破壊的/未定義。各ユニットで適切な値に上書きする。
    AUTONOMY_TIER: int = 4
    # 自律的に呼び出し可能なアクション名のリスト（method名を想定）。
    AUTONOMOUS_ACTIONS: list[str] = []

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

    async def autonomous_execute(
        self, method: str, params: dict, user_id: str,
    ) -> dict:
        """Actuator から自律アクションを実行する際に呼ばれる。

        AUTONOMOUS_ACTIONS にあるメソッドのみサポートすること。
        各ユニットで必要に応じて override する。
        """
        raise NotImplementedError(
            f"{self.UNIT_NAME} does not implement autonomous_execute ({method})"
        )

    # --- Discord通知ヘルパー ---

    async def notify(self, message: str, *, _user_id: str = "") -> None:
        if self._admin_channel_id:
            channel = self.bot.get_channel(self._admin_channel_id)
            if channel:
                sent = await channel.send(message)
                # 返信ルーティング用: メッセージID → ユニット名を記録
                if self.UNIT_NAME:
                    self.bot._reply_units[sent.id] = self.UNIT_NAME
                # 通知を会話ログに保存（InnerMind等の文脈参照用）
                log_content = _MENTION_RE.sub("", message).strip()
                if log_content:
                    await self.bot.database.log_conversation(
                        "discord", "assistant", log_content,
                        unit=self.UNIT_NAME or None,
                        user_id=_user_id,
                        channel_name=getattr(channel, "name", ""),
                    )
                    # WebGUIにリアルタイム通知を配信
                    ft = get_flow_tracker()
                    await ft.broadcast_notification(
                        log_content, unit=self.UNIT_NAME, user_id=_user_id,
                    )

    async def notify_user(self, message: str, user_id: str = "") -> None:
        """ユーザーメンション付きで管理チャンネルに通知する。"""
        if user_id and user_id != "webgui":
            message = f"<@{user_id}> {message}"
        await self.notify(message, _user_id=user_id)

    async def notify_error(self, message: str) -> None:
        await self.notify(f"[Error] {message}")

    # --- キャラクター変換 ---

    async def _persona_with_memories(
        self, user_message: str, max_memories: int = 3,
    ) -> str:
        """character.persona + 関連ai_memory + 関連people_memory を結合した system prompt を返す。

        - Ollama稼働中でない、またはpersonaが空なら空文字を返す
        - user_message が短すぎ（10文字未満）ならmemoryは取得せずpersonaのみ返す
        - distance >= 0.75（類似度 <= 0.25）のものは除外してノイズカット
        - 各メモリ抜粋は100文字で切り詰め
        """
        if not self.bot.llm_router.ollama_available:
            return ""
        persona = self.bot.config.get("character", {}).get("persona", "")
        if not persona:
            return ""

        # 短すぎるメッセージはmemory取得をスキップ（コスト削減・精度低下防止）
        if not user_message or len(user_message) < 10:
            return persona

        ai_mem = getattr(self.bot, "ai_memory", None)
        people_mem = getattr(self.bot, "people_memory", None)
        tasks = []
        if ai_mem is not None:
            tasks.append(asyncio.to_thread(ai_mem.recall, user_message, max_memories))
        else:
            tasks.append(asyncio.sleep(0, result=[]))
        if people_mem is not None:
            tasks.append(asyncio.to_thread(people_mem.recall, user_message, max_memories))
        else:
            tasks.append(asyncio.sleep(0, result=[]))

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            log.debug("memory recall gather failed: %s", e)
            return persona

        ai_items = results[0] if not isinstance(results[0], BaseException) else []
        people_items = results[1] if not isinstance(results[1], BaseException) else []

        ai_excerpts = self._format_memory_excerpts(ai_items, max_memories)
        people_excerpts = self._format_memory_excerpts(people_items, max_memories)

        parts = [persona]
        if ai_excerpts:
            parts.append("## あなた(ミミ)の関連記憶\n" + "\n".join(f"- {e}" for e in ai_excerpts))
        if people_excerpts:
            parts.append("## いにわに関する関連情報\n" + "\n".join(f"- {e}" for e in people_excerpts))
        return "\n\n".join(parts)

    @staticmethod
    def _format_memory_excerpts(items, limit: int) -> list[str]:
        """chroma search結果から抜粋リストを作る。distance >= 0.75は除外・100文字で切り詰め。"""
        if not items or not isinstance(items, list):
            return []
        out: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            dist = it.get("distance")
            # distance が None の場合は一応採用（safe側）
            if dist is not None:
                try:
                    if float(dist) >= 0.75:
                        continue
                except (TypeError, ValueError):
                    pass
            # document / text 両キー対応
            doc = it.get("document") or it.get("text") or ""
            doc = doc.strip()
            if not doc:
                continue
            if len(doc) > 100:
                doc = doc[:100] + "…"
            out.append(doc)
            if len(out) >= limit:
                break
        return out

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
        # Ollama稼働中 かつ persona 非空のためここで記憶注入版を組み立てる
        persona_block = await self._persona_with_memories(user_message)
        if not persona_block:
            persona_block = persona
        system = (
            f"{persona_block}\n\n"
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
        # Ollama稼働中 かつ persona 非空のためここで記憶注入版を組み立てる
        persona_block = await self._persona_with_memories(user_message)
        if not persona_block:
            persona_block = persona
        system = (
            f"{persona_block}\n\n"
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
