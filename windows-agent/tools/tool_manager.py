"""外部ツールのサブプロセス管理。"""

import asyncio
import collections
import os
import subprocess
import sys
import threading
from pathlib import Path

TOOLS_DIR = Path(__file__).parent

# ログのリングバッファサイズ
LOG_BUFFER_SIZE = 500


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
                capture_output=True, text=True,
            )
            if check.returncode != 0:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     f"name={name}", "dir=in", "action=allow",
                     "protocol=TCP", f"localport={port}"],
                    capture_output=True,
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

    def start(self):
        """プロセスを起動。"""
        if self.running:
            return

        self._manually_stopped = False
        self._setup_firewall()
        self._stop_event.clear()

        self.logs.append(f"[manager] Starting: {' '.join(self.cmd)}")
        self.process = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

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
        """死活監視ループ。落ちたプロセスを自動再起動。"""
        while True:
            await asyncio.sleep(check_interval)
            for tool in self._tools.values():
                if tool._manually_stopped:
                    continue
                if tool.process is not None and not tool.running:
                    tool.logs.append("[manager] Process died, restarting...")
                    tool.start()

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
