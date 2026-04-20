"""WD14 自動タグ付けサブプロセス管理。

kohya_ss 同梱の `finetune/tag_images_by_wd14_tagger.py` を venv-kohya の Python で
起動し、NAS 上の LoRA dataset dir に直接 `.txt` caption を書き込む。
SetupTask パターン（setup_manager.py）を流用し、`task_id + 進捗 + ログ tail` で
進捗を返す。
"""

from __future__ import annotations

import asyncio
import os
import uuid

from .setup_manager import SetupTask, _append, _run_cmd, _tasks, _venv_python

_DEFAULT_REPO = "SmilingWolf/wd-v1-4-swinv2-tagger-v2"
_DEFAULT_THRESHOLD = 0.35


def _tagger_script(root: str) -> str:
    return os.path.join(root, "kohya_ss", "finetune", "tag_images_by_wd14_tagger.py")


async def run_wd14_tagging(
    root: str,
    *,
    dataset_dir: str,
    threshold: float = _DEFAULT_THRESHOLD,
    repo_id: str = _DEFAULT_REPO,
    trigger_word: str | None = None,
    caption_extension: str = ".txt",
) -> SetupTask:
    """WD14 タグ付けを非同期で起動する。"""
    tid = f"wd14_{uuid.uuid4().hex[:16]}"
    task = SetupTask(task_id=tid, kind="wd14_tagging")
    _tasks[tid] = task
    asyncio.create_task(
        _do_wd14_tagging(
            task, root, dataset_dir, threshold, repo_id, trigger_word,
            caption_extension,
        ),
    )
    return task


async def _do_wd14_tagging(
    task: SetupTask, root: str, dataset_dir: str, threshold: float,
    repo_id: str, trigger_word: str | None, caption_extension: str,
) -> None:
    import time
    try:
        if not os.path.isdir(dataset_dir):
            raise RuntimeError(f"dataset dir not found: {dataset_dir}")
        script = _tagger_script(root)
        if not os.path.exists(script):
            raise RuntimeError(f"tagger script not found: {script}")
        venv_dir = os.path.join(root, "venv-kohya")
        py = _venv_python(venv_dir)
        if not os.path.exists(py):
            raise RuntimeError(f"venv python not found at {py}")

        cmd = [
            py, script,
            "--batch_size", "1",
            "--thresh", f"{threshold:.3f}",
            "--caption_extension", caption_extension,
            "--repo_id", repo_id,
            "--onnx",
            dataset_dir,
        ]
        task.current_step = "tagging"
        rc = await _run_cmd(task, cmd, cwd=os.path.join(root, "kohya_ss"), timeout=3600)
        if rc != 0:
            raise RuntimeError(f"wd14 tagger failed (rc={rc})")

        if trigger_word:
            task.current_step = "prepend_trigger"
            inserted = await asyncio.to_thread(
                _prepend_trigger_word, dataset_dir, trigger_word, caption_extension,
            )
            _append(task, f"prepended trigger '{trigger_word}' to {inserted} caption files")

        task.status = "done"
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = time.time()
        _append(task, f"ERR: {e}")


def _prepend_trigger_word(
    dataset_dir: str, trigger: str, caption_extension: str,
) -> int:
    """dataset dir 内の全 caption ファイル先頭にトリガーワードを差し込む（重複は入れない）。"""
    count = 0
    for fname in os.listdir(dataset_dir):
        if not fname.endswith(caption_extension):
            continue
        path = os.path.join(dataset_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                body = f.read().strip()
        except Exception:
            continue
        parts = [t.strip() for t in body.split(",") if t.strip()]
        if parts and parts[0] == trigger:
            continue
        parts.insert(0, trigger)
        with open(path, "w", encoding="utf-8") as f:
            f.write(", ".join(parts))
        count += 1
    return count
