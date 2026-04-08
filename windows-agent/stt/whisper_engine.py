"""kotoba-whisper ラッパー（lazy load / unload）— Sub PC用。"""

import io
import threading
import time

import soundfile as sf


class WhisperEngine:
    """kotoba-whisper-v2.0 によるSTT処理。レイジーロード対応。"""

    def __init__(self, config: dict):
        self._model_name = config.get("name", "kotoba-tech/kotoba-whisper-v2.0")
        self._device = config.get("device", "cuda")
        self._torch_dtype = config.get("torch_dtype", "float16")
        self._unload_minutes = config.get("unload_after_minutes", 10)

        self._pipe = None
        self._lock = threading.Lock()
        self._last_used: float = 0
        self._unload_timer: threading.Timer | None = None

    @property
    def loaded(self) -> bool:
        return self._pipe is not None

    def get_status(self) -> dict:
        idle_seconds = None
        if self._pipe and self._last_used:
            idle_seconds = round(time.time() - self._last_used, 1)
        return {
            "loaded": self.loaded,
            "model": self._model_name,
            "device": self._device,
            "idle_seconds": idle_seconds,
        }

    def transcribe(self, wav_bytes: bytes) -> str:
        """WAVバイト列を受け取り、テキストを返す。"""
        with self._lock:
            self._ensure_loaded()
            self._last_used = time.time()

        audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if sr != 16000:
            import librosa
            audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)

        result = self._pipe(
            audio,
            generate_kwargs={"language": "ja", "task": "transcribe"},
        )
        text = result.get("text", "") if isinstance(result, dict) else ""

        self._reset_unload_timer()
        return text.strip()

    def unload(self) -> None:
        """モデルをVRAMから解放する。"""
        with self._lock:
            if self._pipe is not None:
                del self._pipe
                self._pipe = None
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    def _ensure_loaded(self) -> None:
        if self._pipe is not None:
            return
        import torch
        from transformers import pipeline

        dtype_map = {"float16": torch.float16, "float32": torch.float32}
        torch_dtype = dtype_map.get(self._torch_dtype, torch.float16)

        self._pipe = pipeline(
            "automatic-speech-recognition",
            model=self._model_name,
            torch_dtype=torch_dtype,
            device=self._device,
        )

    def _reset_unload_timer(self) -> None:
        if self._unload_timer:
            self._unload_timer.cancel()
        if self._unload_minutes > 0:
            self._unload_timer = threading.Timer(
                self._unload_minutes * 60, self.unload
            )
            self._unload_timer.daemon = True
            self._unload_timer.start()
