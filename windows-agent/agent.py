"""Windows Agent — FastAPIサーバー（ポート7777）。"""

import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from activity.game_detector import get_activity as get_game_activity, reload_process_map
from activity.obs_manager import OBSManager, create_obs_manager
from tools.tool_manager import ToolManager, create_tool_manager

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")
_tool_manager: ToolManager | None = None
_obs_manager: OBSManager | None = None
_stt_capture = None   # MicCapture
_stt_client = None    # STTClient (Main PC → Sub PC送信用、未使用時None)
_whisper_engine = None  # WhisperEngine (Sub PC)
_stt_pipeline = None  # LocalSTTPipeline (Sub PC: キャプチャ+推論)
_agent_role: str = "unknown"
_agent_config: dict = {}


def _load_agent_config() -> dict:
    """windows-agent/config/agent_config.yaml があれば読み込む。なければ空 dict。"""
    import yaml
    config_path = os.path.join(os.path.dirname(__file__), "config", "agent_config.yaml")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _detect_role() -> str:
    """環境変数 or IPアドレスからロールを判定。"""
    role = os.environ.get("AGENT_ROLE")
    if role:
        return role

    # IPベースの判定
    try:
        hostname = socket.gethostname()
        local_ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        ip_set = {addr[4][0] for addr in local_ips}
    except Exception:
        ip_set = set()

    role_map = {
        "192.168.1.210": "main",
        "192.168.1.211": "sub",
    }
    for ip, r in role_map.items():
        if ip in ip_set:
            return r

    return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tool_manager, _obs_manager, _stt_capture, _stt_client, _whisper_engine, _stt_pipeline
    global _agent_role, _agent_config
    role = _detect_role()
    config = _load_agent_config()
    _agent_role = role
    _agent_config = config
    print(f"[Agent] Role: {role}")
    _tool_manager = create_tool_manager(role)
    _tool_manager.start_all()
    _tool_manager.start_monitor()
    # Sub PC: OBS監視 + ファイル整理を開始
    if role == "sub":
        _obs_manager = create_obs_manager(config)
        _obs_manager.start()

    # STT初期化
    stt_cfg = config.get("stt", {})
    if stt_cfg.get("enabled", False):
        if role == "sub":
            # Sub PC: ローカルパイプライン（キャプチャ + Whisper推論）
            from stt.mic_capture import MicCapture
            from stt.whisper_engine import WhisperEngine
            from stt.local_pipeline import LocalSTTPipeline
            _whisper_engine = WhisperEngine(stt_cfg.get("model", {}))
            _stt_capture = MicCapture(stt_cfg.get("capture", {}))
            _stt_pipeline = LocalSTTPipeline(_stt_capture, _whisper_engine, stt_cfg.get("pipeline", {}))
            _stt_capture.start()
            _stt_pipeline.start()
            print("[Agent] STT local pipeline started (Sub PC: capture + whisper)")
        elif role == "main":
            # Main PC: キャプチャ → Sub PC送信
            from stt.mic_capture import MicCapture
            from stt.stt_client import STTClient
            _stt_capture = MicCapture(stt_cfg.get("capture", {}))
            _stt_client = STTClient(_stt_capture, {
                "sub_pc_url": stt_cfg.get("sub_pc_url", "http://192.168.1.211:7777"),
                "interval_minutes": stt_cfg.get("batch", {}).get("interval_minutes", 5),
                "agent_token": _SECRET_TOKEN,
            })
            _stt_capture.start()
            _stt_client.start()
            print("[Agent] STT capture started (Main PC → Sub PC)")

    yield

    # STTシャットダウン
    if _stt_pipeline:
        _stt_pipeline.stop()
    if _stt_client:
        _stt_client.stop()
    if _stt_capture:
        _stt_capture.stop()
    if _whisper_engine:
        _whisper_engine.unload()
    if _obs_manager:
        _obs_manager.stop()
    _tool_manager.stop_monitor()
    _tool_manager.stop_all()


app = FastAPI(title="Windows Agent", lifespan=lifespan)


def _verify_token(request: Request):
    if not _SECRET_TOKEN:
        return
    token = request.headers.get("X-Agent-Token", "")
    if token != _SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


def _get_commit_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, errors="replace"
        ).strip()
    except Exception:
        return "unknown"


@app.get("/health")
async def health(request: Request):
    _verify_token(request)
    return {"status": "ok", "version": _get_commit_hash()}


@app.get("/version")
async def version(request: Request):
    _verify_token(request)
    return {"version": _get_commit_hash()}


@app.post("/update")
async def update(request: Request):
    _verify_token(request)
    try:
        result = subprocess.run(
            ["git", "pull"], capture_output=True, text=True, errors="replace", timeout=30
        )
        pull_output = result.stdout.strip()

        # サブモジュール更新
        sub_result = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            capture_output=True, text=True, errors="replace", timeout=60,
        )
        sub_output = sub_result.stdout.strip()

        return {"success": True, "output": pull_output, "submodule": sub_output}
    except Exception as e:
        raise HTTPException(500, f"Update failed: {e}")


@app.get("/activity")
async def activity(request: Request):
    _verify_token(request)
    role = _detect_role()
    result: dict = {"role": role}
    if role == "main":
        result.update(get_game_activity())
    elif role == "sub":
        if _obs_manager:
            result.update(_obs_manager.get_status())
        else:
            result.update({
                "obs_connected": False,
                "obs_streaming": False,
                "obs_recording": False,
                "obs_replay_buffer": False,
            })
    return result


@app.get("/units")
async def list_units(request: Request):
    _verify_token(request)
    # Windows側ユニットを列挙（将来拡張用）
    return {"units": []}


@app.post("/execute/{unit_name}")
async def execute_unit(unit_name: str, request: Request):
    _verify_token(request)
    body = await request.json()
    # 将来: ユニット名に応じた処理を実行
    return {"result": f"Unit '{unit_name}' executed (stub)", "parsed": body}


# --- Tools: input-relay ---

@app.post("/tools/input-relay/update")
async def input_relay_update(request: Request):
    _verify_token(request)
    try:
        result = subprocess.run(
            ["git", "submodule", "update", "--remote", "windows-agent/tools/input-relay"],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return {"success": result.returncode == 0, "output": output}
    except Exception as e:
        raise HTTPException(500, f"Submodule update failed: {e}")


@app.get("/tools/input-relay/status")
async def input_relay_status(request: Request):
    _verify_token(request)
    tool = _tool_manager.get("input-relay") if _tool_manager else None
    if not tool:
        return {"name": "input-relay", "running": False, "pid": None, "registered": False}
    return {**tool.get_status(), "registered": True}


@app.get("/tools/input-relay/logs")
async def input_relay_logs(request: Request, lines: int = 100):
    _verify_token(request)
    tool = _tool_manager.get("input-relay") if _tool_manager else None
    if not tool:
        raise HTTPException(404, "input-relay not registered")
    return {"logs": tool.get_logs(lines)}


@app.post("/tools/input-relay/start")
async def input_relay_start(request: Request):
    _verify_token(request)
    tool = _tool_manager.get("input-relay") if _tool_manager else None
    if not tool:
        raise HTTPException(404, "input-relay not registered")
    tool.start()
    return {**tool.get_status()}


@app.post("/tools/input-relay/stop")
async def input_relay_stop(request: Request):
    _verify_token(request)
    tool = _tool_manager.get("input-relay") if _tool_manager else None
    if not tool:
        raise HTTPException(404, "input-relay not registered")
    tool.stop()
    return {**tool.get_status()}


@app.post("/tools/input-relay/restart")
async def input_relay_restart(request: Request):
    _verify_token(request)
    tool = _tool_manager.get("input-relay") if _tool_manager else None
    if not tool:
        raise HTTPException(404, "input-relay not registered")
    tool.restart()
    return {**tool.get_status()}


# --- STT: Main PC endpoints ---

@app.get("/stt/devices")
async def stt_devices(request: Request):
    """利用可能なマイクデバイス一覧を返す。"""
    _verify_token(request)
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                input_devices.append({
                    "id": i,
                    "name": d["name"],
                    "channels": d["max_input_channels"],
                    "sample_rate": d["default_samplerate"],
                })
        default_input = sd.default.device[0]
        return {"devices": input_devices, "default": default_input}
    except Exception as e:
        return {"devices": [], "default": None, "error": str(e)}


@app.get("/stt/status")
async def stt_status(request: Request):
    _verify_token(request)
    result: dict = {"role": _agent_role, "enabled": _stt_capture is not None or _whisper_engine is not None}
    if _stt_pipeline:
        result.update(_stt_pipeline.get_status())
    else:
        if _stt_capture:
            result["capture"] = _stt_capture.get_status()
            result["capture"]["device"] = _stt_capture._device
        if _stt_client:
            result["client"] = {
                "running": _stt_client.running,
                "transcript_count": len(_stt_client._transcripts),
                "last_error": _stt_client._last_error,
            }
        if _whisper_engine:
            result["whisper"] = _whisper_engine.get_status()
    return result


@app.get("/stt/transcripts")
async def stt_transcripts(request: Request, since: str | None = None):
    _verify_token(request)
    if _stt_pipeline:
        return {"transcripts": _stt_pipeline.get_transcripts(since=since)}
    if _stt_client:
        return {"transcripts": _stt_client.get_transcripts(since=since)}
    return {"transcripts": []}


@app.post("/stt/control")
async def stt_control(request: Request):
    """STTの開始/停止/デバイス変更/動的初期化。"""
    _verify_token(request)
    global _stt_capture, _stt_client, _whisper_engine, _stt_pipeline
    body = await request.json()
    action = body.get("action", "")

    if action == "init":
        # 既存のパイプラインを停止
        if _stt_pipeline:
            _stt_pipeline.stop()
        if _stt_capture and _stt_capture.running:
            _stt_capture.stop()
        if _stt_client and _stt_client.running:
            _stt_client.stop()

        from stt.mic_capture import MicCapture

        device_id = body.get("device")
        capture_cfg = {
            "device": device_id,
            "vad_aggressiveness": body.get("vad_aggressiveness", 2),
            "volume_threshold_rms": body.get("volume_threshold_rms", 300),
            "silence_threshold_seconds": body.get("silence_threshold_seconds", 1.5),
            "min_utterance_seconds": body.get("min_utterance_seconds", 1.0),
        }
        _stt_capture = MicCapture(capture_cfg)

        if _agent_role == "sub":
            # Sub PC: ローカルパイプライン
            from stt.whisper_engine import WhisperEngine
            from stt.local_pipeline import LocalSTTPipeline
            if not _whisper_engine:
                stt_cfg = _agent_config.get("stt", {})
                _whisper_engine = WhisperEngine(stt_cfg.get("model", {}))
            pipeline_cfg = _agent_config.get("stt", {}).get("pipeline", {})
            pipeline_cfg["process_interval_seconds"] = body.get(
                "process_interval_seconds", pipeline_cfg.get("process_interval_seconds", 30)
            )
            _stt_pipeline = LocalSTTPipeline(_stt_capture, _whisper_engine, pipeline_cfg)
            _stt_capture.start()
            _stt_pipeline.start()
            return {"status": "initialized", "mode": "local", "capture": _stt_capture.get_status()}
        else:
            # Main PC: Sub PCへ送信
            from stt.stt_client import STTClient
            _stt_client = STTClient(_stt_capture, {
                "sub_pc_url": body.get("sub_pc_url", "http://192.168.1.211:7777"),
                "interval_minutes": body.get("interval_minutes", 5),
                "agent_token": _SECRET_TOKEN,
            })
            _stt_capture.start()
            _stt_client.start()
            return {"status": "initialized", "mode": "remote", "capture": _stt_capture.get_status()}

    if action == "start":
        if not _stt_capture:
            raise HTTPException(400, "STT not initialized. Use action='init' first.")
        _stt_capture.start()
        if _stt_pipeline and not _stt_pipeline.running:
            _stt_pipeline.start()
        elif _stt_client and not _stt_client.running:
            _stt_client.start()
    elif action == "stop":
        if _stt_pipeline:
            _stt_pipeline.stop()
        if _stt_capture:
            _stt_capture.stop()
        if _stt_client:
            _stt_client.stop()
    elif action == "set_device":
        device_id = body.get("device")
        if _stt_capture:
            was_running = _stt_capture.running
            if was_running:
                _stt_capture.stop()
            _stt_capture._device = device_id
            if was_running:
                _stt_capture.start()
            return {"status": "device_changed", "device": device_id, "capture": _stt_capture.get_status()}
        raise HTTPException(400, "STT not initialized")
    else:
        raise HTTPException(400, f"Unknown action: {action}")

    return _stt_capture.get_status() if _stt_capture else {"running": False}


# --- STT: Sub PC endpoints ---

@app.post("/stt")
async def stt_transcribe(request: Request):
    _verify_token(request)
    if not _whisper_engine:
        raise HTTPException(400, "Whisper engine not available on this agent")
    wav_data = await request.body()
    if not wav_data:
        raise HTTPException(400, "No audio data received")
    try:
        text = _whisper_engine.transcribe(wav_data)
        return {"text": text}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Transcription failed: {e}")


@app.get("/stt/model/status")
async def stt_model_status(request: Request):
    _verify_token(request)
    if not _whisper_engine:
        return {"loaded": False, "error": "Whisper engine not available on this agent"}
    return _whisper_engine.get_status()


# --- OBS: ゲームプロセス管理 ---

_GAMES_FILE = os.path.join(os.path.dirname(__file__), "config", "game_processes.json")
_GROUPS_FILE = os.path.join(os.path.dirname(__file__), "config", "game_groups.json")


def _load_games_data() -> dict:
    import json as _json
    games_raw = {}
    if os.path.exists(_GAMES_FILE):
        with open(_GAMES_FILE, encoding="utf-8") as f:
            games_raw = _json.load(f)
    groups_data = {"groups": [], "assignments": {}}
    if os.path.exists(_GROUPS_FILE):
        with open(_GROUPS_FILE, encoding="utf-8") as f:
            groups_data = _json.load(f)
    games = [
        {"process": k, "name": v, "group": groups_data["assignments"].get(k, "")}
        for k, v in games_raw.items()
    ]
    return {"games": games, "groups": groups_data["groups"]}


def _save_games_data(data: dict) -> None:
    import json as _json
    games_dict = {g["process"]: g["name"] for g in data["games"]}
    with open(_GAMES_FILE, "w", encoding="utf-8") as f:
        _json.dump(games_dict, f, ensure_ascii=False, indent=2)
    assignments = {g["process"]: g["group"] for g in data["games"] if g.get("group")}
    groups_data = {"groups": data["groups"], "assignments": assignments}
    with open(_GROUPS_FILE, "w", encoding="utf-8") as f:
        _json.dump(groups_data, f, ensure_ascii=False, indent=2)
    reload_process_map()


@app.get("/obs/games")
async def obs_games(request: Request):
    _verify_token(request)
    return _load_games_data()


@app.post("/obs/games")
async def obs_games_save(request: Request):
    _verify_token(request)
    body = await request.json()
    _save_games_data(body)
    return {"ok": True, "count": len(body.get("games", []))}


@app.get("/obs/status")
async def obs_status(request: Request):
    _verify_token(request)
    if _obs_manager:
        return {**_obs_manager.get_status(), "file_organizer_enabled": _obs_manager._enabled}
    return {"obs_connected": False, "file_organizer_enabled": False}


_LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "agent.log")


@app.get("/obs/logs")
async def obs_logs(request: Request, lines: int = 100):
    """OBSManager関連のログを返す。"""
    _verify_token(request)
    log_entries = []
    try:
        if os.path.exists(_LOG_FILE):
            with open(_LOG_FILE, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            obs_keywords = {"obs", "moved", "recording", "replay", "screenshot",
                            "sweep", "compress", "cleanup", "stray", "pngquant"}
            obs_lines = [
                l.rstrip() for l in all_lines
                if any(kw in l.lower() for kw in obs_keywords)
            ]
            log_entries = obs_lines[-lines:]
    except Exception:
        pass
    return {"logs": log_entries}


# --- PC制御 ---

@app.post("/shutdown")
async def shutdown_pc(request: Request):
    _verify_token(request)
    body = await request.json()
    delay = max(body.get("delay", 60), 10)
    subprocess.Popen(
        ["shutdown", "/s", "/t", str(delay)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return {"status": "scheduled", "delay": delay}


@app.post("/restart")
async def restart_pc(request: Request):
    _verify_token(request)
    body = await request.json()
    delay = max(body.get("delay", 60), 10)
    subprocess.Popen(
        ["shutdown", "/r", "/t", str(delay)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return {"status": "scheduled", "delay": delay}


@app.post("/cancel-shutdown")
async def cancel_shutdown(request: Request):
    _verify_token(request)
    result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, errors="replace")
    return {"status": "cancelled" if result.returncode == 0 else "no_pending"}


if __name__ == "__main__":
    import logging
    import uvicorn

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(log_dir, "agent.log"), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    port = int(os.environ.get("AGENT_PORT", "7777"))
    uvicorn.run(app, host="0.0.0.0", port=port)
