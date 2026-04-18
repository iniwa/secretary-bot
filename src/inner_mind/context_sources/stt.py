"""STTSource — いにわの音声テキスト化データを InnerMind に提供する。"""

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)


class STTSource(ContextSource):
    """最近のSTTテキストと要約をコンテキストに注入する。"""

    name = "いにわの発話（STT）"
    priority = 50

    async def collect(self, shared: dict) -> dict | None:
        stt_cfg = self.bot.config.get("stt", {})
        if not stt_cfg.get("enabled", False):
            return None

        # 未要約の生テキスト（直近10件）
        raw = await self.bot.database.fetchall(
            "SELECT raw_text, started_at FROM stt_transcripts "
            "WHERE summarized = 0 ORDER BY started_at DESC LIMIT 10"
        )

        # 直近の要約（3件）
        summaries = await self.bot.database.fetchall(
            "SELECT summary, created_at FROM stt_summaries "
            "ORDER BY created_at DESC LIMIT 3"
        )

        if not raw and not summaries:
            return None

        return {
            "raw_transcripts": list(reversed(raw)),
            "summaries": list(reversed(summaries)),
        }

    def format_for_prompt(self, data: dict) -> str:
        lines = []

        summaries = data.get("summaries", [])
        if summaries:
            lines.append("### 最近の発話要約")
            for s in summaries:
                lines.append(f"[{s['created_at'][:16]}] {s['summary']}")
            lines.append("")

        raw = data.get("raw_transcripts", [])
        if raw:
            lines.append("### 未要約の最近の発話")
            for r in raw:
                time_str = r["started_at"][:16] if r.get("started_at") else "?"
                lines.append(f"[{time_str}] {r['raw_text']}")

        return "\n".join(lines)

    async def salience(self, data: dict, shared: dict) -> float:
        """未要約の生テキスト（新しい発話）があれば高い。要約のみなら低め。"""
        raw = data.get("raw_transcripts", []) or []
        summaries = data.get("summaries", []) or []
        if raw:
            return 0.8
        if summaries:
            return 0.35
        return 0.0
