"""ZZZ Disc エンドポイント — capture / extract / capture-and-extract。"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .capture import capture_mss, capture_obs
from .vlm_client import extract as vlm_extract

router = APIRouter()

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")


def _verify(request: Request) -> None:
    if not _SECRET_TOKEN:
        return
    if request.headers.get("X-Agent-Token", "") != _SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _do_capture(
    backend: str,
    monitor: int,
    crop: Optional[dict],
    obs_host: Optional[str],
    obs_port: Optional[int],
    obs_source: Optional[str],
) -> bytes:
    if backend == "mss":
        return await run_in_threadpool(capture_mss, monitor, crop)
    if backend == "obs":
        host = obs_host or "localhost"
        port = int(obs_port or 4455)
        password = os.environ.get("OBS_WEBSOCKET_PASSWORD", "")
        source = obs_source or ""
        if not source:
            raise HTTPException(400, "obs_source is required for obs backend")
        return await run_in_threadpool(capture_obs, host, port, password, source)
    raise HTTPException(400, f"Unknown backend: {backend}")


@router.post("/capture")
async def capture_endpoint(request: Request):
    """画面キャプチャして PNG を返す。"""
    _verify(request)
    body = await request.json()
    try:
        png = await _do_capture(
            backend=body.get("backend", "mss"),
            monitor=int(body.get("monitor", 1)),
            crop=body.get("crop"),
            obs_host=body.get("obs_host"),
            obs_port=body.get("obs_port"),
            obs_source=body.get("obs_source"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Capture failed: {e}")
    return Response(content=png, media_type="image/png")


@router.post("/extract")
async def extract_endpoint(request: Request, file: UploadFile = File(...)):
    """multipart で画像受信 → VLM 抽出結果を返す。"""
    _verify(request)
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "Empty image")
    try:
        result = await vlm_extract(image_bytes)
    except ValueError as e:
        raise HTTPException(422, f"VLM parse error: {e}")
    except Exception as e:
        raise HTTPException(500, f"VLM call failed: {e}")
    return result


@router.post("/capture-and-extract")
async def capture_and_extract(request: Request):
    """キャプチャ + 抽出を 1 コールで実行。

    Response: {"png_base64": str, "extraction": dict}
    Pi 側 extractor.capture_and_extract がこの構造を期待する。
    """
    import base64 as _b64
    _verify(request)
    body = await request.json()
    try:
        png = await _do_capture(
            backend=body.get("backend", "mss"),
            monitor=int(body.get("monitor", 1)),
            crop=body.get("crop"),
            obs_host=body.get("obs_host"),
            obs_port=body.get("obs_port"),
            obs_source=body.get("obs_source"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Capture failed: {e}")

    model = body.get("model", "gemma4")
    ollama_url = body.get("ollama_url", "http://localhost:11434")
    try:
        result = await vlm_extract(png, model=model, ollama_url=ollama_url)
    except ValueError as e:
        raise HTTPException(422, f"VLM parse error: {e}")
    except Exception as e:
        raise HTTPException(500, f"VLM call failed: {e}")
    return {
        "png_base64": _b64.b64encode(png).decode("ascii"),
        "extraction": result,
        "model": model,
    }
