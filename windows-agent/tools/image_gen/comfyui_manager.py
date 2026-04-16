"""ComfyUI サブプロセス管理。

- Phase 1: 明示リクエストまたは初回 generate 時に起動する遅延起動方針
  （lifespan で自動起動はしない）
- クラッシュ時は最大 crash_restart_max_retries まで自動再起動
- ヘルスチェックは health_check_interval_seconds 周期で /system_stats を叩く
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class ComfyUIState:
    pid: Optional[int] = None
    started_at: Optional[float] = None
    last_health_at: Optional[float] = None
    last_error: Optional[str] = None
    restart_count: int = 0
    available: bool = False
    log_tail: deque = field(default_factory=lambda: deque(maxlen=500))


class ComfyUIManager:
    def __init__(
        self,
        root: str,
        host: str = "127.0.0.1",
        port: int = 8188,
        startup_timeout_seconds: int = 60,
        health_check_interval_seconds: int = 30,
        crash_restart_max_retries: int = 3,
        logger=None,
    ) -> None:
        self.root = root
        self.host = host
        self.port = int(port)
        self.startup_timeout_seconds = int(startup_timeout_seconds)
        self.health_check_interval_seconds = int(health_check_interval_seconds)
        self.crash_restart_max_retries = int(crash_restart_max_retries)
        self.state = ComfyUIState()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._logger = logger

    @property
    def _probe_host(self) -> str:
        # 0.0.0.0 は bind 用ワイルドカードで、Windows では接続先として使うと
        # WinError 10049 になる。ヘルスチェックはローカルから叩くので 127.0.0.1 に寄せる。
        if self.host in ("0.0.0.0", "::", ""):
            return "127.0.0.1"
        return self.host

    @property
    def base_url(self) -> str:
        return f"http://{self._probe_host}:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self._probe_host}:{self.port}/ws"

    def _log(self, level: str, msg: str) -> None:
        line = f"[comfyui] {msg}"
        self.state.log_tail.append({"ts": time.time(), "level": level, "message": msg})
        if self._logger:
            getattr(self._logger, level if hasattr(self._logger, level) else "info")(line)
        else:
            print(line, flush=True)

    def _resolve_entry(self) -> Optional[list[str]]:
        """ComfyUI の起動コマンドを組み立てる。

        優先順:
          1. ${SECRETARY_BOT_ROOT}/venv-comfyui/Scripts/python.exe + comfyui/main.py
          2. ${root}/comfyui/main.py を現行 python で実行
          3. 見つからない場合 None
        """
        comfy_dir = os.path.join(self.root, "comfyui")
        main_py = os.path.join(comfy_dir, "main.py")
        if not os.path.exists(main_py):
            return None

        venv_py = os.path.join(self.root, "venv-comfyui", "Scripts", "python.exe")
        python_exe = venv_py if os.path.exists(venv_py) else sys.executable
        return [
            python_exe, main_py,
            "--listen", self.host,
            "--port", str(self.port),
            "--extra-model-paths-config",
            os.path.join(comfy_dir, "extra_model_paths.yaml"),
        ]

    def is_running(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def _probe_existing(self) -> bool:
        """ポートに既に別プロセスが応答していないか確認する。

        エージェント再起動などで ``self._proc`` を失った状態で ``start()`` を
        呼ぶと、ポート衝突した二重起動になる。HTTP で ``/system_stats`` を
        短時間叩いて既存インスタンスを検出する。
        """
        try:
            with httpx.Client(timeout=1.0) as client:
                r = client.get(f"{self.base_url}/system_stats")
                return r.status_code == 200
        except Exception:
            return False

    async def wait_until_ready(self, timeout: Optional[int] = None) -> bool:
        # 採用済み（外部プロセスを adopt）時は自前の Popen が無いので
        # is_running() が False になる。既に available なら即 True を返す。
        if self.state.available and self._proc is None:
            return True
        deadline = time.time() + (timeout or self.startup_timeout_seconds)
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.time() < deadline:
                if self._proc is not None and not self.is_running():
                    # 自前 Popen が死んでいる
                    await asyncio.sleep(0.5)
                    continue
                try:
                    r = await client.get(f"{self.base_url}/system_stats")
                    if r.status_code == 200:
                        self.state.available = True
                        self.state.last_health_at = time.time()
                        return True
                except Exception:
                    pass
                await asyncio.sleep(1.0)
        return False

    def start(self) -> dict:
        """起動要求。既に起動中なら no-op。"""
        with self._lock:
            if self.is_running():
                return {"ok": True, "pid": self._proc.pid, "already_running": True}
            # エージェント再起動後は self._proc が None のまま、外部で ComfyUI が
            # 生き残っているケースがある。二重起動による VRAM 競合を防ぐため
            # ポートに応答があれば既存インスタンスを採用する。
            if self._probe_existing():
                self.state.available = True
                self.state.last_health_at = time.time()
                self.state.last_error = None
                self._log("info", f"adopted existing ComfyUI at {self.base_url}")
                self._ensure_monitor()
                return {"ok": True, "pid": None, "already_running": True, "adopted": True}
            cmd = self._resolve_entry()
            if cmd is None:
                self.state.last_error = "ComfyUI not installed"
                self.state.available = False
                return {"ok": False, "error": "ComfyUI not installed", "transient": False}
            try:
                creationflags = 0
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                # Windows + CREATE_NO_WINDOW + パイプ stdout だと tqdm が stderr.flush で
                # EINVAL を踏むことがある。バッファ無効化 + tqdm 更新間隔を広げて回避。
                env = os.environ.copy()
                env.setdefault("PYTHONUNBUFFERED", "1")
                env.setdefault("PYTHONIOENCODING", "utf-8")
                env.setdefault("TQDM_MININTERVAL", "1.0")
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=os.path.join(self.root, "comfyui"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    text=True,
                    errors="replace",
                    env=env,
                    bufsize=1,
                )
                self.state.pid = self._proc.pid
                self.state.started_at = time.time()
                self.state.last_error = None
                self._log("info", f"started pid={self._proc.pid} cmd={shlex.join(cmd)}")
                # stdout を別スレッドで吸い上げ
                t = threading.Thread(target=self._drain_stdout, daemon=True)
                t.start()
                self._ensure_monitor()
                return {"ok": True, "pid": self._proc.pid, "already_running": False}
            except Exception as e:
                self.state.last_error = f"spawn failed: {e}"
                self._log("error", self.state.last_error)
                return {"ok": False, "error": str(e), "transient": True}

    def _drain_stdout(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        for line in self._proc.stdout:
            line = (line or "").rstrip()
            if not line:
                continue
            level = "error" if any(k in line.lower() for k in ("error", "traceback", "cuda out of memory")) else "info"
            self.state.log_tail.append({"ts": time.time(), "level": level, "message": line})

    def _ensure_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        interval = max(self.health_check_interval_seconds, 5)
        while not self._monitor_stop.is_set():
            time.sleep(interval)
            healthy = False
            try:
                with httpx.Client(timeout=3.0) as client:
                    r = client.get(f"{self.base_url}/system_stats")
                    if r.status_code == 200:
                        healthy = True
                        self.state.available = True
                        self.state.last_health_at = time.time()
            except Exception as e:
                self.state.last_error = f"health failed: {e}"

            if healthy:
                continue
            self.state.available = False

            # 採用済み外部プロセスは勝手に再起動しない（管理外のため）。
            # 自前で spawn した Popen が死んでいる場合のみ再起動する。
            if self._proc is None or self._proc.poll() is None:
                continue
            if self.state.restart_count >= self.crash_restart_max_retries:
                self.state.last_error = "restart retries exceeded"
                self._log("error", self.state.last_error)
                return
            self.state.restart_count += 1
            self._log("warn", f"crash detected, auto-restarting (#{self.state.restart_count})")
            self.start()

    def stop(self, timeout: float = 10.0) -> dict:
        with self._lock:
            self._monitor_stop.set()
            if not self.is_running():
                return {"ok": True, "stopped": False}
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5.0)
                self._log("info", "stopped")
                return {"ok": True, "stopped": True}
            except Exception as e:
                self._log("error", f"stop failed: {e}")
                return {"ok": False, "error": str(e)}

    def status_snapshot(self) -> dict:
        owned = self.is_running()
        # 採用した外部プロセスは owned=False でもヘルスが通っていれば動作中とみなす
        running = owned or self.state.available
        return {
            "running": running,
            "available": self.state.available,
            "pid": self._proc.pid if owned and self._proc else None,
            "base_url": self.base_url,
            "started_at": self.state.started_at,
            "last_health_at": self.state.last_health_at,
            "last_error": self.state.last_error,
            "restart_count": self.state.restart_count,
        }

    def recent_logs(self, lines: int = 200) -> list[dict]:
        tail = list(self.state.log_tail)
        return tail[-lines:]
