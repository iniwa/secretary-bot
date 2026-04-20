"""
音声特徴量分析モジュール
librosaを使用して音量(RMS)・ピッチ(F0)・有声フレーム比率を分析
"""

import json
import os
import subprocess
import tempfile

import librosa
import numpy as np

from .config import AUDIO_SEGMENT_SEC


def extract_audio(video_path: str, log=print) -> str:
    """動画からWAVを一時ファイルとして抽出"""
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


def analyze_audio(video_path: str, output_dir: str, segment_sec: float = None, wav_path: str = None, log=print, progress_callback=None) -> list[dict]:
    """
    動画の音声特徴量を分析する。

    Args:
        video_path: 動画ファイルパス
        output_dir: 出力ディレクトリ
        segment_sec: 分析単位の秒数
        wav_path: 前処理済みWAVパス (指定時は音声抽出をスキップ)
        log: ログ出力関数

    Returns:
        [{"start": 0.0, "end": 5.0, "rms": 0.12, "pitch_mean": 220.0, "voicing_ratio": 0.65}, ...]
    """
    if segment_sec is None:
        segment_sec = AUDIO_SEGMENT_SEC

    features_path = os.path.join(output_dir, "audio_features.json")
    if os.path.exists(features_path):
        log("既存の音声特徴量を使用します")
        with open(features_path, encoding="utf-8") as f:
            return json.load(f)

    if wav_path and os.path.exists(wav_path):
        log("前処理済み音声を使用します")
        own_wav = False
    else:
        log("音声を抽出中...")
        wav_path = extract_audio(video_path, log=log)
        own_wav = True

    try:
        log("音声特徴量を分析中...")
        y, sr = librosa.load(wav_path, sr=16000)
        duration = librosa.get_duration(y=y, sr=sr)
        hop_length = 512

        # RMS (音量)
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)

        # ピッチ (F0) — yin で高速推定（チャンク分割でメモリ対策、オーバーラップで境界不連続を緩和）
        chunk_sec = 300  # 5分ごとに処理
        overlap_sec = 10  # チャンク境界のオーバーラップ
        chunk_samples = int(chunk_sec * sr)
        overlap_samples = int(overlap_sec * sr)
        n_chunks = (len(y) + chunk_samples - 1) // chunk_samples
        f0_chunks = []
        log(f"ピッチ推定中（{n_chunks}チャンク、オーバーラップ{overlap_sec}秒）...")
        for ci, start_sample in enumerate(range(0, len(y), chunk_samples)):
            end_sample = min(start_sample + chunk_samples, len(y))
            # 先頭チャンク以外はオーバーラップ分を前方に拡張
            if ci > 0:
                actual_start = max(0, start_sample - overlap_samples)
            else:
                actual_start = start_sample
            chunk = y[actual_start:end_sample]
            f0_c = librosa.yin(chunk, fmin=50, fmax=500, sr=sr, hop_length=hop_length)
            # オーバーラップ分のフレームを破棄（先頭チャンク以外）
            if ci > 0:
                discard_samples = start_sample - actual_start
                discard_frames = discard_samples // hop_length
                f0_c = f0_c[discard_frames:]
            f0_chunks.append(f0_c)
            log(f"  ピッチ推定 {ci + 1}/{n_chunks} チャンク完了")
        f0 = np.concatenate(f0_chunks)
        # yin は有声/無声を区別しないため、妥当な範囲外を無声と判定
        voiced_flag = (f0 >= 55) & (f0 <= 480)
        f0[~voiced_flag] = np.nan
        f0_times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop_length)

        # セグメント単位で集約
        features = []
        t = 0.0
        while t < duration:
            seg_end = min(t + segment_sec, duration)

            # RMS平均
            rms_mask = (rms_times >= t) & (rms_times < seg_end)
            seg_rms = float(np.mean(rms[rms_mask])) if np.any(rms_mask) else 0.0

            # ピッチ平均（有声部分のみ）
            f0_mask = (f0_times >= t) & (f0_times < seg_end)
            seg_f0 = f0[f0_mask]
            seg_f0_voiced = seg_f0[~np.isnan(seg_f0)]
            pitch_mean = float(np.mean(seg_f0_voiced)) if len(seg_f0_voiced) > 0 else 0.0

            # 有声フレーム比率（voicing_ratio: 有声フレームの割合、0.0〜1.0）
            seg_voiced = voiced_flag[f0_mask] if np.any(f0_mask) else np.array([])
            voicing_ratio = float(np.mean(seg_voiced)) if len(seg_voiced) > 0 else 0.0

            features.append({
                "start": round(t, 2),
                "end": round(seg_end, 2),
                "rms": round(seg_rms, 4),
                "pitch_mean": round(pitch_mean, 1),
                "voicing_ratio": round(voicing_ratio, 3),
            })
            if progress_callback:
                progress_callback(seg_end, duration, f"音声特徴量分析 {seg_end:.0f}/{duration:.0f}秒")
            t = seg_end

        log(f"音声特徴量分析完了（{len(features)}セグメント）")

        os.makedirs(output_dir, exist_ok=True)
        with open(features_path, "w", encoding="utf-8") as f:
            json.dump(features, f, ensure_ascii=False, indent=2)

        return features
    finally:
        if own_wav:
            os.unlink(wav_path)
