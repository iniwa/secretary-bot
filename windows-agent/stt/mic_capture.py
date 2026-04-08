"""マイクキャプチャ + webrtcvad による発話検出・バッファリング。"""

import io
import struct
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone, timedelta

import numpy as np

JST = timezone(timedelta(hours=9))

# フレーム長 (webrtcvad は 10/20/30ms のみ受付)
_FRAME_DURATION_MS = 30
_SAMPLE_RATE = 16000
_FRAME_SIZE = int(_SAMPLE_RATE * _FRAME_DURATION_MS / 1000)  # 480 samples


class Utterance:
    """確定した発話区間。"""

    __slots__ = ("audio", "started_at", "ended_at")

    def __init__(self, audio: bytes, started_at: str, ended_at: str):
        self.audio = audio
        self.started_at = started_at
        self.ended_at = ended_at

    def to_wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(self.audio)
        return buf.getvalue()

    @property
    def duration_seconds(self) -> float:
        return len(self.audio) / (2 * _SAMPLE_RATE)


class MicCapture:
    """sounddevice + webrtcvad でマイクの発話区間をキャプチャする。"""

    def __init__(self, config: dict):
        self._device = config.get("device")
        self._sample_rate = config.get("sample_rate", _SAMPLE_RATE)
        self._vad_aggressiveness = config.get("vad_aggressiveness", 2)
        self._volume_threshold = config.get("volume_threshold_rms", 300)
        self._silence_threshold = config.get("silence_threshold_seconds", 1.5)
        self._min_utterance = config.get("min_utterance_seconds", 1.0)

        self._running = False
        self._stream = None
        self._vad = None
        self._lock = threading.Lock()

        # バッファ
        self._current_frames: list[bytes] = []
        self._speech_start: str | None = None
        self._silent_frames = 0
        self._max_silent_frames = int(self._silence_threshold * 1000 / _FRAME_DURATION_MS)

        # 確定済み utterance キュー
        self._utterances: deque[Utterance] = deque(maxlen=200)
        self._total_captured = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def buffer_count(self) -> int:
        return len(self._utterances)

    @property
    def total_captured(self) -> int:
        return self._total_captured

    def start(self) -> None:
        if self._running:
            return
        import sounddevice as sd
        import webrtcvad

        self._vad = webrtcvad.Vad(self._vad_aggressiveness)
        self._running = True
        self._current_frames = []
        self._speech_start = None
        self._silent_frames = 0

        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            blocksize=_FRAME_SIZE,
            device=self._device,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # 残っているフレームを utterance として確定
        self._finalize_utterance()

    def drain(self) -> list[Utterance]:
        """蓄積された utterance を全て取り出す。"""
        with self._lock:
            result = list(self._utterances)
            self._utterances.clear()
            return result

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "buffer_utterances": len(self._utterances),
            "total_captured": self._total_captured,
        }

    def _audio_callback(self, indata, frames, time_info, status):
        if not self._running:
            return
        raw = bytes(indata)

        # 音量チェック（RMS）
        samples = np.frombuffer(raw, dtype=np.int16)
        rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
        if rms < self._volume_threshold:
            self._handle_silence()
            return

        # VAD判定
        try:
            is_speech = self._vad.is_speech(raw, self._sample_rate)
        except Exception:
            return

        if is_speech:
            if self._speech_start is None:
                self._speech_start = datetime.now(JST).isoformat()
            self._current_frames.append(raw)
            self._silent_frames = 0
        else:
            self._handle_silence()
            # 発話中の短い無音はフレームに含める（途切れ防止）
            if self._speech_start is not None:
                self._current_frames.append(raw)

    def _handle_silence(self) -> None:
        if self._speech_start is None:
            return
        self._silent_frames += 1
        if self._silent_frames >= self._max_silent_frames:
            self._finalize_utterance()

    def _finalize_utterance(self) -> None:
        if not self._current_frames or self._speech_start is None:
            self._current_frames = []
            self._speech_start = None
            self._silent_frames = 0
            return

        audio = b"".join(self._current_frames)
        ended_at = datetime.now(JST).isoformat()
        utt = Utterance(audio, self._speech_start, ended_at)

        if utt.duration_seconds >= self._min_utterance:
            with self._lock:
                self._utterances.append(utt)
                self._total_captured += 1

        self._current_frames = []
        self._speech_start = None
        self._silent_frames = 0
