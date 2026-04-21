"""STT transcript の LLM 要約 + ChromaDB 保存。"""

import json
from datetime import datetime, timedelta, timezone

from src.database import jst_now
from src.logger import get_logger

log = get_logger(__name__)

_JST = timezone(timedelta(hours=9))

_SUMMARY_SYSTEM = (
    "あなたは音声ログの要約アシスタントです。"
    "出力は必ず日本語のみで行ってください。"
    "韓国語・中国語・英語など他言語は絶対に使わないでください。"
    "入力が短い発話断片の羅列でも、必ず日本語で簡潔にまとめてください。"
)

_SUMMARY_PROMPT = """\
以下は、いにわ（ユーザー）のマイクから取得した音声テキスト化の結果です。
期間: {period_start} 〜 {period_end}
片側の発話のみで、相手の声は含まれていません。

## 発話データ
{transcripts}

## 指示
- 必ず日本語で出力してください
- 最初の1行で「期間: {period_start} 〜 {period_end}」を記述
- 続けて箇条書き3〜5点、合計200〜300文字程度で簡潔に
- 何について話していたかと、推測できる文脈（ゲーム中・作業中・通話中など）を含める
- 前置きや見出しは不要、期間+箇条書きのみ返す
"""


def _parse_started_at(s: str) -> datetime | None:
    """started_at 文字列を JST aware datetime に変換。失敗時は None。"""
    if not s:
        return None
    try:
        # ISO 8601（T区切り）も空白区切り（"YYYY-MM-DD HH:MM:SS"）も fromisoformat で扱える
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_JST)
    return dt


class STTProcessor:
    """蓄積された transcript を LLM で要約し ChromaDB に保存する。"""

    def __init__(self, bot):
        self.bot = bot
        cfg = bot.config.get("stt", {}).get("processing", {})
        self._threshold = cfg.get("summary_threshold_chars", 2000)
        self._silence_minutes = cfg.get("silence_trigger_minutes", 90)
        self._gap_minutes = cfg.get("gap_split_minutes", 120)
        self._min_chunk = cfg.get("min_chunk_chars", 300)
        self._retention_days = cfg.get("retention_days", 30)

    async def process(self) -> bool:
        """未要約の transcript を確認し、ハイブリッド条件で発火したら要約する。True なら処理実行。"""
        # Ollama 利用可否チェック（ai_memory 同様、LLM 不在時は静かにスキップ）
        if not self.bot.llm_router.ollama_available:
            return False

        rows = await self.bot.database.fetchall(
            "SELECT * FROM stt_transcripts WHERE summarized = 0 ORDER BY started_at"
        )
        if not rows:
            return False

        rows, gap_split = self._apply_gap_split(rows)
        if not rows:
            return False

        total_chars = sum(len(r["raw_text"]) for r in rows)

        # 発火条件判定
        fire = False
        reason = ""
        if gap_split and total_chars >= self._min_chunk:
            fire = True
            reason = "gap_split"
        elif total_chars >= self._threshold:
            fire = True
            reason = "threshold"
        else:
            last_dt = _parse_started_at(rows[-1]["started_at"])
            if last_dt is not None:
                now_dt = datetime.now(tz=_JST)
                if now_dt - last_dt >= timedelta(minutes=self._silence_minutes) and total_chars >= self._min_chunk:
                    fire = True
                    reason = "silence"

        if not fire:
            # 詰み解消: gap_split で確定した先頭チャンクが min_chunk 未満なら、
            # もう新しい transcript が挿入される余地がない（=永遠に発火しない）ので、
            # 要約せず summarized=1 を立てて次のチャンクへ進める。
            if gap_split and total_chars < self._min_chunk:
                await self._mark_summarized([r["id"] for r in rows])
                log.info(
                    "STT pruned %d stuck transcripts (%d chars, gap-closed below min_chunk=%d)",
                    len(rows), total_chars, self._min_chunk,
                )
                return True
            return False

        return await self._do_summarize(rows, reason=reason)

    async def flush(self, until: str, reason: str) -> bool:
        """`until` 時刻以前の未要約 transcript を強制要約する（Activity 側トリガーから呼び出し）。

        - min_chunk 未満なら要約せず summarized=1 でマークのみ（LLM 負荷対策）
        - gap_split が先に効くため、until までに複数チャンクあれば先頭チャンクのみ処理
          （残りは次回 process/flush で拾われる）
        """
        if not self.bot.llm_router.ollama_available:
            return False

        rows = await self.bot.database.fetchall(
            "SELECT * FROM stt_transcripts WHERE summarized = 0 AND started_at <= ? ORDER BY started_at",
            (until,),
        )
        if not rows:
            return False

        rows, _gap_split = self._apply_gap_split(rows)
        if not rows:
            return False

        total_chars = sum(len(r["raw_text"]) for r in rows)
        if total_chars < self._min_chunk:
            await self._mark_summarized([r["id"] for r in rows])
            log.info(
                "STT flush skipped-as-mark: %d transcripts, %d chars < min_chunk=%d (reason=%s)",
                len(rows), total_chars, self._min_chunk, reason,
            )
            return True

        return await self._do_summarize(rows, reason=f"flush:{reason}")

    def _apply_gap_split(self, rows: list[dict]) -> tuple[list[dict], bool]:
        """時間順 rows の先頭チャンクを gap_minutes で切り出す。(切り出し後, 分割が起きたか)。"""
        gap_split = False
        cutoff = len(rows)
        prev_dt: datetime | None = None
        gap_threshold = timedelta(minutes=self._gap_minutes)
        for i, r in enumerate(rows):
            cur_dt = _parse_started_at(r["started_at"])
            if prev_dt is not None and cur_dt is not None:
                if cur_dt - prev_dt >= gap_threshold:
                    cutoff = i
                    gap_split = True
                    break
            prev_dt = cur_dt
        if gap_split:
            rows = rows[:cutoff]
        return rows, gap_split

    async def _mark_summarized(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self.bot.database.execute(
            f"UPDATE stt_transcripts SET summarized = 1 WHERE id IN ({placeholders})",
            tuple(ids),
        )

    async def _do_summarize(self, rows: list[dict], reason: str) -> bool:
        """LLM 要約 + stt_summaries INSERT + stt_transcripts マーク + ChromaDB 保存。"""
        transcript_lines = []
        for r in rows:
            time_str = r["started_at"][:16] if r["started_at"] else "?"
            transcript_lines.append(f"[{time_str}] {r['raw_text']}")
        transcript_text = "\n".join(transcript_lines)

        period_start = rows[0]["started_at"][:16] if rows[0]["started_at"] else "?"
        period_end = rows[-1]["started_at"][:16] if rows[-1]["started_at"] else "?"

        prompt = _SUMMARY_PROMPT.format(
            transcripts=transcript_text,
            period_start=period_start,
            period_end=period_end,
        )
        try:
            summary = await self.bot.llm_router.generate(
                prompt, system=_SUMMARY_SYSTEM, purpose="stt_summary",
            )
        except Exception as e:
            log.warning("STT summary generation failed: %s", e)
            return False

        ids = [r["id"] for r in rows]
        ids_json = json.dumps(ids)
        await self.bot.database.execute(
            "INSERT INTO stt_summaries (summary, transcript_ids, created_at) VALUES (?, ?, ?)",
            (summary, ids_json, jst_now()),
        )
        await self._mark_summarized(ids)

        meta_period_start = rows[0]["started_at"] if rows[0]["started_at"] else ""
        meta_period_end = rows[-1]["ended_at"] if rows[-1]["ended_at"] else ""
        try:
            doc_id = f"stt_summary_{ids[0]}_{ids[-1]}"
            self.bot.chroma.add(
                "stt_summaries", doc_id, summary,
                {
                    "transcript_ids": ",".join(str(i) for i in ids),
                    "period_start": meta_period_start,
                    "period_end": meta_period_end,
                },
            )
        except Exception as e:
            log.warning("STT summary ChromaDB save failed: %s", e)

        log.info(
            "STT summary created from %d transcripts (%d chars, reason=%s)",
            len(ids), sum(len(r["raw_text"]) for r in rows), reason,
        )
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

        period_start = rows[0]["started_at"][:16] if rows[0]["started_at"] else "?"
        period_end = rows[-1]["started_at"][:16] if rows[-1]["started_at"] else "?"

        prompt = _SUMMARY_PROMPT.format(
            transcripts=transcript_text,
            period_start=period_start,
            period_end=period_end,
        )
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
        meta_period_start = rows[0]["started_at"] if rows[0]["started_at"] else ""
        meta_period_end = rows[-1]["started_at"] if rows[-1]["started_at"] else ""
        try:
            doc_id = f"stt_summary_{ids[0]}_{ids[-1]}"
            self.bot.chroma.add(
                "stt_summaries", doc_id, new_summary,
                {
                    "transcript_ids": ",".join(str(i) for i in ids),
                    "period_start": meta_period_start,
                    "period_end": meta_period_end,
                },
            )
        except Exception as e:
            log.warning("STT resummarize ChromaDB update failed for id=%d: %s", summary_id, e)

        # NOTE: people_memory への保存は daily_diary 側で一本化（A案）。

        log.info("STT summary id=%d resummarized (%d transcripts)", summary_id, len(ids))
        return True

    async def cleanup_old_transcripts(self) -> int:
        """要約済みで retention_days 以上経過した transcripts を削除。戻り値は参考値（0 でも可）。"""
        if self._retention_days <= 0:
            return 0
        cutoff_dt = datetime.now(tz=_JST) - timedelta(days=self._retention_days)
        # started_at は ISO 8601（T区切り）も空白区切りも両方あり得る。
        # lexicographic 比較で安全側に倒すため、T 区切りの代表形で cutoff を作る。
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            await self.bot.database.execute(
                "DELETE FROM stt_transcripts WHERE id IN "
                "(SELECT id FROM stt_transcripts WHERE summarized = 1 AND started_at < ? LIMIT 1000)",
                (cutoff,),
            )
            log.info("STT cleanup executed (cutoff=%s, retention_days=%d)", cutoff, self._retention_days)
        except Exception as e:
            log.warning("STT cleanup failed: %s", e)
        return 0
