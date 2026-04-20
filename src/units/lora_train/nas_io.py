"""NAS 上の lora_datasets / lora_work ディレクトリ操作。

Pi はここで NAS のディレクトリ作成・削除・ファイル書き出しを行う。
Agent 側は別途キャッシュ同期で同じファイルをローカル SSD に取得する。
"""

from __future__ import annotations

import os
import re
import shutil
import uuid

from src.errors import ValidationError

# 注: lora.js にも同じパターンがあるが UX 用の事前チェック。サーバ側がソース。

ALLOWED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_IMAGE_BYTES = 16 * 1024 * 1024   # 16 MiB / 枚

# kohya / Windows / SMB 安全な英数 + `_` のみ
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,31}$")


def validate_project_name(name: str) -> str:
    """プロジェクト名（= トリガーワード）のバリデーション。

    - 2〜32 文字
    - 先頭は英数字、以降は英数字 + `_`
    - 全て小文字（kohya caption の重複混乱を避ける）
    """
    if not isinstance(name, str):
        raise ValidationError("project name must be a string")
    n = name.strip()
    if not _NAME_RE.match(n):
        raise ValidationError(
            "project name must be lowercase alphanumeric + underscore, 2-32 chars",
        )
    return n


def dataset_dir(nas_base: str, datasets_subdir: str, name: str) -> str:
    return os.path.join(nas_base, datasets_subdir, name)


def work_dir(nas_base: str, work_subdir: str, name: str) -> str:
    return os.path.join(nas_base, work_subdir, name)


def ensure_dataset_dir(nas_base: str, datasets_subdir: str, name: str) -> str:
    """`<NAS>/<datasets_subdir>/<name>/` を作成して絶対パスを返す。"""
    path = dataset_dir(nas_base, datasets_subdir, name)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_work_dirs(nas_base: str, work_subdir: str, name: str) -> dict[str, str]:
    """`<NAS>/<work_subdir>/<name>/{checkpoints,samples,logs}/` を作成。"""
    base = work_dir(nas_base, work_subdir, name)
    sub = {
        "base": base,
        "checkpoints": os.path.join(base, "checkpoints"),
        "samples": os.path.join(base, "samples"),
        "logs": os.path.join(base, "logs"),
    }
    for p in sub.values():
        os.makedirs(p, exist_ok=True)
    return sub


def remove_project_dirs(
    nas_base: str, datasets_subdir: str, work_subdir: str, name: str,
) -> None:
    """プロジェクトの NAS 上ファイルを丸ごと削除（プロジェクト DB 削除前に呼ぶ）。"""
    for path in (
        dataset_dir(nas_base, datasets_subdir, name),
        work_dir(nas_base, work_subdir, name),
    ):
        shutil.rmtree(path, ignore_errors=True)


def normalize_image_ext(filename: str | None) -> str:
    """`.PNG` → `.png`、`.jpeg` → そのまま。許可外なら ValidationError。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ValidationError(
            f"unsupported image extension '{ext}' "
            f"(allowed: {ALLOWED_IMAGE_EXTS})",
        )
    return ext


def write_dataset_image(target_dir: str, ext: str, content: bytes) -> str:
    """`<target_dir>/<uuid><ext>` に書き出して絶対パスを返す。

    呼び出し側で `ensure_dataset_dir` を済ませ、`normalize_image_ext` で得た
    拡張子と検証済み bytes を渡すこと（バッチ呼び出しで dir 確保を1回に抑える）。
    """
    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"image exceeds {MAX_IMAGE_BYTES} bytes (got {len(content)})",
        )
    out = os.path.join(target_dir, f"{uuid.uuid4().hex}{ext}")
    with open(out, "wb") as f:
        f.write(content)
    return out


def is_inside_dataset_dir(
    path: str, nas_base: str, datasets_subdir: str,
) -> bool:
    """DB 由来パスが NAS dataset ルート配下であることを確認（path traversal 防御）。"""
    root = os.path.realpath(os.path.join(nas_base, datasets_subdir))
    target = os.path.realpath(path)
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def remove_dataset_file(path: str) -> None:
    """画像本体と同名 caption (`<stem>.txt`) を併せて削除する。"""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    txt = os.path.splitext(path)[0] + ".txt"
    try:
        os.remove(txt)
    except FileNotFoundError:
        pass


# === LoRA 昇格 (models/loras への配置) ===

_CHECKPOINT_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+\.safetensors$")


def checkpoints_dir(nas_base: str, work_subdir: str, name: str) -> str:
    return os.path.join(work_dir(nas_base, work_subdir, name), "checkpoints")


def loras_dir(nas_base: str, loras_subdir: str, name: str) -> str:
    return os.path.join(nas_base, loras_subdir, name)


def list_checkpoints(nas_base: str, work_subdir: str, name: str) -> list[dict]:
    """`<NAS>/lora_work/<name>/checkpoints/` の .safetensors 一覧を返す。"""
    root = checkpoints_dir(nas_base, work_subdir, name)
    out: list[dict] = []
    if not os.path.isdir(root):
        return out
    for entry in os.scandir(root):
        if not entry.is_file():
            continue
        if not entry.name.lower().endswith(".safetensors"):
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        out.append({
            "filename": entry.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "path": entry.path,
        })
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def promote_checkpoint(
    nas_base: str, work_subdir: str, loras_subdir: str,
    name: str, *, checkpoint_filename: str,
    target_filename: str | None = None,
) -> str:
    """`lora_work/<name>/checkpoints/<file>` → `<loras_subdir>/<name>/<file>` へコピー。"""
    if not _CHECKPOINT_NAME_RE.match(checkpoint_filename):
        raise ValidationError(
            f"invalid checkpoint filename: {checkpoint_filename!r}",
        )
    src = os.path.join(
        checkpoints_dir(nas_base, work_subdir, name), checkpoint_filename,
    )
    if not os.path.isfile(src):
        raise ValidationError(f"checkpoint not found: {src}")

    dst_name = target_filename or checkpoint_filename
    if not _CHECKPOINT_NAME_RE.match(dst_name):
        raise ValidationError(
            f"invalid target filename: {dst_name!r}",
        )
    dst_dir = loras_dir(nas_base, loras_subdir, name)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, dst_name)
    shutil.copy2(src, dst)
    return dst
