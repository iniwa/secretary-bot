"""Windows Agent — FastAPIサーバー（ポート7777）。"""

import os
import subprocess
import sys

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Windows Agent")

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")


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
