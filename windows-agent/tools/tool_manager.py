"""外部ツールのサブプロセス管理。"""

import asyncio
import collections
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import threading
from pathlib import Path

TOOLS_DIR = Path(__file__).parent

# ログのリングバッファサイズ
LOG_BUFFER_SIZE = 500

# 死活監視: 連続失敗でリトライを停止する閾値
MAX_CONSECUTIVE_FAILURES = 5

# --- Windows Job Object ---
# 親プロセス終了時に子プロセスも自動終了させる

_kernel32 = ctypes.windll.kernel32

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
        ("LimitFlags", ctypes.wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.wintypes.DWORD),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
        ("PriorityClass", ctypes.wintypes.DWORD),
        ("SchedulingClass", ctypes.wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_job_object() -> ctypes.wintypes.HANDLE | None:
    """KILL_ON_JOB_CLOSE付きJob Objectを作成。"""
    try:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _kernel32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        return job
    except Exception:
        return None


def _assign_to_job(job: ctypes.wintypes.HANDLE, pid: int):
    """プロセスをJob Objectに割り当て。"""
    try:
        handle = _kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if handle:
            _kernel32.AssignProcessToJobObject(job, handle)
            _kernel32.CloseHandle(handle)
    except Exception:
        pass


# モジュール起動時にJob Object作成（Agent終了時に自動で子プロセスも終了）
_job_object = _create_job_object()


class ToolProcess:
    """1つのツールプロセスを管理する。"""

    def __init__(self, name: str, cmd: list[str], cwd: str,
                 firewall_rules: list[dict] | None = None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.firewall_rules = firewall_rules or []
        self.process: subprocess.Popen | None = None
        self.logs: collections.deque[str] = collections.deque(maxlen=LOG_BUFFER_SIZE)
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._manually_stopped = False  # 手動停止フラグ（死活監視の自動再起動を抑制）
        self._consecutive_failures = 0  # 連続失敗カウンタ

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.running else None

    def _setup_firewall(self):
        """ファイアウォールルールを追加（既存なら追加しない）。"""
        for rule in self.firewall_rules:
            name = rule["name"]
            port = rule["port"]
            # ルールが存在するか確認
            check = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                capture_output=True, text=True, errors="replace",
            )
            if check.returncode != 0:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     f"name={name}", "dir=in", "action=allow",
                     "protocol=TCP", f"localport={port}"],
                    capture_output=True, errors="replace",
                )
                self.logs.append(f"[firewall] Added rule: {name} (port {port})")

    def _read_output(self):
        """stdout/stderr を読み取ってログバッファに保存。"""
        proc = self.process
        if not proc or not proc.stdout:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                stripped = line.rstrip("\n\r")
                if stripped:
                    self.logs.append(stripped)
        except Exception:
            pass

    def _kill_port_holders(self):
        """起動前に、使用予定ポートを占有しているプロセスをkill。"""
        ports = {r["port"] for r in self.firewall_rules}
        if not ports:
            return
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, errors="replace",
            )
        except Exception:
            return
        killed: set[int] = set()
        my_pid = os.getpid()
        for line in result.stdout.splitlines():
            if "LISTENING" not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            # netstat -ano: Proto LocalAddr ForeignAddr State PID
            # e.g.: TCP  0.0.0.0:8888  0.0.0.0:0  LISTENING  12345
            try:
                port = int(parts[1].rsplit(":", 1)[1])
                pid = int(parts[-1])
            except (ValueError, IndexError):
                continue
            if port in ports and pid != my_pid and pid not in killed:
                self.logs.append(f"[manager] Killing stale process PID {pid} on port {port}")
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    killed.add(pid)
                except Exception:
                    pass

    def start(self):
        """プロセスを起動。"""
        if self.running:
            return

        self._manually_stopped = False
        self._consecutive_failures = 0
        self._kill_port_holders()
        self._setup_firewall()
        self._stop_event.clear()

        self.logs.append(f"[manager] Starting: {' '.join(self.cmd)}")
        self.process = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # Job Objectに登録（Agent終了時に自動kill）
        if _job_object and self.process.pid:
            _assign_to_job(_job_object, self.process.pid)

        self._reader_thread = threading.Thread(
            target=self._read_output, daemon=True,
        )
        self._reader_thread.start()

    def stop(self):
        """プロセスを停止。"""
        self._manually_stopped = True
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            self.logs.append("[manager] Stopping process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.logs.append("[manager] Process stopped.")
        self.process = None

    def restart(self):
        """プロセスを再起動。"""
        self.stop()
        self.start()

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "running": self.running,
            "pid": self.pid,
        }

    def get_logs(self, lines: int = 100) -> list[str]:
        all_logs = list(self.logs)
        return all_logs[-lines:]


class ToolManager:
    """ロールに応じたツールを管理する。"""

    def __init__(self):
        self._tools: dict[str, ToolProcess] = {}
        self._monitor_task: asyncio.Task | None = None

    def register(self, tool: ToolProcess):
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolProcess | None:
        return self._tools.get(name)

    def start_all(self):
        for tool in self._tools.values():
            tool.start()

    def stop_all(self):
        for tool in self._tools.values():
            tool.stop()

    async def monitor(self, check_interval: float = 10.0):
        """死活監視ループ。落ちたプロセスを自動再起動（連続失敗時は停止）。"""
        while True:
            await asyncio.sleep(check_interval)
            for tool in self._tools.values():
                if tool._manually_stopped:
                    continue
                if tool.process is not None and not tool.running:
                    tool._consecutive_failures += 1
                    if tool._consecutive_failures > MAX_CONSECUTIVE_FAILURES:
                        if tool._consecutive_failures == MAX_CONSECUTIVE_FAILURES + 1:
                            tool.logs.append(
                                f"[manager] {tool.name}: {MAX_CONSECUTIVE_FAILURES} consecutive "
                                "failures, giving up auto-restart. Use manual start to retry."
                            )
                        continue
                    tool.logs.append(
                        f"[manager] Process died, restarting... "
                        f"(attempt {tool._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                    )
                    tool.start()
                elif tool.running:
                    tool._consecutive_failures = 0

    def start_monitor(self):
        self._monitor_task = asyncio.create_task(self.monitor())

    def stop_monitor(self):
        if self._monitor_task:
            self._monitor_task.cancel()


def create_tool_manager(role: str) -> ToolManager:
    """ロールに応じたツールを登録した ToolManager を返す。"""
    manager = ToolManager()
    python = sys.executable
    input_relay_dir = str(TOOLS_DIR / "input-relay")

    if role == "main":
        manager.register(ToolProcess(
            name="input-relay",
            cmd=[python, "sender/input_sender.py"],
            cwd=input_relay_dir,
            firewall_rules=[
                {"name": "InputSender GUI HTTP", "port": 8082},
                {"name": "InputSender Monitor WS", "port": 8083},
            ],
        ))
    elif role == "sub":
        manager.register(ToolProcess(
            name="input-relay",
            cmd=[python, "receiver/input_server.py", "--http-port", "8081"],
            cwd=input_relay_dir,
            firewall_rules=[
                {"name": "InputDisplay-WS", "port": 8888},
                {"name": "InputDisplay-HTTP", "port": 8081},
            ],
        ))

    return manager
