"""Windows Agent — FastAPIサーバー（ポート7777）。"""

import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from tools.tool_manager import ToolManager, create_tool_manager

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")
_tool_manager: ToolManager | None = None


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
    global _tool_manager
    role = _detect_role()
    print(f"[Agent] Role: {role}")
    _tool_manager = create_tool_manager(role)
    _tool_manager.start_all()
    _tool_manager.start_monitor()
    yield
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
            ["git", "rev-parse", "HEAD"], text=True
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
            ["git", "pull"], capture_output=True, text=True, timeout=30
        )
        pull_output = result.stdout.strip()

        # サブモジュール更新
        sub_result = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            capture_output=True, text=True, timeout=60,
        )
        sub_output = sub_result.stdout.strip()

        return {"success": True, "output": pull_output, "submodule": sub_output}
    except Exception as e:
        raise HTTPException(500, f"Update failed: {e}")


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
            capture_output=True, text=True, timeout=30,
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
    result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
    return {"status": "cancelled" if result.returncode == 0 else "no_pending"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("AGENT_PORT", "7777"))
    uvicorn.run(app, host="0.0.0.0", port=port)
