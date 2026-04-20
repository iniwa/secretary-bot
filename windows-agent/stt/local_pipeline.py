"""ローカルSTTパイプライン — マイクキャプチャ + Whisper推論を同一PCで実行。"""

import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone

from stt.mic_capture import MicCapture
from stt.whisper_engine import WhisperEngine

JST = timezone(timedelta(hours=9))

# --- Whisper ハルシネーション フィルター ---
# Whisperがノイズ/無音から生成しがちな定型フレーズ
_KNOWN_HALLUCINATIONS = frozenset({
    "ごめん", "ご視聴ありがとうございました", "ありがとうございました",
    "おやすみなさい", "お疲れ様でした", "では", "はい",
    "ご視聴ありがとうございます", "字幕視聴ありがとうございました",
    "お願いします", "それでは", "よいしょ",
})

# 繰り返し検出: 直近N件の中で同じ短いテキストがM回以上出たらハルシネーション
_REPEAT_WINDOW = 10  # 直近10件を確認
_REPEAT_THRESHOLD = 3  # 3回以上の繰り返しで判定
_SHORT_TEXT_MAXLEN = 5  # 繰り返し検出対象の最大文字数


class Transcript:
    """STT結果1件。"""

    __slots__ = ("text", "started_at", "ended_at", "duration_seconds", "created_at")

    def __init__(self, text: str, started_at: str, ended_at: str, duration_seconds: float):
        self.text = text
        self.started_at = started_at
        self.ended_at = ended_at
        self.duration_seconds = duration_seconds
        self.created_at = datetime.now(JST).isoformat()

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at,
        }


class LocalSTTPipeline:
    """MicCapture → WhisperEngine のローカルパイプライン。

    バッチ処理で蓄積された発話を定期的にWhisperで推論し、transcriptとして保持する。
    """

    def __init__(self, capture: MicCapture, engine: WhisperEngine, config: dict):
        self._capture = capture
        self._engine = engine
        self._interval = config.get("process_interval_seconds", 30)

        self._transcripts: deque[Transcript] = deque(maxlen=1000)
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_error: str | None = None
        self._total_processed = 0
        self._total_filtered = 0

        # ハルシネーションフィルター設定
        filter_cfg = config.get("hallucination_filter", {})
        self._filter_enabled = filter_cfg.get("enabled", True)
        self._filter_max_duration = filter_cfg.get("max_duration_seconds", 3.0)
        self._filter_extra_phrases: set[str] = set(filter_cfg.get("extra_phrases", []))
        # 直近のテキスト履歴（繰り返し検出用）
        self._recent_texts: deque[str] = deque(maxlen=_REPEAT_WINDOW)

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def get_transcripts(self, since: str | None = None) -> list[dict]:
        result = []
        for t in self._transcripts:
            if since and t.created_at <= since:
                continue
            result.append(t.to_dict())
        return result

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "transcript_count": len(self._transcripts),
            "total_processed": self._total_processed,
            "total_filtered": self._total_filtered,
            "last_error": self._last_error,
            "capture": self._capture.get_status(),
            "whisper": self._engine.get_status(),
        }

    def _is_hallucination(self, text: str, duration: float) -> str | None:
        """ハルシネーション判定。検出理由を返す。Noneなら正常。"""
        if not self._filter_enabled:
            return None

        # 短い発話のみ対象（長い発話は実際の音声の可能性が高い）
        if duration > self._filter_max_duration:
            return None

        stripped = text.strip()

        # 1) 既知のハルシネーションフレーズ一致
        known = _KNOWN_HALLUCINATIONS | self._filter_extra_phrases
        if stripped in known:
            return f"known_phrase: '{stripped}'"

        # 2) 短いテキストの繰り返し検出
        if len(stripped) <= _SHORT_TEXT_MAXLEN:
            count = Counter(self._recent_texts)
            # 今回のテキストを加えた出現回数
            if count.get(stripped, 0) + 1 >= _REPEAT_THRESHOLD:
                return f"repeated_short: '{stripped}' x{count[stripped] + 1}"

        return None

    def _process_loop(self) -> None:
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            self._process_batch()

    def _process_batch(self) -> None:
        utterances = self._capture.drain()
        if not utterances:
            return

        for utt in utterances:
            try:
                wav_data = utt.to_wav()
                text = self._engine.transcribe(wav_data).strip()
                if not text:
                    self._total_processed += 1
                    self._last_error = None
                    continue

                # ハルシネーションフィルター
                reason = self._is_hallucination(text, utt.duration_seconds)
                if reason:
                    self._total_filtered += 1
                    self._total_processed += 1
                    self._recent_texts.append(text)
                    print(f"[LocalSTT] Hallucination filtered: {reason} "
                          f"(duration={utt.duration_seconds:.1f}s)")
                    continue

                self._recent_texts.append(text)
                self._transcripts.append(Transcript(
                    text=text,
                    started_at=utt.started_at,
                    ended_at=utt.ended_at,
                    duration_seconds=utt.duration_seconds,
                ))
                self._total_processed += 1
                self._last_error = None
            except Exception as e:
                self._last_error = str(e)
                print(f"[LocalSTT] Transcription error: {e}")
