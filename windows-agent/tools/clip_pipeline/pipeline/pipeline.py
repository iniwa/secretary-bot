"""
パイプライン制御モジュール
全ステップをオーケストレーション（スリープ挿入、ログコールバック）
"""

import os
import time

from .config import SLEEP_BETWEEN_STEPS
from .preprocess_audio import preprocess_audio
from .transcribe import transcribe
from .analyze_audio import analyze_audio
from .emotion import analyze_emotion
from .highlight import detect_highlights
from .export_edl import export_edl
from .export_clips import export_clips


def _make_step_progress(progress_callback, step_start, step_weight):
    """ステップ内の (current, total) をグローバル fraction に変換するラッパーを生成"""
    if progress_callback is None:
        return None
    def step_progress(current, total, desc=""):
        if total > 0:
            frac = step_start + step_weight * min(current / total, 1.0)
        else:
            frac = step_start
        progress_callback(frac, desc)
    return step_progress


# 各ステップの重み（合計 1.0）
_STEP_WEIGHTS = [
    0.10,  # Step 0: 音声前処理
    0.25,  # Step 1: 文字起こし
    0.15,  # Step 2: 音声特徴量分析
    0.25,  # Step 3: 感情推定
    0.20,  # Step 4: ハイライト判定
    0.02,  # Step 5: EDL出力
    0.03,  # Step 6: クリップ切り出し
]


def run_pipeline(
    video_path: str,
    whisper_model: str,
    ollama_model: str,
    output_dir: str = "output",
    sleep_sec: float = None,
    top_n: int = 10,
    min_clip_sec: float = 30,
    max_clip_sec: float = 180,
    do_export_clips: bool = False,
    mic_track: int = None,
    use_demucs: bool = True,
    log=print,
    progress_callback=None,
    step_callback=None,
    whisper_download_root: str | None = None,
    cancel_flag=None,
):
    """
    パイプライン全体を実行する。

    Args:
        video_path: 動画ファイルパス
        whisper_model: Whisperモデル名
        ollama_model: Ollamaモデル名
        output_dir: 出力ディレクトリ
        sleep_sec: ステップ間スリープ秒数
        top_n: 最大候補数
        min_clip_sec: 最短クリップ秒数
        max_clip_sec: 最長クリップ秒数
        do_export_clips: MP4切り出しを行うか
        mic_track: マイクトラック番号 (0始まり、None=config値)
        use_demucs: 単一トラック時にDemucsを使用するか
        log: ログ出力関数
    """
    if sleep_sec is None:
        sleep_sec = SLEEP_BETWEEN_STEPS

    # バリデーション
    if not os.path.exists(video_path):
        log(f"エラー: ファイルが見つかりません: {video_path}")
        return

    os.makedirs(output_dir, exist_ok=True)
    log("処理を開始します...")

    # ステップ名（Pi 側 STEP_* に合わせる）
    _STEP_NAMES = ["preprocess", "transcribe", "analyze", "emotion", "highlight", "edl", "clips"]

    # ステップ開始位置を累積計算するヘルパー
    def _step_start(step_idx):
        return sum(_STEP_WEIGHTS[:step_idx])

    def _check_cancelled():
        if cancel_flag is not None and cancel_flag():
            raise RuntimeError("job cancelled")

    def _notify(step_idx, desc):
        _check_cancelled()
        if step_callback:
            step_callback(_STEP_NAMES[step_idx])
        if progress_callback:
            progress_callback(_step_start(step_idx), desc)

    def _step_done(step_idx):
        if progress_callback:
            progress_callback(_step_start(step_idx) + _STEP_WEIGHTS[step_idx], "")

    # ⓪ 音声前処理（マイクトラック抽出 or Demucs分離）
    _notify(0, "ステップ0: 音声前処理")
    log("--- ステップ0: 音声前処理 ---")
    voice_wav = preprocess_audio(video_path, output_dir, mic_track=mic_track, use_demucs=use_demucs, log=log)
    _step_done(0)
    time.sleep(sleep_sec)

    # ① 文字起こし
    _notify(1, "ステップ1: 文字起こし")
    log("--- ステップ1: 文字起こし ---")
    sp1 = _make_step_progress(progress_callback, _step_start(1), _STEP_WEIGHTS[1])
    transcript = transcribe(video_path, whisper_model, output_dir, wav_path=voice_wav, log=log, progress_callback=sp1, download_root=whisper_download_root)
    _step_done(1)
    time.sleep(sleep_sec)

    # ② 音声特徴量分析
    _notify(2, "ステップ2: 音声特徴量分析")
    log("--- ステップ2: 音声特徴量分析 ---")
    sp2 = _make_step_progress(progress_callback, _step_start(2), _STEP_WEIGHTS[2])
    audio_features = analyze_audio(video_path, output_dir, wav_path=voice_wav, log=log, progress_callback=sp2)
    _step_done(2)
    time.sleep(sleep_sec)

    # ③ 感情推察
    _notify(3, "ステップ3: 感情推察")
    log("--- ステップ3: 感情推察 ---")
    sp3 = _make_step_progress(progress_callback, _step_start(3), _STEP_WEIGHTS[3])
    emotions = analyze_emotion(video_path, transcript, output_dir, wav_path=voice_wav, log=log, progress_callback=sp3)
    _step_done(3)
    time.sleep(sleep_sec)

    # ④ ハイライト判定
    _notify(4, "ステップ4: ハイライト判定")
    log("--- ステップ4: ハイライト判定 ---")
    sp4 = _make_step_progress(progress_callback, _step_start(4), _STEP_WEIGHTS[4])
    highlights = detect_highlights(
        transcript, audio_features, emotions, ollama_model, output_dir, top_n=top_n, log=log, progress_callback=sp4,
    )

    # LLM出力のバリデーション（不正なタイムスタンプを除外）
    valid_highlights = []
    for h in highlights:
        start = h.get("start", -1)
        end = h.get("end", -1)
        # 文字列で返ってきた場合はfloatに変換を試みる
        try:
            start = float(start)
            end = float(end)
            h["start"] = start
            h["end"] = end
        except (TypeError, ValueError):
            log(f"  除外（型変換失敗）: start={start}, end={end}")
            continue
        if start < 0 or end < 0 or end <= start:
            log(f"  除外（範囲不正）: start={start}s, end={end}s")
            continue
        valid_highlights.append(h)
    if len(valid_highlights) < len(highlights):
        log(f"バリデーション: {len(highlights) - len(valid_highlights)}件の不正なハイライトを除外")
    highlights = valid_highlights

    # 隣接ハイライトのマージ（短いクリップを統合して見応えのあるシーンにする）
    if highlights:
        highlights.sort(key=lambda h: h["start"])
        merged = [highlights[0].copy()]
        for h in highlights[1:]:
            prev = merged[-1]
            gap = h["start"] - prev["end"]
            merged_duration = h["end"] - prev["start"]
            # 15秒以内のギャップ、かつマージ後が max_clip_sec 以内なら統合
            if gap <= 15 and merged_duration <= max_clip_sec:
                prev["end"] = h["end"]
                # reason は最初のものを保持（代表シーン）
            else:
                merged.append(h.copy())
        log(f"隣接マージ: {len(highlights)}件 → {len(merged)}件")
        highlights = merged

    # クリップ長のフィルタリング
    log(f"クリップ長フィルタ適用: {min_clip_sec}s ≤ duration ≤ {max_clip_sec}s（フィルタ前: {len(highlights)}件）")
    filtered_out = []
    filtered_in = []
    for h in highlights:
        duration = h.get("end", 0) - h.get("start", 0)
        if min_clip_sec <= duration <= max_clip_sec:
            filtered_in.append(h)
        else:
            filtered_out.append(h)
            log(f"  除外: start={h.get('start')}s, end={h.get('end')}s, "
                f"duration={duration:.1f}s, reason={h.get('reason', '(なし)')}")
    if filtered_out:
        log(f"  {len(filtered_out)}件がフィルタで除外されました")
    highlights = filtered_in
    log(f"クリップ長フィルタ後: {len(highlights)}件")
    _step_done(4)
    time.sleep(sleep_sec)

    # ⑤ EDL出力
    _notify(5, "ステップ5: EDL出力")
    log("--- ステップ5: EDL出力 ---")
    edl_path = export_edl(highlights, video_path, output_dir, log=log)
    _step_done(5)

    clip_paths: list[str] = []
    # ⑥ クリップ切り出し（オプション）
    if do_export_clips:
        time.sleep(sleep_sec)
        _notify(6, "ステップ6: クリップ切り出し")
        log("--- ステップ6: クリップ切り出し ---")
        sp6 = _make_step_progress(progress_callback, _step_start(6), _STEP_WEIGHTS[6])
        clip_paths = export_clips(highlights, video_path, output_dir, log=log, progress_callback=sp6) or []

    if progress_callback:
        progress_callback(1.0, "完了")
    log("完了！")

    return {
        "highlights": highlights,
        "edl_path": edl_path,
        "clip_paths": clip_paths,
        "transcript_path": os.path.join(output_dir, "transcript.json"),
    }
