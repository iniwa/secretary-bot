"""STT transcript の LLM 要約 + ChromaDB 保存。"""

import json

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)

_SUMMARY_PROMPT = """\
以下は、いにわ（ユーザー）のマイクから取得した音声テキスト化の結果です。
片側の発話のみで、相手の声は含まれていません。
この発話内容を簡潔に要約してください。

## 発話データ
{transcripts}

## 出力
- 何について話していたかを箇条書きで要約
- 推測できる文脈（ゲーム中、作業中、通話中など）があれば付記
- 日本語で出力
"""


class STTProcessor:
    """蓄積された transcript を LLM で要約し ChromaDB に保存する。"""

    def __init__(self, bot):
        self.bot = bot
        self._threshold = bot.config.get("stt", {}).get(
            "processing", {}
        ).get("summary_threshold_chars", 2000)

    async def process(self) -> bool:
        """未要約の transcript を確認し、閾値を超えていたら要約する。True なら処理実行。"""
        rows = await self.bot.database.fetchall(
            "SELECT * FROM stt_transcripts WHERE summarized = 0 ORDER BY started_at"
        )
        if not rows:
            return False

        total_chars = sum(len(r["raw_text"]) for r in rows)
        if total_chars < self._threshold:
            return False

        # LLM要約
        transcript_lines = []
        for r in rows:
            time_str = r["started_at"][:16] if r["started_at"] else "?"
            transcript_lines.append(f"[{time_str}] {r['raw_text']}")
        transcript_text = "\n".join(transcript_lines)

        prompt = _SUMMARY_PROMPT.format(transcripts=transcript_text)
        try:
            summary = await self.bot.llm_router.generate(
                prompt, purpose="stt_summary"
            )
        except Exception as e:
            log.warning("STT summary generation failed: %s", e)
            return False

        # DB保存
        ids = [r["id"] for r in rows]
        ids_json = json.dumps(ids)
        await self.bot.database.execute(
            "INSERT INTO stt_summaries (summary, transcript_ids, created_at) VALUES (?, ?, ?)",
            (summary, ids_json, jst_now()),
        )

        # transcript を要約済みにマーク
        placeholders = ",".join("?" * len(ids))
        await self.bot.database.execute(
            f"UPDATE stt_transcripts SET summarized = 1 WHERE id IN ({placeholders})",
            tuple(ids),
        )

        # ChromaDB 保存
        try:
            period_start = rows[0]["started_at"] if rows[0]["started_at"] else ""
            period_end = rows[-1]["ended_at"] if rows[-1]["ended_at"] else ""
            doc_id = f"stt_summary_{ids[0]}_{ids[-1]}"
            self.bot.chroma.add(
                "stt_summaries", doc_id, summary,
                {
                    "transcript_ids": ",".join(str(i) for i in ids),
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
        except Exception as e:
            log.warning("STT summary ChromaDB save failed: %s", e)

        log.info("STT summary created from %d transcripts (%d chars)", len(ids), total_chars)
        return True
