"""STT transcript の LLM 要約 + ChromaDB 保存。"""

import json

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)

_SUMMARY_SYSTEM = (
    "あなたは音声ログの要約アシスタントです。"
    "出力は必ず日本語のみで行ってください。"
    "韓国語・中国語・英語など他言語は絶対に使わないでください。"
    "入力が短い発話断片の羅列でも、必ず日本語で簡潔にまとめてください。"
)

_SUMMARY_PROMPT = """\
以下は、いにわ（ユーザー）のマイクから取得した音声テキスト化の結果です。
片側の発話のみで、相手の声は含まれていません。

## 発話データ
{transcripts}

## 指示
- 必ず日本語で出力してください
- 箇条書き3〜5点、合計200〜300文字程度で簡潔に
- 何について話していたかと、推測できる文脈（ゲーム中・作業中・通話中など）を含める
- 前置きや見出しは不要、箇条書きのみ返す
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
                prompt, system=_SUMMARY_SYSTEM, purpose="stt_summary"
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

    async def resummarize(self, summary_id: int) -> bool:
        """既存の stt_summaries 行を、現在のプロンプトで作り直す。ChromaDB も更新。"""
        summary_row = await self.bot.database.fetchone(
            "SELECT id, summary, transcript_ids FROM stt_summaries WHERE id = ?",
            (summary_id,),
        )
        if not summary_row:
            log.warning("STT resummarize: summary id=%d not found", summary_id)
            return False

        ids = json.loads(summary_row["transcript_ids"])
        if not ids:
            return False

        placeholders = ",".join("?" * len(ids))
        rows = await self.bot.database.fetchall(
            f"SELECT id, started_at, raw_text FROM stt_transcripts WHERE id IN ({placeholders}) ORDER BY started_at",
            tuple(ids),
        )
        if not rows:
            log.warning("STT resummarize: no transcripts for summary id=%d", summary_id)
            return False

        transcript_lines = []
        for r in rows:
            time_str = r["started_at"][:16] if r["started_at"] else "?"
            transcript_lines.append(f"[{time_str}] {r['raw_text']}")
        transcript_text = "\n".join(transcript_lines)

        prompt = _SUMMARY_PROMPT.format(transcripts=transcript_text)
        try:
            new_summary = await self.bot.llm_router.generate(
                prompt, system=_SUMMARY_SYSTEM, purpose="stt_summary",
            )
        except Exception as e:
            log.warning("STT resummarize failed for id=%d: %s", summary_id, e)
            return False

        await self.bot.database.execute(
            "UPDATE stt_summaries SET summary = ? WHERE id = ?",
            (new_summary, summary_id),
        )

        # ChromaDB 更新（doc_id は旧来の命名に合わせる、upsertなので上書き可）
        try:
            doc_id = f"stt_summary_{ids[0]}_{ids[-1]}"
            period_start = rows[0]["started_at"] if rows[0]["started_at"] else ""
            period_end = rows[-1]["started_at"] if rows[-1]["started_at"] else ""
            self.bot.chroma.add(
                "stt_summaries", doc_id, new_summary,
                {
                    "transcript_ids": ",".join(str(i) for i in ids),
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
        except Exception as e:
            log.warning("STT resummarize ChromaDB update failed for id=%d: %s", summary_id, e)

        log.info("STT summary id=%d resummarized (%d transcripts)", summary_id, len(ids))
        return True
