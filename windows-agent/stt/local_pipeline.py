"""ローカルSTTパイプライン — マイクキャプチャ + Whisper推論を同一PCで実行。"""

import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta

from stt.mic_capture import MicCapture
from stt.whisper_engine import WhisperEngine

JST = timezone(timedelta(hours=9))


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
            "last_error": self._last_error,
            "capture": self._capture.get_status(),
            "whisper": self._engine.get_status(),
        }

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
                if text:
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
