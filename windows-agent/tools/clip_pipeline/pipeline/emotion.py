"""
感情推察モジュール
emotion2vec+ (FunASR) を使用して
各文字起こしセグメントの感情を推定（音声韻律ベース）
"""

import json
import os
import subprocess
import tempfile

import librosa
import numpy as np

_MODEL_ID = "iic/emotion2vec_plus_base"
_SAMPLE_RATE = 16000


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


def analyze_emotion(
    video_path: str,
    segments: list[dict],
    output_dir: str,
    confidence_threshold: float = 0.0,
    wav_path: str = None,
    log=print,
    progress_callback=None,
) -> list[dict]:
    """
    音声セグメントごとに感情ラベルを付与する。

    Args:
        video_path: 動画ファイルパス
        segments: 文字起こしセグメント [{"start": ..., "end": ..., "text": ...}, ...]
        output_dir: 出力ディレクトリ
        confidence_threshold: この値未満はneutralに強制 (デフォルト0.0=無効)
        wav_path: 前処理済みWAVパス (指定時は音声抽出をスキップ)
        log: ログ出力関数

    Returns:
        [{"start": 12.3, "end": 15.7, "emotion": "happy", "confidence": 0.85}, ...]
    """
    emotions_path = os.path.join(output_dir, "emotions.json")

    if os.path.exists(emotions_path):
        log("既存の感情分析結果を使用します")
        with open(emotions_path, encoding="utf-8") as f:
            return json.load(f)

    if not segments:
        log("文字起こしセグメントがないため感情分析をスキップします")
        return []

    if wav_path and os.path.exists(wav_path):
        log("前処理済み音声を使用します")
        own_wav = False
    else:
        log("音声を抽出中...")
        wav_path = _extract_audio(video_path, log=log)
        own_wav = True

    try:
        from funasr import AutoModel

        log(f"感情推定モデル ({_MODEL_ID}) をロード中...")
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = AutoModel(model=_MODEL_ID, device=device)

        log("音声を読み込み中...")
        y, sr = librosa.load(wav_path, sr=_SAMPLE_RATE)

        emotions = []
        total = len(segments)
        log(f"感情推定中（{total}セグメント）...")

        for i, seg in enumerate(segments):
            start_sample = int(seg["start"] * sr)
            end_sample = int(seg["end"] * sr)
            audio_slice = y[start_sample:end_sample]

            if len(audio_slice) < sr * 0.1:  # 0.1秒未満はスキップ
                emotions.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "emotion": "neutral",
                    "confidence": 0.0,
                })
                continue

            try:
                res = model.generate(
                    audio_slice,
                    granularity="utterance",
                    extract_embedding=False,
                )
                # res[0] = {"labels": [...], "scores": [...], "feats": ...}
                result = res[0]
                labels = result["labels"]
                scores = result["scores"]

                best_idx = int(np.argmax(scores))
                emotion = labels[best_idx].lower()
                confidence = round(float(scores[best_idx]), 3)

                if confidence < confidence_threshold:
                    emotion = "neutral"

                emotions.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "emotion": emotion,
                    "confidence": confidence,
                })
            except Exception as e:
                log(f"  セグメント{i}で推定エラー: {e}")
                emotions.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "emotion": "neutral",
                    "confidence": 0.0,
                })

            if progress_callback:
                progress_callback(i + 1, total, f"感情推定 {i + 1}/{total}")
            if (i + 1) % 50 == 0:
                log(f"  {i + 1}/{total} セグメント処理済み")

        log(f"感情推定完了（{len(emotions)}セグメント）")

        # モデルを明示的に解放
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        os.makedirs(output_dir, exist_ok=True)
        with open(emotions_path, "w", encoding="utf-8") as f:
            json.dump(emotions, f, ensure_ascii=False, indent=2)

        return emotions
    finally:
        if own_wav:
            os.unlink(wav_path)
