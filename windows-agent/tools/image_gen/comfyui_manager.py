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
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import httpx


@dataclass
class ComfyUIState:
    pid: int | None = None
    started_at: float | None = None
    last_health_at: float | None = None
    last_error: str | None = None
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
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._logger = logger
        # Windows で stdout を PIPE にすると tqdm の stderr.flush で EINVAL を踏むため
        # real file にリダイレクトする。state.log_tail 用に別スレッドで tail する。
        self._log_fh = None
        self._log_path: str | None = None
        self._log_tail_stop = threading.Event()

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

    def _resolve_entry(self) -> list[str] | None:
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

    async def wait_until_ready(self, timeout: int | None = None) -> bool:
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

    def adopt_if_alive(self) -> bool:
        """エージェント起動直後に呼び、port が応答していれば既存 ComfyUI を採用する。

        Code Update で agent.py だけが再起動されたとき、Windows の subprocess は
        親が死んでも子が残るため ComfyUI は port を握ったまま生存している。
        UI 上「停止」と表示されないよう、起動時に一度だけ採用判定する。"""
        with self._lock:
            if self._proc is not None or self.state.available:
                return False
            if not self._probe_existing():
                return False
            self.state.available = True
            self.state.last_health_at = time.time()
            self.state.last_error = None
            self._log("info", f"adopted existing ComfyUI at {self.base_url} (on agent boot)")
            self._ensure_monitor()
            return True

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
                # Windows + CREATE_NO_WINDOW で stdout を PIPE にすると、tqdm の
                # status_printer が呼ぶ sys.stderr.flush() が anonymous pipe に対して
                # OSError [Errno 22] を投げ、KSampler 以降の全ジョブが失敗する。
                # stdout/stderr を real file にリダイレクトすれば flush は成功する。
                env = os.environ.copy()
                env.setdefault("PYTHONUNBUFFERED", "1")
                env.setdefault("PYTHONIOENCODING", "utf-8")
                self._log_path = os.path.join(self.root, "comfyui.agent.log")
                # 起動の度に切り詰め（恒久保存したければ別途 rotate）
                self._log_fh = open(self._log_path, "w", buffering=1, encoding="utf-8", errors="replace")
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=os.path.join(self.root, "comfyui"),
                    stdout=self._log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    env=env,
                )
                self.state.pid = self._proc.pid
                self.state.started_at = time.time()
                self.state.last_error = None
                self._log("info", f"started pid={self._proc.pid} cmd={shlex.join(cmd)}")
                # log ファイルを別スレッドで tail し state.log_tail を更新
                self._log_tail_stop.clear()
                t = threading.Thread(target=self._tail_log_file, daemon=True)
                t.start()
                self._ensure_monitor()
                return {"ok": True, "pid": self._proc.pid, "already_running": False}
            except Exception as e:
                self.state.last_error = f"spawn failed: {e}"
                self._log("error", self.state.last_error)
                return {"ok": False, "error": str(e), "transient": True}

    def _tail_log_file(self) -> None:
        """ComfyUI ログファイルを tail して state.log_tail を更新。"""
        if not self._log_path:
            return
        try:
            f = open(self._log_path, encoding="utf-8", errors="replace")
        except OSError:
            return
        try:
            while not self._log_tail_stop.is_set():
                line = f.readline()
                if not line:
                    if self._proc is None or self._proc.poll() is not None:
                        break
                    time.sleep(0.5)
                    continue
                line = line.rstrip()
                if not line:
                    continue
                level = "error" if any(k in line.lower() for k in ("error", "traceback", "cuda out of memory")) else "info"
                self.state.log_tail.append({"ts": time.time(), "level": level, "message": line})
        finally:
            try:
                f.close()
            except Exception:
                pass

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
            self._log_tail_stop.set()
            # 自前 Popen が生きているなら素直に terminate
            if self.is_running():
                try:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                        self._proc.wait(timeout=5.0)
                    self._close_log_fh()
                    self._mark_stopped()
                    self._log("info", "stopped")
                    return {"ok": True, "stopped": True}
                except Exception as e:
                    self._log("error", f"stop failed: {e}")
                    return {"ok": False, "error": str(e)}

            # 外部起動（adopted）で port だけ応答しているケース。
            # psutil でポート保有 PID を特定し kill を試みる。
            self._close_log_fh()
            if self._probe_existing():
                result = self._kill_by_port(self.port, timeout=timeout)
                self._mark_stopped()
                return result
            self._mark_stopped()
            return {"ok": True, "stopped": False}

    def _mark_stopped(self) -> None:
        self.state.available = False
        self.state.pid = None
        self._proc = None

    def _kill_by_port(self, port: int, timeout: float = 10.0) -> dict:
        """adopted ComfyUI を port から PID 逆引きして kill する。
        権限不足の場合は error_class=PermissionError を返し、UI 側で手動停止を促す。"""
        try:
            import psutil  # type: ignore
        except ImportError:
            return {"ok": False, "error": "psutil not installed", "error_class": "RuntimeError"}

        pid = None
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                    pid = conn.pid
                    break
        except (psutil.AccessDenied, PermissionError) as e:
            self._log("error", f"kill_by_port: cannot enumerate sockets: {e}")
            return {"ok": False, "error": f"cannot enumerate listening sockets: {e}", "error_class": "PermissionError"}

        if not pid:
            self._log("warn", f"kill_by_port: no listener on :{port}")
            return {"ok": True, "stopped": False, "note": f"no process listening on :{port}"}

        try:
            proc = psutil.Process(pid)
            name = proc.name()
            self._log("info", f"kill_by_port pid={pid} name={name} (adopted)")
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
            return {"ok": True, "stopped": True, "pid": pid, "adopted_kill": True}
        except psutil.NoSuchProcess:
            return {"ok": True, "stopped": False, "note": "process already gone"}
        except (psutil.AccessDenied, PermissionError) as e:
            self._log("error", f"kill_by_port pid={pid} permission denied: {e}")
            return {
                "ok": False,
                "error": f"PID {pid} cannot be terminated by agent (elevated?)",
                "error_class": "PermissionError",
                "pid": pid,
            }
        except Exception as e:
            self._log("error", f"kill_by_port pid={pid} failed: {e}")
            return {"ok": False, "error": str(e), "error_class": "RuntimeError", "pid": pid}

    def _close_log_fh(self) -> None:
        fh = self._log_fh
        self._log_fh = None
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass

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
