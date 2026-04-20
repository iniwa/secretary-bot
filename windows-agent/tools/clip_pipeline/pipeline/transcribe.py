"""
文字起こしモジュール
faster-whisper を使用して動画ファイルからタイムスタンプ付きテキストを抽出
"""

import json
import os
import subprocess
import tempfile

from faster_whisper import WhisperModel

# CTranslate2 の WhisperModel は __del__ でセグフォする (ctranslate2 4.7.1)
# unload_model() でVRAMを解放し、オブジェクト自体はここで保持して __del__ を防ぐ
# プロセス終了時にGCが走りセグフォするが、全機能は正常完了後なので実害なし
_kept_models: list = []


def _extract_audio(video_path: str, log=print) -> str:
    """動画から16kHz mono WAVを一時ファイルとして抽出"""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", tmp.name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log(f"ffmpeg 音声抽出エラー (code={result.returncode}): {result.stderr[:300]}")
        return tmp.name
    except Exception:
        os.unlink(tmp.name)
        raise


def transcribe(video_path: str, model_name: str, output_dir: str, wav_path: str = None, log=print, progress_callback=None, download_root: str | None = None) -> list[dict]:
    """
    動画ファイルを文字起こしする。

    Args:
        video_path: 動画ファイルパス
        model_name: faster-whisperモデル名 (e.g. "large-v3", "large-v3-turbo", "base")
        output_dir: 出力ディレクトリ
        wav_path: 前処理済みWAVパス (指定時は音声抽出をスキップ)
        log: ログ出力関数

    Returns:
        [{"start": 12.3, "end": 15.7, "text": "やばい！倒した！"}, ...]
    """
    transcript_path = os.path.join(output_dir, "transcript.json")

    if os.path.exists(transcript_path):
        log("既存の文字起こし結果を使用します")
        with open(transcript_path, encoding="utf-8") as f:
            return json.load(f)

    if wav_path and os.path.exists(wav_path):
        log("前処理済み音声を使用します")
        own_wav = False
    else:
        log("音声を抽出中...")
        wav_path = _extract_audio(video_path, log=log)
        own_wav = True

    try:
        import torch
        if torch.cuda.is_available():
            device, compute_type = "cuda", "float16"
        else:
            device, compute_type = "cpu", "int8"
            log("警告: CUDA が利用できません。CPU モードで実行します（低速）")
        log(f"faster-whisper ({model_name}) をロード中... (device={device})")
        kwargs = {"device": device, "compute_type": compute_type}
        if download_root:
            kwargs["download_root"] = download_root
        model = WhisperModel(model_name, **kwargs)

        log("文字起こし中...")
        segments_iter, info = model.transcribe(
            wav_path,
            language="ja",
            beam_size=5,
            vad_filter=True,
        )

        segments = []
        total_duration = info.duration or 0
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                segments.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": text,
                })
            if progress_callback and total_duration > 0:
                progress_callback(seg.end, total_duration, f"文字起こし {seg.end:.0f}/{total_duration:.0f}秒")

        log(f"文字起こし完了（{len(segments)}セグメント、言語: {info.language}, 確率: {info.language_probability:.2f}）")

        # CTranslate2 の WhisperModel は __del__ でセグフォする (ctranslate2 4.7.1)
        # unload_model() でVRAMを解放し、オブジェクトはモジュールレベルで保持
        try:
            model.model.unload_model()
        except Exception:
            pass
        _kept_models.append(model)
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        os.makedirs(output_dir, exist_ok=True)
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)

        return segments
    finally:
        if own_wav:
            os.unlink(wav_path)
