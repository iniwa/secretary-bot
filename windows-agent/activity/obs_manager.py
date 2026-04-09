"""OBS WebSocket 接続・状態取得・ファイル自動整理（2pc-obs から移植・統合）。

責務:
- OBS WebSocket への接続・再接続・死活監視
- OBS状態取得（/activity レスポンス用 = アクティビティ判定向け）
- OBSイベント駆動のファイル整理（録画/リプレイ/スクリーンショット）
- 定期クリーンアップ（迷子ファイル掃除・空フォルダ削除・スクショ圧縮）
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import obsws_python as obs
except ImportError:
    obs = None


# ---------------------------------------------------------------------------
# ファイル操作ユーティリティ（2pc-obs/sub_pc/agent.py から移植）
# ---------------------------------------------------------------------------

def _sanitize_folder_name(name: str) -> str:
    """Windows ディレクトリ名に使えない文字を置換。"""
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()


def _resolve_dest(dest_dir: Path, filename: str) -> Path:
    """重複しないファイルパスを返す。"""
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{stem} ({counter}){suffix}"
        counter += 1
    return dest


def _move_file(src: Path, dest: Path, retries: int, retry_delay: float) -> bool:
    """リトライ付きファイル移動。成功なら True。"""
    for attempt in range(1, retries + 1):
        try:
            shutil.move(str(src), str(dest))
            log.info("Moved '%s' -> '%s'", src.name, dest)
            return True
        except PermissionError as e:
            log.warning("Move attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(retry_delay)
        except OSError as e:
            log.error("Move failed: %s", e)
            return False
    log.error("Failed to move '%s' after %d attempts", src.name, retries)
    return False


def _compress_screenshot(src: Path, dest_dir: Path) -> None:
    """pngquant で PNG を圧縮して dest_dir に配置。"""
    if src.suffix.lower() != ".png":
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _resolve_dest(dest_dir, src.name)
    try:
        result = subprocess.run(
            ["pngquant", "--quality=70-90", "--speed=1", "--output", str(dest), str(src)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            src.unlink()
            log.info("Compressed screenshot '%s' -> '%s'", src.name, dest)
        elif result.returncode == 99:
            # 品質条件を満たせない → そのまま移動
            shutil.move(str(src), str(dest))
            log.info("Compression skipped (already optimal), moved '%s' -> '%s'", src.name, dest)
        else:
            log.error("pngquant failed (rc=%d): %s", result.returncode, result.stderr.strip())
    except FileNotFoundError:
        log.warning("pngquant not found, moving without compression")
        shutil.move(str(src), str(dest))
    except OSError as e:
        log.error("Screenshot compression failed: %s", e)


# ---------------------------------------------------------------------------
# 定期クリーンアップ
# ---------------------------------------------------------------------------

def _sweep_stray_files(obs_record_dir: str | None, output_base_dir: str,
                       unknown_folder: str, screenshot_folder: str) -> None:
    """OBS 出力ディレクトリの残留ファイルを Unknown に移動。"""
    if not obs_record_dir:
        return
    video_exts = {".mp4", ".mkv", ".ts", ".flv", ".mov", ".avi", ".webm"}
    image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tiff"}
    src_dir = Path(obs_record_dir)
    out_base = Path(output_base_dir)
    if not src_dir.exists():
        return

    moved = 0
    for entry in src_dir.iterdir():
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext in video_exts:
            dest_dir = out_base / unknown_folder
        elif ext in image_exts:
            dest_dir = out_base / screenshot_folder / unknown_folder
        else:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _resolve_dest(dest_dir, entry.name)
        try:
            shutil.move(str(entry), str(dest))
            log.info("Swept stray file '%s' -> '%s'", entry.name, dest)
            moved += 1
        except OSError as e:
            log.warning("Failed to sweep '%s': %s", entry.name, e)
    if moved:
        log.info("Sweep: moved %d stray file(s)", moved)


def _cleanup_empty_dirs(base_dir: str, screenshot_folder: str) -> None:
    """メディアファイルのない空フォルダを削除。"""
    video_exts = {".mp4", ".mkv", ".ts", ".flv", ".mov", ".avi", ".webm"}
    image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tiff"}
    base = Path(base_dir)
    if not base.exists():
        return
    screenshot_dir = base / screenshot_folder
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(base, topdown=False):
        p = Path(dirpath)
        if p == base or p == screenshot_dir:
            continue
        is_screenshot = screenshot_dir in p.parents or p.parent == screenshot_dir
        if is_screenshot:
            has_media = any(Path(f).suffix.lower() in image_exts for f in filenames)
        else:
            has_media = any(Path(f).suffix.lower() in video_exts for f in filenames)
        if has_media:
            continue
        try:
            os.rmdir(p)
            log.info("Removed empty directory: %s", p)
            removed += 1
        except OSError:
            pass
    if removed:
        log.info("Cleanup: removed %d empty director(ies)", removed)


def _compress_existing_screenshots(incoming_ss_dir: str, encoded_ss_dir: str) -> None:
    """incoming スクショの残存 PNG を一括圧縮。"""
    src_base = Path(incoming_ss_dir)
    dst_base = Path(encoded_ss_dir)
    if not src_base.exists():
        return
    count = 0
    for png in src_base.rglob("*.png"):
        rel = png.relative_to(src_base)
        dest_dir = dst_base / rel.parent
        _compress_screenshot(png, dest_dir)
        count += 1
    if count:
        log.info("Startup compression: processed %d screenshot(s)", count)


# ---------------------------------------------------------------------------
# ゲーム名取得（Main PC /activity を問い合わせ）
# ---------------------------------------------------------------------------

def _query_game(main_agent_url: str, timeout: int = 3) -> tuple[str | None, str | None]:
    """Main PC の /activity からゲーム名とフォアグラウンドプロセスを取得。

    Returns:
        (game_name, foreground_process)
    """
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    try:
        req = urllib.request.Request(main_agent_url)
        if token:
            req.add_header("X-Agent-Token", token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("game"), data.get("foreground_process")
    except Exception as e:
        log.debug("Could not reach Main PC /activity: %s", e)
    return None, None


def _resolve_game_name(main_agent_url: str) -> tuple[str | None, str | None]:
    """Main PC からゲーム検出結果を取得。

    Returns:
        (known_game_name, foreground_process)
        - known_game_name: game_processes.json で識別されたゲーム名。未登録なら None。
        - foreground_process: Main PC の生 foreground パス（メモ用）。取得できなかった場合は None。

    NOTE: 呼び出し側が known_game_name と foreground から、最終的な保存フォルダを決める。
    """
    return _query_game(main_agent_url)


# ---------------------------------------------------------------------------
# OBSManager: 状態取得 + ファイル整理の統合クラス
# ---------------------------------------------------------------------------

class OBSManager:
    """OBS WebSocket に接続し、状態監視とファイル自動整理を行う。

    - get_status(): アクティビティ判定向けの OBS 状態
    - ファイル整理: OBS イベントでトリガーされるバックグラウンド処理
    """

    def __init__(self, config: dict):
        obs_cfg = config.get("obs_file_organizer", {})
        self._enabled = obs_cfg.get("enabled", False)
        self._output_base = obs_cfg.get("output_base_dir", "")
        self._encoded_base = obs_cfg.get("encoded_base_dir", "")
        self._unknown_folder = obs_cfg.get("unknown_folder", "Unknown")
        self._screenshot_folder = obs_cfg.get("screenshot_folder", "_screenshot")
        self._retries = obs_cfg.get("file_move_retries", 3)
        self._retry_delay = obs_cfg.get("file_move_retry_delay_seconds", 1)
        self._cleanup_interval = obs_cfg.get("cleanup_interval_seconds", 3600)

        # Main PC Agent URL（ゲーム名取得用）
        main_agents = [a for a in config.get("windows_agents", []) if a.get("role") == "main"]
        if main_agents:
            a = main_agents[0]
            self._main_activity_url = f"http://{a['host']}:{a['port']}/activity"
        else:
            self._main_activity_url = ""

        # OBS WebSocket 接続設定（config優先 → 環境変数フォールバック）
        self._host = obs_cfg.get("host") or os.environ.get("OBS_WEBSOCKET_HOST", "localhost")
        self._port = int(obs_cfg.get("port") or os.environ.get("OBS_WEBSOCKET_PORT", "4455"))
        self._password = obs_cfg.get("password") or os.environ.get("OBS_WEBSOCKET_PASSWORD", "")
        self._timeout = obs_cfg.get("timeout", 5)
        self._retry_interval = obs_cfg.get("connect_retry_interval_seconds", 30)

        # 状態
        self._connected = False
        self._streaming = False
        self._recording = False
        self._replay_buffer = False

        self._req: object | None = None
        self._ev: object | None = None
        self._thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def get_status(self) -> dict:
        """アクティビティ判定向けの OBS 状態を返す。"""
        return {
            "obs_connected": self._connected,
            "obs_streaming": self._streaming,
            "obs_recording": self._recording,
            "obs_replay_buffer": self._replay_buffer,
        }

    def start(self) -> None:
        """バックグラウンドで OBS 接続・状態監視・ファイル整理を開始。"""
        if obs is None:
            log.info("obsws-python not installed, OBS manager disabled")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._disconnect()

    # --- 接続管理 ---

    def _disconnect(self) -> None:
        self._connected = False
        if self._ev:
            try:
                self._ev.disconnect()
            except Exception:
                pass
            self._ev = None
        if self._req:
            try:
                self._req.disconnect()
            except Exception:
                pass
            self._req = None

    def _connect(self) -> bool:
        """ReqClient + EventClient の両方を接続。"""
        try:
            self._req = obs.ReqClient(
                host=self._host, port=self._port,
                password=self._password, timeout=self._timeout,
            )
            self._connected = True
            log.info("Connected to OBS WebSocket at %s:%d", self._host, self._port)

            # ファイル整理が有効ならイベント登録
            if self._enabled and self._output_base:
                self._ev = obs.EventClient(
                    host=self._host, port=self._port,
                    password=self._password, timeout=self._timeout,
                )
                self._ev.callback.register([
                    self.on_record_state_changed,
                    self.on_replay_buffer_saved,
                    self.on_screenshot_saved,
                ])
                log.info("OBS event handlers registered (file organizer enabled)")

            return True
        except Exception as e:
            self._connected = False
            self._req = None
            self._ev = None
            log.debug("OBS connection failed: %s", e)
            return False

    def _obs_alive(self) -> bool:
        """EventClient の接続とワーカースレッドが生きているか確認。"""
        if not self._ev:
            # EventClient がなければ ReqClient のポーリングで判断
            return self._connected
        # ワーカースレッドが死んでいれば不達 → 再接続トリガー
        # (obsws_python 1.8.0 はコールバック内の非 OSError 例外を捕捉せず、
        #  ワーカースレッドが silent に死ぬため、明示的に生死を確認する)
        worker = getattr(self._ev, 'worker', None)
        if worker is not None and not worker.is_alive():
            return False
        ws = getattr(self._ev.base_client, 'ws', None)
        if ws is not None:
            return bool(getattr(ws, 'connected', True))
        return True

    # --- 状態ポーリング ---

    def _poll_status(self) -> bool:
        """OBS の現在状態を取得。接続切れなら False。"""
        try:
            stream = self._req.get_stream_status()
            self._streaming = stream.output_active

            record = self._req.get_record_status()
            self._recording = record.output_active

            try:
                replay = self._req.get_replay_buffer_status()
                self._replay_buffer = replay.output_active
            except Exception:
                self._replay_buffer = False

            return True
        except Exception as e:
            log.debug("OBS poll failed: %s", e)
            self._streaming = False
            self._recording = False
            self._replay_buffer = False
            self._connected = False
            return False

    # --- OBS イベントハンドラ ---

    # NOTE: obsws_python 1.8.0 はコールバック内の非 OSError 例外を捕捉せず、
    # ワーカースレッドが silent に死ぬ。各コールバックは必ず try/except で
    # 包み、例外を log.exception で記録してからスレッド死を防ぐこと。

    def on_record_state_changed(self, data) -> None:
        try:
            if data.output_state != "OBS_WEBSOCKET_OUTPUT_STOPPED":
                return
            file_path = getattr(data, "output_path", None)
            if not file_path:
                log.warning("RecordStateChanged STOPPED: no output_path")
                return
            log.info("Recording stopped: %s", file_path)
            game, foreground = _resolve_game_name(self._main_activity_url)
            log.info("Detected game: %s", game or "(unknown)")
            self._organize_video(file_path, game, foreground)
        except Exception:
            log.exception("on_record_state_changed failed")

    def on_replay_buffer_saved(self, data) -> None:
        try:
            file_path = getattr(data, "saved_replay_path", None)
            if not file_path:
                log.warning("ReplayBufferSaved: no saved_replay_path")
                return
            log.info("Replay saved: %s", file_path)
            game, foreground = _resolve_game_name(self._main_activity_url)
            log.info("Detected game: %s", game or "(unknown)")
            self._organize_video(file_path, game, foreground)
        except Exception:
            log.exception("on_replay_buffer_saved failed")

    def on_screenshot_saved(self, data) -> None:
        try:
            file_path = getattr(data, "saved_screenshot_path", None)
            if not file_path:
                log.warning("ScreenshotSaved: no saved_screenshot_path")
                return
            log.info("Screenshot saved: %s", file_path)
            game, foreground = _resolve_game_name(self._main_activity_url)
            log.info("Detected game: %s", game or "(unknown)")
            self._organize_screenshot(file_path, game, foreground)
        except Exception:
            log.exception("on_screenshot_saved failed")

    # --- ファイル整理 ---

    def _pick_folder(self, kind: str, file_name: str,
                     game_name: str | None, foreground: str | None) -> str:
        """保存フォルダ名を決定し、ゲーム未識別時にメモログを出す。

        優先順位:
          1. game_processes.json で識別されたゲーム名
          2. foreground プロセスの stem（exe 名ベース、ゲーム登録漏れのヒント用）
          3. self._unknown_folder（設定値。デフォルト "Unknown"）

        2, 3 のケースでは、その時の foreground プロセスを警告ログとしてメモ。
        """
        if game_name:
            return _sanitize_folder_name(game_name)

        # ゲーム未識別 → foreground をメモして folder を決める
        if foreground:
            folder = _sanitize_folder_name(Path(foreground).stem)
            log.warning(
                "[Unknown memo] %s '%s' saved to fallback folder '%s'. "
                "Foreground process at capture time: %s "
                "(add to game_processes.json to register as a game)",
                kind, file_name, folder, foreground,
            )
            return folder

        # 完全に不明 → Unknown 行き
        log.warning(
            "[Unknown memo] %s '%s' saved to '%s'. "
            "Foreground process: (unavailable — Main PC unreachable or no foreground)",
            kind, file_name, self._unknown_folder,
        )
        return self._unknown_folder

    def _organize_video(self, file_path: str, game_name: str | None,
                        foreground: str | None = None) -> None:
        src = Path(file_path)
        if not src.exists():
            log.error("Source file not found: %s", src)
            return
        folder = self._pick_folder("recording", src.name, game_name, foreground)
        dest_dir = Path(self._output_base) / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _resolve_dest(dest_dir, src.name)
        _move_file(src, dest, self._retries, self._retry_delay)

    def _organize_screenshot(self, file_path: str, game_name: str | None,
                             foreground: str | None = None) -> None:
        src = Path(file_path)
        if not src.exists():
            log.error("Source file not found: %s", src)
            return
        folder = self._pick_folder("screenshot", src.name, game_name, foreground)
        incoming_dir = Path(self._output_base) / self._screenshot_folder / folder
        incoming_dir.mkdir(parents=True, exist_ok=True)
        dest = _resolve_dest(incoming_dir, src.name)

        if not _move_file(src, dest, self._retries, self._retry_delay):
            return

        # 圧縮して encoded ディレクトリへ
        if self._encoded_base:
            encoded_dir = Path(self._encoded_base) / self._screenshot_folder / folder
            _compress_screenshot(dest, encoded_dir)

    # --- 定期クリーンアップ ---

    def _start_cleanup_loop(self, obs_record_dir: str | None) -> None:
        """クリーンアップスレッドを開始。"""
        if self._cleanup_thread is not None:
            return

        def loop():
            # 起動時にスクショ圧縮
            if self._encoded_base:
                incoming_ss = str(Path(self._output_base) / self._screenshot_folder)
                encoded_ss = str(Path(self._encoded_base) / self._screenshot_folder)
                _compress_existing_screenshots(incoming_ss, encoded_ss)

            while not self._stop_event.is_set():
                _sweep_stray_files(
                    obs_record_dir, self._output_base,
                    self._unknown_folder, self._screenshot_folder,
                )
                _cleanup_empty_dirs(self._output_base, self._screenshot_folder)
                if self._encoded_base:
                    incoming_ss = str(Path(self._output_base) / self._screenshot_folder)
                    encoded_ss = str(Path(self._encoded_base) / self._screenshot_folder)
                    _compress_existing_screenshots(incoming_ss, encoded_ss)
                self._stop_event.wait(self._cleanup_interval)

        self._cleanup_thread = threading.Thread(target=loop, daemon=True)
        self._cleanup_thread.start()
        log.info("Cleanup loop started (interval: %ds)", self._cleanup_interval)

    def _get_obs_record_dir(self) -> str | None:
        """OBS の録画出力ディレクトリを取得。"""
        try:
            resp = self._req.get_record_directory()
            return resp.record_directory
        except Exception as e:
            log.warning("Failed to get OBS record directory: %s", e)
            return None

    # --- メインループ ---

    def _run(self) -> None:
        cleanup_started = False

        while not self._stop_event.is_set():
            if not self._connected:
                if not self._connect():
                    self._stop_event.wait(self._retry_interval)
                    continue

            # 初回接続成功時にクリーンアップ開始
            if not cleanup_started and self._enabled and self._output_base:
                obs_record_dir = self._get_obs_record_dir()
                if obs_record_dir:
                    log.info("OBS recording directory: %s", obs_record_dir)
                self._start_cleanup_loop(obs_record_dir)
                cleanup_started = True

            # 状態ポーリング + 死活監視
            if not self._poll_status() or not self._obs_alive():
                log.warning("OBS connection lost, reconnecting in %ds...", self._retry_interval)
                self._disconnect()
                self._stop_event.wait(self._retry_interval)
                continue

            self._stop_event.wait(5)


# シングルトン
_manager: OBSManager | None = None


def create_obs_manager(config: dict) -> OBSManager:
    """config から OBSManager を作成。"""
    global _manager
    _manager = OBSManager(config)
    return _manager


def get_obs_manager() -> OBSManager | None:
    return _manager
