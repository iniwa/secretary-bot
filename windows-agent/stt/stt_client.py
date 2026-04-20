"""Sub PC /stt への送信・結果蓄積。"""

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import httpx

from stt.mic_capture import MicCapture

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


class STTClient:
    """マイクキャプチャから定期的にSub PCへバッチ送信し、結果を蓄積する。"""

    def __init__(self, capture: MicCapture, config: dict):
        self._capture = capture
        self._sub_pc_url = config.get("sub_pc_url", "http://192.168.1.211:7777")
        self._interval = config.get("interval_minutes", 5) * 60
        self._agent_token = config.get("agent_token", "")

        self._transcripts: deque[Transcript] = deque(maxlen=1000)
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._batch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def get_transcripts(self, since: str | None = None) -> list[dict]:
        """指定時刻以降のtranscriptを返す。"""
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
            "last_error": self._last_error,
            "capture": self._capture.get_status(),
        }

    def _batch_loop(self) -> None:
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            self._process_batch()

    def _process_batch(self) -> None:
        utterances = self._capture.drain()
        if not utterances:
            return

        headers = {}
        if self._agent_token:
            headers["X-Agent-Token"] = self._agent_token

        for utt in utterances:
            try:
                wav_data = utt.to_wav()
                resp = httpx.post(
                    f"{self._sub_pc_url}/stt",
                    content=wav_data,
                    headers={**headers, "Content-Type": "audio/wav"},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data.get("text", "").strip()
                if text:
                    self._transcripts.append(Transcript(
                        text=text,
                        started_at=utt.started_at,
                        ended_at=utt.ended_at,
                        duration_seconds=utt.duration_seconds,
                    ))
                self._last_error = None
            except Exception as e:
                self._last_error = str(e)
