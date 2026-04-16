"""モダリティ（出力メディア種別）関連ヘルパ。

Phase 3.5 で `image_jobs` を `generation_jobs` に汎用化した際に導入。
Workflow カテゴリや出力ファイル拡張子から、ジョブの modality を判定する。

modality 値域:
  image  -- 静止画（png/jpg/webp ほか）
  video  -- 動画（mp4/webm/gif 連番）
  audio  -- 音声（wav/mp3/flac）
"""
from __future__ import annotations

MODALITY_IMAGE = "image"
MODALITY_VIDEO = "video"
MODALITY_AUDIO = "audio"

ALLOWED_MODALITIES = frozenset({MODALITY_IMAGE, MODALITY_VIDEO, MODALITY_AUDIO})

# Workflow カテゴリ → modality のマッピング
# 未登録カテゴリは "image" にフォールバック（既存 t2i_* / i2i_* の互換維持）
_CATEGORY_TO_MODALITY: dict[str, str] = {
    "t2i":   MODALITY_IMAGE,
    "i2i":   MODALITY_IMAGE,
    "hires": MODALITY_IMAGE,
    "t2v":   MODALITY_VIDEO,
    "i2v":   MODALITY_VIDEO,
    "v2v":   MODALITY_VIDEO,
    "tts":   MODALITY_AUDIO,
    "asr":   MODALITY_AUDIO,
}

# 出力ファイル拡張子 → 出力メディア種別
_EXT_TO_KIND: dict[str, str] = {
    ".png":  "image",
    ".jpg":  "image",
    ".jpeg": "image",
    ".webp": "image",
    ".bmp":  "image",
    ".gif":  "image",
    ".mp4":  "video",
    ".webm": "video",
    ".mov":  "video",
    ".mkv":  "video",
    ".wav":  "audio",
    ".mp3":  "audio",
    ".flac": "audio",
    ".ogg":  "audio",
}


def category_to_modality(category: str | None) -> str:
    """Workflow.category → modality。未知値は 'image'。"""
    if not category:
        return MODALITY_IMAGE
    return _CATEGORY_TO_MODALITY.get(category.lower(), MODALITY_IMAGE)


def path_to_kind(path: str) -> str:
    """出力ファイルパスから media kind を判定（image/video/audio）。"""
    import os
    _, ext = os.path.splitext(path or "")
    return _EXT_TO_KIND.get(ext.lower(), "image")


def normalize_modality(value: str | None) -> str:
    """外部入力を正規化。不正値は 'image' にフォールバック。"""
    if value and value.lower() in ALLOWED_MODALITIES:
        return value.lower()
    return MODALITY_IMAGE
