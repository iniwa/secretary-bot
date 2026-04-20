"""LoRA dataset NAS → ローカル SSD 同期。

kohya の学習時に NAS からの直接読み取りは遅いので、`<root>/lora_data/<name>/`
配下に dataset と work ファイルをコピーする。加えて、TOML 内のパスを NAS →
ローカルに書き換えた `dataset.local.toml` / `config.local.toml` を生成し、
Phase F の学習エンドポイントから参照できるようにしておく。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid

from .setup_manager import SetupTask, _append, _tasks


def _lora_local_root(root: str) -> str:
    return os.path.join(root, "lora_data")


def local_project_dirs(root: str, project_name: str) -> dict:
    base = os.path.join(_lora_local_root(root), project_name)
    return {
        "base": base,
        "images": os.path.join(base, "images"),
        "work": os.path.join(base, "work"),
    }


async def run_lora_sync(
    root: str,
    *,
    project_name: str,
    nas_dataset_dir: str,
    nas_work_dir: str,
) -> SetupTask:
    tid = f"lorasync_{uuid.uuid4().hex[:16]}"
    task = SetupTask(task_id=tid, kind="lora_sync")
    _tasks[tid] = task
    asyncio.create_task(
        _do_lora_sync(task, root, project_name, nas_dataset_dir, nas_work_dir),
    )
    return task


async def _do_lora_sync(
    task: SetupTask, root: str, project_name: str,
    nas_dataset_dir: str, nas_work_dir: str,
) -> None:
    try:
        if not os.path.isdir(nas_dataset_dir):
            raise RuntimeError(f"nas dataset dir not found: {nas_dataset_dir}")
        if not os.path.isdir(nas_work_dir):
            raise RuntimeError(f"nas work dir not found: {nas_work_dir}")

        dirs = local_project_dirs(root, project_name)
        os.makedirs(dirs["images"], exist_ok=True)
        os.makedirs(dirs["work"], exist_ok=True)
        _append(task, f"local dirs ready at {dirs['base']}")

        task.current_step = "copy_images"
        copied = await asyncio.to_thread(
            _sync_tree, nas_dataset_dir, dirs["images"], task,
        )
        _append(task, f"images synced: {copied} files")

        task.current_step = "copy_work"
        copied_work = await asyncio.to_thread(
            _sync_tree, nas_work_dir, dirs["work"], task,
            skip_subdirs=("checkpoints", "samples", "logs"),
        )
        _append(task, f"work files synced: {copied_work} files")

        task.current_step = "rewrite_toml"
        produced = await asyncio.to_thread(
            _rewrite_toml_paths, dirs, nas_dataset_dir, nas_work_dir,
        )
        for path in produced:
            _append(task, f"generated local toml: {path}")

        task.status = "done"
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = time.time()
        _append(task, f"ERR: {e}")


def _sync_tree(
    src_dir: str, dst_dir: str, task: SetupTask,
    *, skip_subdirs: tuple[str, ...] = (),
) -> int:
    """src_dir → dst_dir に shallow mirror。更新されたファイルだけコピー。"""
    count = 0
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        if os.path.isdir(src):
            if name in skip_subdirs:
                continue
            os.makedirs(dst, exist_ok=True)
            count += _sync_tree(src, dst, task, skip_subdirs=skip_subdirs)
            continue
        try:
            src_mtime = os.path.getmtime(src)
            src_size = os.path.getsize(src)
        except OSError:
            continue
        if os.path.exists(dst):
            try:
                if (
                    os.path.getmtime(dst) >= src_mtime
                    and os.path.getsize(dst) == src_size
                ):
                    continue
            except OSError:
                pass
        shutil.copy2(src, dst)
        count += 1
        if count % 25 == 0:
            task.current_step = f"copying ({count} files)"
    return count


def _rewrite_toml_paths(
    local_dirs: dict, nas_dataset_dir: str, nas_work_dir: str,
) -> list[str]:
    """local `dataset.toml` / `config.toml` のパス参照をローカルに書き換える。"""
    produced: list[str] = []
    work_local = local_dirs["work"]
    images_local = local_dirs["images"]

    # dataset.toml: NAS dataset_dir → local images dir
    ds_src = os.path.join(work_local, "dataset.toml")
    if os.path.exists(ds_src):
        body = _read(ds_src)
        body = body.replace(nas_dataset_dir, images_local)
        # Windows path escaping: backslashes → 二重化（TOML 仕様）
        body = _normalize_toml_paths(body)
        out = os.path.join(work_local, "dataset.local.toml")
        _write(out, body)
        produced.append(out)

    # config.toml: NAS work_dir → local work dir, dataset_config → local dataset.local.toml
    cfg_src = os.path.join(work_local, "config.toml")
    if os.path.exists(cfg_src):
        body = _read(cfg_src)
        body = body.replace(nas_work_dir, work_local)
        body = body.replace(nas_dataset_dir, images_local)
        body = body.replace(
            "dataset.toml", "dataset.local.toml",
        )
        body = _normalize_toml_paths(body)
        out = os.path.join(work_local, "config.local.toml")
        _write(out, body)
        produced.append(out)

    return produced


def _normalize_toml_paths(body: str) -> str:
    """TOML では `\\` がエスケープ扱いなのでダブルバックスラッシュに正規化。"""
    out_lines = []
    for line in body.splitlines():
        if "=" in line and '"' in line and "\\" in line:
            left, _, right = line.partition("=")
            right_strip = right.strip()
            if right_strip.startswith('"') and right_strip.endswith('"'):
                inner = right_strip[1:-1]
                # `\\` を含まない `\` を全て二重化
                if "\\" in inner and "\\\\" not in inner:
                    inner = inner.replace("\\", "\\\\")
                    line = f'{left}= "{inner}"'
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if body.endswith("\n") else "")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
