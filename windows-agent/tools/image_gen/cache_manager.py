"""ローカル SSD キャッシュ管理（Phase 1: sha256 検証・原子的配置のみ）。

LRU や cache manifest エクスポートは Phase 2 で追加予定。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field

# models サブディレクトリ許可リスト（API 仕様 §3.2 files[].type）
_ALLOWED_TYPES = {
    "checkpoints", "loras", "vae", "embeddings",
    "upscale_models", "clip", "controlnet",
}


@dataclass
class CacheEntry:
    type: str
    filename: str
    size: int
    sha256: str | None
    mtime: float
    path: str


def cache_models_root(cache_root: str) -> str:
    return os.path.join(cache_root, "models")


def resolve_local_path(cache_root: str, type_: str, filename: str) -> str:
    if type_ not in _ALLOWED_TYPES:
        raise ValueError(f"invalid type: {type_}")
    # パストラバーサル防止
    safe = filename.replace("\\", "/").lstrip("/")
    if ".." in safe.split("/"):
        raise ValueError(f"invalid filename: {filename}")
    return os.path.join(cache_models_root(cache_root), type_, *safe.split("/"))


def sha256_of_file(path: str, block: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sidecar_sha256(path: str) -> str | None:
    side = path + ".sha256"
    if not os.path.exists(side):
        return None
    try:
        with open(side, encoding="utf-8") as f:
            return (f.readline() or "").strip().lower() or None
    except Exception:
        return None


def iter_cache_entries(cache_root: str) -> list[CacheEntry]:
    entries: list[CacheEntry] = []
    root = cache_models_root(cache_root)
    if not os.path.isdir(root):
        return entries
    for type_ in _ALLOWED_TYPES:
        type_dir = os.path.join(root, type_)
        if not os.path.isdir(type_dir):
            continue
        for dirpath, _, files in os.walk(type_dir):
            for name in files:
                if name.endswith(".sha256"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, type_dir).replace("\\", "/")
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                entries.append(CacheEntry(
                    type=type_,
                    filename=rel,
                    size=st.st_size,
                    sha256=sidecar_sha256(full),
                    mtime=st.st_mtime,
                    path=full,
                ))
    return entries


def cache_usage_gb(cache_root: str) -> float:
    total = 0
    for e in iter_cache_entries(cache_root):
        total += e.size
    return round(total / (1024 ** 3), 2)


@dataclass
class SyncProgress:
    sync_id: str
    status: str = "queued"   # queued / running / done / failed / cancelled
    total_bytes: int = 0
    bytes_done: int = 0
    current_file: str | None = None
    mbps: float = 0.0
    error: dict | None = None
    file_results: list[dict] = field(default_factory=list)
    cancelled: bool = False

    def snapshot(self) -> dict:
        pct = 0
        if self.total_bytes > 0:
            pct = int(self.bytes_done * 100 / self.total_bytes)
        return {
            "sync_id": self.sync_id,
            "status": self.status,
            "percent": pct,
            "bytes_done": self.bytes_done,
            "bytes_total": self.total_bytes,
            "current_file": self.current_file,
            "mbps": round(self.mbps, 2),
            "error": self.error,
        }


def copy_with_progress(
    src: str,
    dst: str,
    progress: SyncProgress,
    on_chunk: Callable[[int], None],
    block: int = 4 * 1024 * 1024,
) -> None:
    """temp → rename の原子的コピー。途中で progress.cancelled を検知したら中断。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    start = time.time()
    bytes_done_local = 0
    try:
        with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            while True:
                if progress.cancelled:
                    raise RuntimeError("cancelled")
                chunk = fsrc.read(block)
                if not chunk:
                    break
                fdst.write(chunk)
                bytes_done_local += len(chunk)
                on_chunk(len(chunk))
                elapsed = max(time.time() - start, 0.001)
                progress.mbps = (bytes_done_local / (1024 * 1024)) / elapsed
        os.replace(tmp, dst)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def write_sha256_sidecar(path: str, sha: str) -> None:
    side = path + ".sha256"
    tmp = side + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(sha.lower())
    os.replace(tmp, side)


def free_space_bytes(path: str) -> int:
    try:
        usage = shutil.disk_usage(path)
        return usage.free
    except OSError:
        return 0
