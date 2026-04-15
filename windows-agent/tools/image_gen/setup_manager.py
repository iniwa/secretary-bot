"""ComfyUI / kohya_ss のインストール・更新サブプロセス管理。

`/comfyui/setup` / `/comfyui/update` / `/kohya/setup` の裏で動く非同期タスクを
task_id で追跡する。長時間かかるため即時 202 + 別エンドポイントで進捗を返す方針。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SetupTask:
    task_id: str
    kind: str                # "comfyui_setup" / "comfyui_update" / "kohya_setup"
    status: str = "running"  # running / done / failed
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    current_step: str = ""
    log_tail: deque = field(default_factory=lambda: deque(maxlen=400))

    def snapshot(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "current_step": self.current_step,
            "log_tail": list(self.log_tail),
        }


_tasks: dict[str, SetupTask] = {}


def get_task(task_id: str) -> Optional[SetupTask]:
    return _tasks.get(task_id)


def list_tasks() -> list[dict]:
    return [t.snapshot() for t in _tasks.values()]


def _append(task: SetupTask, line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    task.log_tail.append({"ts": time.time(), "message": line})


async def _run_cmd(
    task: SetupTask, cmd: list[str], cwd: Optional[str] = None,
    env: Optional[dict] = None, timeout: int = 1800,
) -> int:
    """サブプロセスを走らせて stdout/stderr を task.log_tail に流す。"""
    task.current_step = " ".join(cmd[:3]) + (" ..." if len(cmd) > 3 else "")
    _append(task, f"$ {' '.join(cmd)} (cwd={cwd or '.'})")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            env=env or os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as e:
        _append(task, f"ERR: command not found: {e}")
        return 127

    async def _reader():
        assert proc.stdout is not None
        async for raw in proc.stdout:
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                line = str(raw)
            _append(task, line)

    try:
        await asyncio.wait_for(
            asyncio.gather(_reader(), proc.wait()), timeout=timeout,
        )
    except asyncio.TimeoutError:
        _append(task, f"ERR: timeout after {timeout}s")
        try:
            proc.kill()
        except Exception:
            pass
        return 124
    rc = proc.returncode if proc.returncode is not None else -1
    _append(task, f"rc={rc}")
    return rc


async def _exists_git() -> bool:
    return shutil.which("git") is not None


async def run_comfyui_setup(
    root: str, *, repo_url: str, ref: str, cuda_index_url: str | None = None,
) -> SetupTask:
    """ComfyUI を <root>/comfyui にクローンし、<root>/venv-comfyui に PyTorch+依存を入れる。"""
    tid = f"setup_{uuid.uuid4().hex[:16]}"
    task = SetupTask(task_id=tid, kind="comfyui_setup")
    _tasks[tid] = task
    asyncio.create_task(_do_comfyui_setup(task, root, repo_url, ref, cuda_index_url))
    return task


async def _do_comfyui_setup(
    task: SetupTask, root: str, repo_url: str, ref: str,
    cuda_index_url: str | None,
) -> None:
    try:
        if not await _exists_git():
            raise RuntimeError("git not found in PATH")
        os.makedirs(root, exist_ok=True)
        comfy_dir = os.path.join(root, "comfyui")
        venv_dir = os.path.join(root, "venv-comfyui")

        # 1. git clone or fetch
        if os.path.isdir(os.path.join(comfy_dir, ".git")):
            rc = await _run_cmd(task, ["git", "fetch", "--all", "--prune"],
                                cwd=comfy_dir, timeout=600)
            if rc != 0:
                raise RuntimeError("git fetch failed")
            rc = await _run_cmd(task, ["git", "checkout", ref], cwd=comfy_dir)
            if rc != 0:
                raise RuntimeError(f"git checkout {ref} failed")
            rc = await _run_cmd(task, ["git", "pull", "--ff-only"], cwd=comfy_dir)
            if rc != 0:
                raise RuntimeError("git pull failed")
        else:
            rc = await _run_cmd(
                task, ["git", "clone", "--depth", "50", "--branch", ref, repo_url, comfy_dir],
                timeout=1200,
            )
            if rc != 0:
                raise RuntimeError("git clone failed")

        # 2. venv 作成
        if not os.path.isdir(venv_dir):
            rc = await _run_cmd(task, [sys.executable, "-m", "venv", venv_dir])
            if rc != 0:
                raise RuntimeError("venv creation failed")

        py = _venv_python(venv_dir)
        if not os.path.exists(py):
            raise RuntimeError(f"venv python not found at {py}")

        # 3. pip upgrade
        rc = await _run_cmd(task, [py, "-m", "pip", "install", "--upgrade", "pip", "wheel"],
                            timeout=600)
        if rc != 0:
            raise RuntimeError("pip upgrade failed")

        # 4. PyTorch (CUDA)
        torch_cmd = [py, "-m", "pip", "install", "torch", "torchvision", "torchaudio"]
        if cuda_index_url:
            torch_cmd += ["--index-url", cuda_index_url]
        rc = await _run_cmd(task, torch_cmd, timeout=3600)
        if rc != 0:
            raise RuntimeError("torch install failed")

        # 5. requirements.txt
        req = os.path.join(comfy_dir, "requirements.txt")
        if os.path.exists(req):
            rc = await _run_cmd(task, [py, "-m", "pip", "install", "-r", req],
                                timeout=1800)
            if rc != 0:
                raise RuntimeError("requirements install failed")

        task.status = "done"
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = time.time()
        _append(task, f"ERR: {e}")


async def run_comfyui_update(root: str, *, ref: str = "master") -> SetupTask:
    tid = f"update_{uuid.uuid4().hex[:16]}"
    task = SetupTask(task_id=tid, kind="comfyui_update")
    _tasks[tid] = task
    asyncio.create_task(_do_comfyui_update(task, root, ref))
    return task


async def _do_comfyui_update(task: SetupTask, root: str, ref: str) -> None:
    try:
        comfy_dir = os.path.join(root, "comfyui")
        if not os.path.isdir(os.path.join(comfy_dir, ".git")):
            raise RuntimeError(f"ComfyUI repo not found at {comfy_dir}; run setup first")
        rc = await _run_cmd(task, ["git", "fetch", "--all", "--prune"],
                            cwd=comfy_dir, timeout=600)
        if rc != 0:
            raise RuntimeError("git fetch failed")
        rc = await _run_cmd(task, ["git", "checkout", ref], cwd=comfy_dir)
        if rc != 0:
            raise RuntimeError(f"git checkout {ref} failed")
        rc = await _run_cmd(task, ["git", "pull", "--ff-only"], cwd=comfy_dir)
        if rc != 0:
            raise RuntimeError("git pull failed")

        # requirements.txt 更新があれば再インストール
        venv_dir = os.path.join(root, "venv-comfyui")
        py = _venv_python(venv_dir)
        req = os.path.join(comfy_dir, "requirements.txt")
        if os.path.exists(py) and os.path.exists(req):
            rc = await _run_cmd(task, [py, "-m", "pip", "install", "-r", req],
                                timeout=1800)
            if rc != 0:
                raise RuntimeError("requirements install failed")

        task.status = "done"
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = time.time()
        _append(task, f"ERR: {e}")


async def run_kohya_setup(
    root: str, *, repo_url: str, ref: str, cuda_index_url: str | None = None,
) -> SetupTask:
    tid = f"kohya_{uuid.uuid4().hex[:16]}"
    task = SetupTask(task_id=tid, kind="kohya_setup")
    _tasks[tid] = task
    asyncio.create_task(_do_kohya_setup(task, root, repo_url, ref, cuda_index_url))
    return task


async def _do_kohya_setup(
    task: SetupTask, root: str, repo_url: str, ref: str,
    cuda_index_url: str | None,
) -> None:
    try:
        if not await _exists_git():
            raise RuntimeError("git not found in PATH")
        os.makedirs(root, exist_ok=True)
        kohya_dir = os.path.join(root, "kohya_ss")
        venv_dir = os.path.join(root, "venv-kohya")

        if os.path.isdir(os.path.join(kohya_dir, ".git")):
            rc = await _run_cmd(task, ["git", "fetch", "--all", "--prune"],
                                cwd=kohya_dir, timeout=600)
            if rc != 0:
                raise RuntimeError("git fetch failed")
            rc = await _run_cmd(task, ["git", "checkout", ref], cwd=kohya_dir)
            if rc != 0:
                raise RuntimeError(f"git checkout {ref} failed")
            rc = await _run_cmd(task, ["git", "pull", "--ff-only"], cwd=kohya_dir)
            if rc != 0:
                raise RuntimeError("git pull failed")
        else:
            rc = await _run_cmd(
                task, ["git", "clone", "--depth", "50", "--branch", ref,
                       "--recurse-submodules", repo_url, kohya_dir],
                timeout=1800,
            )
            if rc != 0:
                raise RuntimeError("git clone failed")

        if not os.path.isdir(venv_dir):
            rc = await _run_cmd(task, [sys.executable, "-m", "venv", venv_dir])
            if rc != 0:
                raise RuntimeError("venv creation failed")
        py = _venv_python(venv_dir)
        if not os.path.exists(py):
            raise RuntimeError(f"venv python not found at {py}")

        rc = await _run_cmd(task, [py, "-m", "pip", "install", "--upgrade", "pip", "wheel"],
                            timeout=600)
        if rc != 0:
            raise RuntimeError("pip upgrade failed")

        torch_cmd = [py, "-m", "pip", "install", "torch", "torchvision"]
        if cuda_index_url:
            torch_cmd += ["--index-url", cuda_index_url]
        rc = await _run_cmd(task, torch_cmd, timeout=3600)
        if rc != 0:
            raise RuntimeError("torch install failed")

        req = os.path.join(kohya_dir, "requirements.txt")
        if os.path.exists(req):
            rc = await _run_cmd(task, [py, "-m", "pip", "install", "-r", req],
                                timeout=1800)
            if rc != 0:
                raise RuntimeError("requirements install failed")

        task.status = "done"
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = time.time()
        _append(task, f"ERR: {e}")


def _venv_python(venv_dir: str) -> str:
    """venv の python 実行ファイルパス（Windows/Unix 両対応）。"""
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")
