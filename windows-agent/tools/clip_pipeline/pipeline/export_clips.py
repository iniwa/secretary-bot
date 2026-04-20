"""
クリップ切り出しモジュール（オプション）
FFmpeg -c copy で無劣化・高速切り出し
"""

import os
import subprocess


def export_clips(
    highlights: list[dict],
    video_path: str,
    output_dir: str,
    log=print,
    progress_callback=None,
) -> list[str]:
    """
    ハイライトからMP4クリップを切り出す。

    Args:
        highlights: [{"start": ..., "end": ..., "reason": ...}, ...]
        video_path: 元動画ファイルパス
        output_dir: 出力ディレクトリ
        log: ログ出力関数

    Returns:
        生成されたクリップファイルパスのリスト
    """
    log(f"クリップ切り出し開始: {len(highlights)}件のハイライト")
    if not highlights:
        log("ハイライトがありません。クリップ切り出しをスキップします。")
        return []

    clips_dir = os.path.join(output_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    paths = []
    for i, h in enumerate(highlights, 1):
        clip_path = os.path.join(clips_dir, f"clip_{i:03d}.mp4")
        start = h["start"]
        duration = h["end"] - h["start"]

        if duration <= 0:
            log(f"  クリップ {i} スキップ: duration={duration:.1f}s（不正な範囲）")
            continue

        log(f"  クリップ {i}/{len(highlights)} 切り出し中... ({start:.1f}s - {h['end']:.1f}s)")
        if progress_callback:
            progress_callback(i - 1, len(highlights), f"クリップ切り出し {i}/{len(highlights)}")

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(duration),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                clip_path,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            paths.append(clip_path)
        else:
            log(f"  クリップ {i} の切り出しに失敗: {result.stderr[:200]}")

    log(f"クリップ切り出し完了（{len(paths)}/{len(highlights)}件）")
    return paths
