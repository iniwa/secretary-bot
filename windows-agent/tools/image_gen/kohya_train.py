"""kohya_ss SDXL LoRA 学習サブプロセス管理。

`sdxl_train_network.py` を `--config_file` + `--dataset_config` で起動し、
stdout を log_tail に流しつつ進捗（step/epoch/loss）と sample 画像を抽出する。
SSE ストリームと POST /cancel を router 側から使う。
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .setup_manager import _venv_python


_RE_STEPS = re.compile(r"steps:\s*\d+%\|[^|]*\|\s*(\d+)/(\d+)")
_RE_EPOCH = re.compile(r"epoch\s+(\d+)\s*/\s*(\d+)")
_RE_LOSS = re.compile(r"(?:avg_loss|loss)[\s=:]+([0-9.]+)")
_RE_SAVED = re.compile(r"saving checkpoint.*?([A-Za-z0-9_\-./\\]+\.safetensors)")
_RE_SAMPLE = re.compile(r"generated sample.*?([A-Za-z0-9_\-./\\]+\.png)")


@dataclass
class TrainTask:
    task_id: str
    project_name: str
    kind: str = "lora_train"
    status: str = "running"  # running / done / failed / cancelled
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    current_step: str = ""
    log_tail: deque = field(default_factory=lambda: deque(maxlen=600))
    # progress
    step: int = 0
    total_steps: int = 0
    epoch: int = 0
    total_epochs: int = 0
    last_loss: float | None = None
    latest_sample: str | None = None
    latest_checkpoint: str | None = None
    # internals
    _proc: asyncio.subprocess.Process | None = None
    _cancel_requested: bool = False
    _log_seq: int = 0  # monotonic id for SSE deltas

    def snapshot(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "project_name": self.project_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "current_step": self.current_step,
            "step": self.step,
            "total_steps": self.total_steps,
            "epoch": self.epoch,
            "total_epochs": self.total_epochs,
            "progress_pct": (
                int(self.step * 100 / self.total_steps)
                if self.total_steps else 0
            ),
            "last_loss": self.last_loss,
            "latest_sample": self.latest_sample,
            "latest_checkpoint": self.latest_checkpoint,
            "log_tail": list(self.log_tail),
        }


_tasks: dict[str, TrainTask] = {}


def get_train_task(task_id: str) -> TrainTask | None:
    return _tasks.get(task_id)


def list_train_tasks() -> list[dict]:
    return [t.snapshot() for t in _tasks.values()]


def _append(task: TrainTask, line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    task._log_seq += 1
    task.log_tail.append({
        "seq": task._log_seq, "ts": time.time(), "message": line,
    })


def _parse_progress(task: TrainTask, line: str) -> None:
    m = _RE_STEPS.search(line)
    if m:
        try:
            task.step = int(m.group(1))
            task.total_steps = int(m.group(2))
        except ValueError:
            pass
    m = _RE_EPOCH.search(line)
    if m:
        try:
            task.epoch = int(m.group(1))
            task.total_epochs = int(m.group(2))
        except ValueError:
            pass
    m = _RE_LOSS.search(line)
    if m:
        try:
            task.last_loss = float(m.group(1))
        except ValueError:
            pass
    m = _RE_SAVED.search(line)
    if m:
        task.latest_checkpoint = m.group(1)
    m = _RE_SAMPLE.search(line)
    if m:
        task.latest_sample = m.group(1)


async def run_kohya_train(
    root: str, *, project_name: str,
    config_file: str, dataset_config: str,
) -> TrainTask:
    tid = f"train_{uuid.uuid4().hex[:16]}"
    task = TrainTask(task_id=tid, project_name=project_name)
    _tasks[tid] = task
    asyncio.create_task(_do_train(task, root, config_file, dataset_config))
    return task


async def _do_train(
    task: TrainTask, root: str, config_file: str, dataset_config: str,
) -> None:
    try:
        if not os.path.isfile(config_file):
            raise RuntimeError(f"config_file not found: {config_file}")
        if not os.path.isfile(dataset_config):
            raise RuntimeError(f"dataset_config not found: {dataset_config}")

        kohya_dir = os.path.join(root, "kohya_ss")
        venv_dir = os.path.join(root, "venv-kohya")
        py = _venv_python(venv_dir)
        if not os.path.isfile(py):
            raise RuntimeError(f"kohya venv python not found: {py}")

        # sd-scripts 配下に SDXL trainer がある
        trainer = os.path.join(
            kohya_dir, "sd-scripts", "sdxl_train_network.py",
        )
        if not os.path.isfile(trainer):
            # 古い layout fallback
            alt = os.path.join(kohya_dir, "sdxl_train_network.py")
            if os.path.isfile(alt):
                trainer = alt
            else:
                raise RuntimeError(
                    f"sdxl_train_network.py not found under {kohya_dir}",
                )

        cmd = [
            py, trainer,
            "--config_file", config_file,
            "--dataset_config", dataset_config,
        ]
        task.current_step = "launching"
        _append(task, f"$ {' '.join(cmd)}")

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")

        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=kohya_dir, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        task._proc = proc

        assert proc.stdout is not None
        async for raw in proc.stdout:
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                line = str(raw)
            _append(task, line)
            _parse_progress(task, line)

        rc = await proc.wait()
        _append(task, f"rc={rc}")

        if task._cancel_requested:
            task.status = "cancelled"
        elif rc == 0:
            task.status = "done"
        else:
            task.status = "failed"
            task.error = f"sdxl_train_network.py exited rc={rc}"
        task.finished_at = time.time()
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.finished_at = time.time()
        _append(task, f"ERR: {e}")


async def cancel_train(task_id: str) -> bool:
    task = _tasks.get(task_id)
    if task is None or task._proc is None:
        return False
    if task.status not in ("running",):
        return False
    task._cancel_requested = True
    task.current_step = "cancelling"
    try:
        if os.name == "nt":
            # Windows: CTRL_BREAK_EVENT は create_subprocess_exec の creationflags が
            # 必要なので、ここでは terminate() で直接シグナル送信する。
            task._proc.terminate()
        else:
            task._proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        return False
    except Exception as e:
        _append(task, f"cancel signal failed: {e}")
        return False
    _append(task, "cancel requested")
    return True


async def log_stream(task_id: str, *, after_seq: int = 0):
    """SSE 用: 現在以降のログ行を逐次 yield する async generator。

    終了条件: task が done/failed/cancelled になった後、残ログを吐いたら抜ける。
    """
    task = _tasks.get(task_id)
    if task is None:
        return
    sent = 0
    while True:
        entries = list(task.log_tail)
        new = [e for e in entries if e["seq"] > after_seq]
        for e in new:
            yield e
            after_seq = e["seq"]
            sent += 1
        terminal = task.status in ("done", "failed", "cancelled")
        if terminal:
            # 1 tick 分の猶予で drain して終了
            await asyncio.sleep(0.3)
            entries = list(task.log_tail)
            new = [e for e in entries if e["seq"] > after_seq]
            for e in new:
                yield e
                after_seq = e["seq"]
            return
        await asyncio.sleep(0.8)
