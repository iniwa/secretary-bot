"""kotoba-whisper ラッパー（lazy load / unload）— Sub PC用。"""

import io
import threading
import time

import numpy as np
import soundfile as sf


class WhisperEngine:
    """kotoba-whisper-v2.0 によるSTT処理。レイジーロード対応。"""

    def __init__(self, config: dict):
        self._model_name = config.get("name", "kotoba-tech/kotoba-whisper-v2.0")
        self._device = config.get("device", "cuda")
        self._torch_dtype = config.get("torch_dtype", "float16")
        self._unload_minutes = config.get("unload_after_minutes", 10)

        self._model = None
        self._processor = None
        self._lock = threading.Lock()
        self._last_used: float = 0
        self._unload_timer: threading.Timer | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def get_status(self) -> dict:
        idle_seconds = None
        if self._model and self._last_used:
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

        # soundfileでデコード
        audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if sr != 16000:
            # リサンプル（簡易線形補間）
            ratio = 16000 / sr
            n = int(len(audio) * ratio)
            indices = np.arange(n) / ratio
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

        # プロセッサでトークン化 → モデル推論
        import torch
        inputs = self._processor(
            audio, sampling_rate=16000, return_tensors="pt"
        )
        input_features = inputs.input_features.to(device=self._device, dtype=self._model.dtype)

        with torch.no_grad():
            predicted_ids = self._model.generate(
                input_features,
                language="ja",
                task="transcribe",
            )

        text = self._processor.batch_decode(predicted_ids, skip_special_tokens=True)
        result = text[0] if text else ""

        self._reset_unload_timer()
        return result.strip()

    def unload(self) -> None:
        """モデルをVRAMから解放する。"""
        with self._lock:
            if self._model is not None:
                del self._model
                del self._processor
                self._model = None
                self._processor = None
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        dtype_map = {"float16": torch.float16, "float32": torch.float32}
        torch_dtype = dtype_map.get(self._torch_dtype, torch.float16)

        print(f"[WhisperEngine] Loading {self._model_name} on {self._device} ({self._torch_dtype})...")
        self._processor = WhisperProcessor.from_pretrained(self._model_name)
        self._model = WhisperForConditionalGeneration.from_pretrained(
            self._model_name,
            torch_dtype=torch_dtype,
        ).to(self._device)
        print(f"[WhisperEngine] Model loaded.")

    def _reset_unload_timer(self) -> None:
        if self._unload_timer:
            self._unload_timer.cancel()
        if self._unload_minutes > 0:
            self._unload_timer = threading.Timer(
                self._unload_minutes * 60, self.unload
            )
            self._unload_timer.daemon = True
            self._unload_timer.start()
