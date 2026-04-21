"""
パイプライン制御モジュール
全ステップをオーケストレーション（スリープ挿入、ログコールバック）
"""

import concurrent.futures
import os
import time

from .analyze_audio import analyze_audio
from .config import SLEEP_BETWEEN_STEPS
from .emotion import analyze_emotion
from .export_clips import export_clips
from .export_edl import export_edl
from .highlight import detect_highlights
from .preprocess_audio import preprocess_audio
from .transcribe import transcribe


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
        min_clip_sec: 最短クリップ秒数（満たない場合は前後に時間を足して確保）
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

    # ①+② 文字起こし（GPU）+ 音声特徴量分析（CPU）を並走
    # transcribe は GPU、analyze_audio は librosa 純 CPU なのでリソース非競合。
    # 進捗は transcribe を代表ドライバとし、ステップ1+2 の weight を合算した 1 本の bar にまとめる。
    _notify(1, "ステップ1+2: 文字起こし+音声特徴量分析（並列実行）")
    log("--- ステップ1+2: 文字起こし + 音声特徴量分析（並列） ---")
    combined_weight = _STEP_WEIGHTS[1] + _STEP_WEIGHTS[2]
    sp_main = _make_step_progress(progress_callback, _step_start(1), combined_weight)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="clip_pipeline"
    ) as pool:
        f_transcribe = pool.submit(
            transcribe,
            video_path, whisper_model, output_dir,
            wav_path=voice_wav, log=log,
            progress_callback=sp_main,
            download_root=whisper_download_root,
        )
        f_analyze = pool.submit(
            analyze_audio,
            video_path, output_dir,
            wav_path=voice_wav, log=log,
            progress_callback=None,  # transcribe 側で進捗を代表
        )
        # transcribe の方が一般に長いが、順序は保証しない。例外は先に伝播させる。
        transcript = f_transcribe.result()
        # transcribe 完了後に analyze が残っていれば step 表示を analyze に切り替え
        if step_callback and not f_analyze.done():
            step_callback(_STEP_NAMES[2])
        audio_features = f_analyze.result()
    _check_cancelled()
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

    # 隣接ハイライトのマージ（15秒以内のギャップで統合、上限なし）
    if highlights:
        highlights.sort(key=lambda h: h["start"])
        merged = [highlights[0].copy()]
        for h in highlights[1:]:
            prev = merged[-1]
            gap = h["start"] - prev["end"]
            if gap <= 15:
                # reason は最初のものを保持（代表シーン）
                prev["end"] = h["end"]
            else:
                merged.append(h.copy())
        log(f"隣接マージ: {len(highlights)}件 → {len(merged)}件")
        highlights = merged

    # 最低時間パディング（min_clip_sec に満たない場合は前後を等分に拡張）
    # start<0 になる場合は 0 にクランプし、不足分を後ろに振り替える。
    if min_clip_sec > 0 and highlights:
        padded_count = 0
        for h in highlights:
            start = h["start"]
            end = h["end"]
            duration = end - start
            if duration >= min_clip_sec:
                continue
            pad = min_clip_sec - duration
            pad_before = pad / 2
            pad_after = pad - pad_before
            new_start = max(0.0, start - pad_before)
            overflow = pad_before - (start - new_start)
            new_end = end + pad_after + overflow
            log(f"  延長: {start:.1f}s-{end:.1f}s ({duration:.1f}s) "
                f"→ {new_start:.1f}s-{new_end:.1f}s ({new_end - new_start:.1f}s)")
            h["start"] = new_start
            h["end"] = new_end
            padded_count += 1
        if padded_count:
            log(f"最低時間パディング: {padded_count}件を {min_clip_sec}s まで延長")
    log(f"最終ハイライト: {len(highlights)}件")
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
