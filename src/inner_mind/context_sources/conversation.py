"""ConversationSource — 直近の会話履歴（セグメント分割・LLM要約付き）。"""

import asyncio
import re
from datetime import datetime

from src.fetch_utils import fetch_page_text
from src.inner_mind.context_sources.base import ContextSource
from src.inner_mind.prompts import CONVERSATION_SUMMARY_PROMPT, CONVERSATION_SUMMARY_SYSTEM
from src.logger import get_logger

log = get_logger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_MAX_URLS = 5
_MAX_CHARS_PER_URL = 500
_TIME_GAP_MINUTES = 30


class ConversationSource(ContextSource):
    """直近の会話を話題ごとにセグメント分割し、LLMで要約して提供する。"""

    name = "最近の会話"
    priority = 10
    always_include = True  # 直近会話は常にモノローグの基盤となる

    def __init__(self, bot):
        super().__init__(bot)
        self._cache_key = None
        self._cache_summary = None
        self._update_lock = asyncio.Lock()

    async def update(self) -> None:
        """背景でLLM要約を更新。新着メッセージが無ければスキップ。"""
        async with self._update_lock:
            messages = await self.bot.database.get_recent_messages(limit=30)
            if not messages:
                return
            messages = [m for m in messages if m.get("unit") != "inner_mind"]
            if not messages:
                return
            latest_id = messages[0].get("id")
            if latest_id == self._cache_key and self._cache_summary:
                return

            url_contents = await self._fetch_urls(messages)
            self._attach_url_details(messages, url_contents)
            segments = self._segment_messages(messages)
            summary = await self._summarize_with_llm(segments)

            self._cache_key = latest_id
            self._cache_summary = summary

    async def collect(self, shared: dict) -> dict | None:
        messages = await self.bot.database.get_recent_messages(limit=30)
        if not messages:
            return None

        # inner_mind 自発発言を除外
        messages = [m for m in messages if m.get("unit") != "inner_mind"]
        if not messages:
            return None

        # URL内容を並列取得 → メッセージに紐付け
        url_contents = await self._fetch_urls(messages)
        self._attach_url_details(messages, url_contents)

        # セグメント分割
        segments = self._segment_messages(messages)

        # キャッシュ命中ならLLM呼ばずに返す（update()が背景で更新済み）
        latest_id = messages[0].get("id")
        if latest_id == self._cache_key and self._cache_summary:
            return {"segments": segments, "summary": self._cache_summary}

        # 背景更新がまだの場合のフォールバック
        return {"segments": segments, "summary": self._heuristic_summary(segments)}

    def format_for_prompt(self, data: dict) -> str:
        """LLM要約があればそれを使用。"""
        return data.get("summary") or ""

    # --- URL取得 ---

    async def _fetch_urls(self, messages: list[dict]) -> dict[str, str]:
        """メッセージ中のURLを抽出し並列取得。"""
        urls: list[str] = []
        seen: set[str] = set()
        for m in messages:
            for url in _URL_RE.findall(m.get("content", "")):
                if url not in seen and len(urls) < _MAX_URLS:
                    seen.add(url)
                    urls.append(url)
        if not urls:
            return {}
        tasks = [fetch_page_text(u, max_chars=_MAX_CHARS_PER_URL) for u in urls]
        results = await asyncio.gather(*tasks)
        return {u: t for u, t in zip(urls, results, strict=False) if t}

    @staticmethod
    def _attach_url_details(messages: list[dict], url_contents: dict):
        """URLメッセージに取得内容を紐付ける。"""
        for msg in messages:
            urls = _URL_RE.findall(msg.get("content", ""))
            details = []
            for url in urls:
                if url in url_contents:
                    details.append({"url": url, "text": url_contents[url]})
            if details:
                msg["_url_details"] = details

    # --- セグメント分割 ---

    def _segment_messages(self, messages: list[dict]) -> list[dict]:
        """時間・チャンネル・ユニットでセグメント分割。"""
        segments: list[dict] = []
        current: dict | None = None
        for msg in reversed(messages):  # 時系列順に処理
            if self._should_split(current, msg):
                current = {
                    "messages": [msg],
                    "start_time": msg.get("timestamp", ""),
                    "end_time": msg.get("timestamp", ""),
                    "channel": msg.get("channel", ""),
                    "channel_name": msg.get("channel_name", ""),
                    "unit": msg.get("unit") if msg.get("role") == "assistant" else None,
                }
                segments.append(current)
            else:
                current["messages"].append(msg)
                current["end_time"] = msg.get("timestamp", "")
                if msg.get("role") == "assistant" and msg.get("unit"):
                    current["unit"] = msg["unit"]
                # channel_name が空なら更新
                if not current["channel_name"] and msg.get("channel_name"):
                    current["channel_name"] = msg["channel_name"]
        return segments

    def _should_split(self, current: dict | None, msg: dict) -> bool:
        """セグメント分割すべきか判定。"""
        if current is None:
            return True
        # 時間ギャップ
        if self._time_gap(current["end_time"], msg.get("timestamp", "")) > _TIME_GAP_MINUTES:
            return True
        # チャンネル種別変更（discord ↔ webgui）
        if msg.get("channel") != current["channel"]:
            return True
        # Discordチャンネル名変更
        msg_ch = msg.get("channel_name", "")
        cur_ch = current.get("channel_name", "")
        if msg_ch and cur_ch and msg_ch != cur_ch:
            return True
        # ユニット変更（assistant応答）
        if msg.get("role") == "assistant" and msg.get("unit"):
            if current["unit"] and msg["unit"] != current["unit"]:
                return True
        return False

    @staticmethod
    def _time_gap(t1: str, t2: str) -> float:
        """2つのタイムスタンプ間の分数を返す。"""
        try:
            dt1 = datetime.fromisoformat(t1)
            dt2 = datetime.fromisoformat(t2)
            return abs((dt2 - dt1).total_seconds()) / 60
        except (ValueError, TypeError):
            return 0

    # --- LLM要約 ---

    async def _summarize_with_llm(self, segments: list[dict]) -> str:
        """全セグメントをLLM 1回で構造化要約。"""
        blocks = []
        for i, seg in enumerate(segments, 1):
            ch_label = self._channel_label(seg)
            time_range = self._format_time_range(seg)
            lines = []
            for m in seg["messages"]:
                role = "U" if m["role"] == "user" else "A"
                uid = m.get("user_id", "")
                prefix = f"U({uid[:8]})" if role == "U" and uid else role
                line = f"  {prefix}: {m['content'][:150]}"
                for detail in m.get("_url_details", []):
                    line += f"\n     -> URL内容: {detail['text'][:300]}"
                lines.append(line)
            blocks.append(
                f"--- セグメント{i} ({ch_label}, {time_range}) ---\n"
                + "\n".join(lines)
            )

        raw_text = "\n\n".join(blocks)
        prompt = CONVERSATION_SUMMARY_PROMPT.format(raw_segments=raw_text)
        try:
            return await self.bot.llm_router.generate(
                prompt, system=CONVERSATION_SUMMARY_SYSTEM,
                purpose="inner_mind", ollama_only=True,
            )
        except Exception:
            log.warning("ConversationSource: LLM summary failed, using heuristic")
            return self._heuristic_summary(segments)

    def _heuristic_summary(self, segments: list[dict]) -> str:
        """フォールバック: ヒューリスティクス要約。"""
        lines = []
        for i, seg in enumerate(segments, 1):
            ch_label = self._channel_label(seg)
            time_range = self._format_time_range(seg)
            user_msgs = [m for m in seg["messages"] if m["role"] == "user"]
            if not user_msgs:
                continue
            if len(user_msgs) <= 2:
                summary = " / ".join(m["content"][:80] for m in user_msgs)
            else:
                summary = f"{user_msgs[0]['content'][:60]}... 他{len(user_msgs)-1}件"

            lines.append(f"[話題{i}] ({ch_label}, {time_range})")
            lines.append(summary)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _channel_label(seg: dict) -> str:
        """セグメントのチャンネルラベルを生成。"""
        name = seg.get("channel_name", "")
        if name:
            return f"#{name}"
        return seg.get("channel", "discord")

    @staticmethod
    def _format_time_range(seg: dict) -> str:
        """時間範囲を整形。"""
        start = seg.get("start_time", "")[:16]
        end = seg.get("end_time", "")[:16]
        if start == end:
            return start[5:]  # MM-DD HH:MM
        return f"{start[5:]}~{end[11:]}"  # MM-DD HH:MM~HH:MM
