"""
音声前処理モジュール
OBSマルチトラック録画からのマイクトラック抽出、
または Demucs によるボーカル分離を行い、クリーンな音声WAVを生成する
"""

import json
import os
import shutil
import subprocess
import tempfile

from .config import DEMUCS_MODEL, MIC_TRACK_INDEX


def _probe_audio_streams(video_path: str) -> list[dict]:
    """ffprobe で音声ストリーム情報を取得"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-select_streams", "a",
            video_path,
        ],
        capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    return data.get("streams", [])


def _extract_track(video_path: str, track_index: int, output_path: str, log=print):
    """指定トラックを 16kHz mono WAV として抽出"""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-map", f"0:a:{track_index}",
            "-ac", "1", "-ar", "16000", "-vn",
            output_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"ffmpeg トラック抽出エラー (code={result.returncode}): {result.stderr[:300]}")


def _extract_mixed(video_path: str, output_path: str, log=print):
    """全音声を混合して 16kHz mono WAV として抽出"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", output_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"ffmpeg 混合音声抽出エラー (code={result.returncode}): {result.stderr[:300]}")


def _run_demucs(wav_path: str, output_dir: str, model: str, log) -> str:
    """Demucs でボーカル分離し、vocals WAV のパスを返す

    torchaudio 2.10+ は torchcodec 経由の保存を要求するが、
    Windows では FFmpeg shared DLL が必要で動作しないことが多い。
    そのため CLI ではなくライブラリとして呼び出し、soundfile で保存する。
    """
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile
    from demucs.pretrained import get_model

    log(f"Demucs ({model}) でボーカル分離中（CPU）...")

    # Demucs は cuDNN 9.x との組み合わせでクラッシュするため CPU 固定
    demucs_model = get_model(model)
    demucs_model.to("cpu")
    demucs_model.eval()

    # 音声読み込み
    wav = AudioFile(wav_path).read(streams=0, samplerate=demucs_model.samplerate, channels=demucs_model.audio_channels)
    ref = wav.mean(0)
    std_val = ref.std()
    if std_val < 1e-7:
        log("警告: 入力音声がほぼ無音です。正規化をスキップします。")
        std_val = 1.0
    wav = (wav - ref.mean()) / std_val

    # 分離実行
    with torch.no_grad():
        sources = apply_model(demucs_model, wav[None], progress=True)[0]

    # vocals を取得して保存
    sources = sources * std_val + ref.mean()
    vocals_idx = demucs_model.sources.index("vocals")
    vocals = sources[vocals_idx].cpu().numpy().T  # (samples, channels)

    stem_name = os.path.splitext(os.path.basename(wav_path))[0]
    out_dir = os.path.join(output_dir, model, stem_name)
    os.makedirs(out_dir, exist_ok=True)
    vocals_path = os.path.join(out_dir, "vocals.wav")

    sf.write(vocals_path, vocals, demucs_model.samplerate)
    log("Demucs ボーカル分離完了")

    # GPU メモリ解放
    del demucs_model, sources, wav
    torch.cuda.empty_cache()

    return vocals_path


def preprocess_audio(
    video_path: str,
    output_dir: str,
    mic_track: int = None,
    use_demucs: bool = True,
    log=print,
) -> str:
    """
    動画からクリーンなマイク音声を取得する。

    - マルチトラック → 指定トラックを抽出
    - 単一トラック + use_demucs → Demucs でボーカル分離
    - 単一トラック + not use_demucs → 混合音声をそのまま使用

    Args:
        video_path: 動画ファイルパス
        output_dir: 出力ディレクトリ
        mic_track: マイクトラック番号 (0始まり)
        use_demucs: 単一トラック時に Demucs を使用するか
        log: ログ出力関数

    Returns:
        前処理済み WAV ファイルパス
    """
    if mic_track is None:
        mic_track = MIC_TRACK_INDEX

    os.makedirs(output_dir, exist_ok=True)
    voice_path = os.path.join(output_dir, "voice.wav")

    # キャッシュチェック
    if os.path.exists(voice_path):
        log("既存の前処理済み音声を使用します")
        return voice_path

    # 音声ストリーム情報を取得
    streams = _probe_audio_streams(video_path)
    num_tracks = len(streams)
    log(f"音声トラック数: {num_tracks}")

    if num_tracks == 0:
        raise RuntimeError("音声トラックが見つかりません")

    # トラック情報をログ表示
    for i, s in enumerate(streams):
        channels = s.get("channels", "?")
        layout = s.get("channel_layout", "不明")
        codec = s.get("codec_name", "不明")
        log(f"  トラック{i}: {codec}, {channels}ch, レイアウト={layout}")

    if num_tracks > 1:
        # マルチトラック → マイクトラック抽出
        if mic_track >= num_tracks:
            log(f"警告: トラック{mic_track}が存在しません（{num_tracks}トラック中）。トラック0を使用します")
            mic_track = 0
        log(f"トラック{mic_track}をマイク音声として抽出中...")
        _extract_track(video_path, mic_track, voice_path, log=log)
    elif use_demucs:
        # 単一トラック + Demucs
        log("単一トラック検出 → Demucs でボーカル分離します")
        tmp_dir = tempfile.mkdtemp(prefix="demucs_")
        tmp_wav = os.path.join(tmp_dir, "mixed.wav")
        try:
            _extract_mixed(video_path, tmp_wav, log=log)
            vocals_path = _run_demucs(tmp_wav, tmp_dir, DEMUCS_MODEL, log)

            # 16kHz mono に変換して voice.wav に保存
            log("分離音声を 16kHz mono に変換中...")
            conv_result = subprocess.run(
                ["ffmpeg", "-y", "-i", vocals_path, "-ac", "1", "-ar", "16000", voice_path],
                capture_output=True, text=True,
            )
            if conv_result.returncode != 0:
                log(f"ffmpeg 変換エラー (code={conv_result.returncode}): {conv_result.stderr[:300]}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        # 単一トラック + Demucs 無効 → 混合音声をそのまま使用
        log("混合音声を抽出中（Demucs無効）...")
        _extract_mixed(video_path, voice_path, log=log)

    if os.path.exists(voice_path):
        size_mb = os.path.getsize(voice_path) / (1024 * 1024)
        log(f"音声前処理完了: {voice_path} ({size_mb:.1f} MB)")
    else:
        log(f"警告: 音声前処理の出力ファイルが見つかりません: {voice_path}")
    return voice_path
