"""
EDL出力モジュール
CMX 3600形式のEDLを生成（DaVinci Resolve対応）
"""

import os


def _seconds_to_timecode(seconds: float, fps: int = 30) -> str:
    """秒数をHH:MM:SS:FF形式のタイムコードに変換"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    f = int((seconds % 1) * fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def export_edl(
    highlights: list[dict],
    video_path: str,
    output_dir: str,
    fps: int = 30,
    log=print,
) -> str:
    """
    ハイライトからCMX 3600形式EDLを生成する。

    Args:
        highlights: [{"start": ..., "end": ..., "reason": ...}, ...]
        video_path: 元動画の絶対パス（EDLに記載）
        output_dir: 出力ディレクトリ
        fps: フレームレート
        log: ログ出力関数

    Returns:
        EDLファイルパス
    """
    log(f"EDL出力開始: {len(highlights)}件のハイライト, fps={fps}")
    if not highlights:
        log("ハイライトがありません。EDL出力をスキップします。")
        return ""

    edl_path = os.path.join(output_dir, "timeline.edl")
    video_abs = os.path.abspath(video_path)

    lines = ["TITLE: Clip Pipeline Highlights", "FCM: NON-DROP FRAME", ""]

    for i, h in enumerate(highlights, 1):
        src_in = _seconds_to_timecode(h["start"], fps)
        src_out = _seconds_to_timecode(h["end"], fps)

        # レコードタイムコード（連続配置）
        if i == 1:
            rec_start = 0.0
        else:
            rec_start = sum(
                prev["end"] - prev["start"] for prev in highlights[:i - 1]
            )
        rec_end = rec_start + (h["end"] - h["start"])
        rec_in = _seconds_to_timecode(rec_start, fps)
        rec_out = _seconds_to_timecode(rec_end, fps)

        lines.append(f"{i:03d}  AX       V     C        {src_in} {src_out} {rec_in} {rec_out}")
        lines.append(f"* FROM CLIP NAME: {video_abs}")
        if h.get("reason"):
            lines.append(f"* COMMENT: {h['reason']}")
        lines.append("")

    os.makedirs(output_dir, exist_ok=True)
    with open(edl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log(f"EDLファイルを出力しました: {edl_path}")
    return edl_path
