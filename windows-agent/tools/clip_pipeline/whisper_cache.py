"""Whisper モデルキャッシュ管理 — NAS の Whisper モデルをローカル SSD に同期。

faster-whisper は CTranslate2 形式のモデルディレクトリ
（`model.bin` / `config.json` / `tokenizer.json` / `vocabulary.*` 等）を
`download_root` 配下の `models--Systran--faster-whisper-<name>` もしくは
サブディレクトリで参照する。ここでは NAS 側に
`<nas_whisper_base>/<model_name>/` として平置きされている前提で、
ローカル `<cache_root>/<model_name>/` へディレクトリ丸ごと再帰コピーする。

image_gen 側の cache_manager と違い ComfyUI のフォルダツリーに縛られないため
単純化した実装に留める（sha256 検証はファイル単位ではなく完了フラグで代用）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WhisperSyncProgress:
    sync_id: str
    model: str
    total_bytes: int = 0
    bytes_done: int = 0
    current_file: str = ""
    status: str = "queued"  # queued | running | done | failed | cancelled
    cancelled: bool = False
    error: dict | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict:
        return {
            "sync_id": self.sync_id,
            "model": self.model,
            "status": self.status,
            "total_bytes": self.total_bytes,
            "bytes_done": self.bytes_done,
            "current_file": self.current_file,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _dir_size_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def list_local_models(cache_root: str) -> list[str]:
    """ローカル SSD にキャッシュ済みのモデル名一覧を返す。"""
    if not os.path.isdir(cache_root):
        return []
    names: list[str] = []
    for entry in os.listdir(cache_root):
        p = os.path.join(cache_root, entry)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "model.bin")):
            names.append(entry)
    return sorted(names)


def list_nas_models(nas_whisper_base: str) -> list[str]:
    """NAS 上に配置済みのモデル名一覧を返す。"""
    if not nas_whisper_base or not os.path.isdir(nas_whisper_base):
        return []
    names: list[str] = []
    for entry in os.listdir(nas_whisper_base):
        p = os.path.join(nas_whisper_base, entry)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "model.bin")):
            names.append(entry)
    return sorted(names)


def is_model_cached(cache_root: str, model: str) -> bool:
    model_dir = os.path.join(cache_root, model)
    return os.path.isdir(model_dir) and os.path.exists(os.path.join(model_dir, "model.bin"))


async def run_whisper_cache_sync(
    prog: WhisperSyncProgress,
    nas_whisper_base: str,
    cache_root: str,
    publish,
) -> None:
    """NAS → ローカルへモデルディレクトリを同期。

    publish(event: str, data: dict) は非同期コールバックで、
    SSE 購読者へイベントを配信するのに使う。
    """
    model = prog.model
    src_dir = os.path.join(nas_whisper_base, model)
    dst_dir = os.path.join(cache_root, model)

    prog.status = "running"
    await publish("status", {"status": "running"})

    try:
        if not nas_whisper_base:
            raise FileNotFoundError("nas_whisper_base is not configured")
        if not os.path.isdir(src_dir):
            raise FileNotFoundError(f"source not found: {src_dir}")
        if not os.path.exists(os.path.join(src_dir, "model.bin")):
            raise FileNotFoundError(f"model.bin missing under: {src_dir}")

        prog.total_bytes = await asyncio.to_thread(_dir_size_bytes, src_dir)
        os.makedirs(cache_root, exist_ok=True)

        # 既にキャッシュ済みなら skip（サイズが NAS とほぼ一致する場合のみ）
        if is_model_cached(cache_root, model):
            local_size = await asyncio.to_thread(_dir_size_bytes, dst_dir)
            if local_size == prog.total_bytes and prog.total_bytes > 0:
                prog.bytes_done = prog.total_bytes
                prog.status = "done"
                prog.finished_at = time.time()
                await publish("status", {"status": "done", "skipped": True})
                await publish("done", {})
                return

        tmp_dir = dst_dir + f".tmp-{uuid.uuid4().hex[:8]}"
        # NAS → tmp へ再帰コピー（途中キャンセル対応のため手書きループ）
        for root, _dirs, files in os.walk(src_dir):
            if prog.cancelled:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                prog.status = "cancelled"
                await publish("status", {"status": "cancelled"})
                await publish("done", {})
                return
            rel = os.path.relpath(root, src_dir)
            out_root = os.path.join(tmp_dir, rel) if rel != "." else tmp_dir
            os.makedirs(out_root, exist_ok=True)
            for name in files:
                if prog.cancelled:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    prog.status = "cancelled"
                    await publish("status", {"status": "cancelled"})
                    await publish("done", {})
                    return
                src_path = os.path.join(root, name)
                dst_path = os.path.join(out_root, name)
                prog.current_file = os.path.relpath(src_path, src_dir)
                await publish("progress", {
                    "current_file": prog.current_file,
                    "bytes_done": prog.bytes_done,
                    "total_bytes": prog.total_bytes,
                })
                await asyncio.to_thread(_copy_with_chunks, src_path, dst_path, prog)

        # 原子的に置換
        if os.path.isdir(dst_dir):
            shutil.rmtree(dst_dir, ignore_errors=True)
        os.replace(tmp_dir, dst_dir)

        prog.status = "done"
        prog.finished_at = time.time()
        await publish("status", {"status": "done"})
        await publish("done", {})
    except Exception as e:
        prog.status = "failed"
        prog.error = {
            "error_class": "CacheSyncError",
            "message": str(e),
            "retryable": True,
        }
        prog.finished_at = time.time()
        await publish("error", prog.error)
        await publish("done", {})


def _copy_with_chunks(src: str, dst: str, prog: WhisperSyncProgress, chunk: int = 4 * 1024 * 1024) -> None:
    """4MB チャンクで同期コピー。進捗は prog.bytes_done を更新。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            if prog.cancelled:
                break
            buf = fin.read(chunk)
            if not buf:
                break
            fout.write(buf)
            prog.bytes_done += len(buf)
