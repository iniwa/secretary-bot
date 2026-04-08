"""ゲームプロセス検出（2pc-obs から移植）。"""

import json
import logging
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
PROCESSES_PATH = CONFIG_DIR / "game_processes.json"


def _load_process_map() -> dict[str, str]:
    """game_processes.json を読み込み、小文字キーの dict を返す。"""
    if not PROCESSES_PATH.exists():
        log.warning("game_processes.json not found: %s", PROCESSES_PATH)
        return {}
    with open(PROCESSES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {k.lower(): v for k, v in raw.items()}


# モジュールロード時に一度読み込み（再読み込みは reload() で）
_process_map: dict[str, str] = _load_process_map()


def reload_process_map() -> int:
    """game_processes.json を再読み込みし、登録数を返す。"""
    global _process_map
    _process_map = _load_process_map()
    return len(_process_map)


def _get_foreground_info() -> tuple[str | None, bool]:
    """フォアグラウンドウィンドウのプロセス名とフルスクリーン判定を返す。

    Returns:
        (process_name, is_fullscreen)
    """
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, False

        # フルスクリーン判定: ウィンドウサイズ == スクリーンサイズ
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        screen_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        is_fullscreen = (
            rect.left <= 0
            and rect.top <= 0
            and rect.right >= screen_w
            and rect.bottom >= screen_h
        )

        # プロセス名取得
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None, is_fullscreen
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name, is_fullscreen
        finally:
            kernel32.CloseHandle(handle)
    except Exception as e:
        log.debug("Could not get foreground info: %s", e)
    return None, False


def detect_game() -> str | None:
    """実行中プロセスから既知ゲームを検出。見つからなければ None。"""
    if not psutil or not _process_map:
        return None
    try:
        running = {
            (proc.info["name"] or "").lower()
            for proc in psutil.process_iter(["name"])
        }
    except Exception:
        return None
    for proc_name, game_name in _process_map.items():
        if proc_name in running:
            return game_name
    return None


def get_activity() -> dict:
    """ゲーム検出結果を dict で返す。"""
    game = detect_game()
    fg_process, is_fullscreen = _get_foreground_info()

    # 未知プロセスがフルスクリーンの場合ログに記録
    if not game and is_fullscreen and fg_process:
        log.info(
            "Unknown fullscreen process: %s (add to game_processes.json?)",
            fg_process,
        )

    return {
        "game": game,
        "foreground_process": fg_process,
        "is_fullscreen": is_fullscreen,
    }
