"""image_gen FastAPI router — /capability, /image/generate, /cache/sync, /comfyui/status。

API 仕様: docs/design/image_gen_api.md
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .cache_manager import (
    SyncProgress,
    cache_models_root,
    cache_usage_gb,
    copy_with_progress,
    iter_cache_entries,
    resolve_local_path,
    sha256_of_file,
    sidecar_sha256,
    write_sha256_sidecar,
)
from .comfyui_manager import ComfyUIManager
from .nas_mount import ensure_mounted
from .workflow_runner import ImageJob, WorkflowRunner, substitute_placeholders


router = APIRouter()

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")


# --- コンテキスト（init_image_gen で差し込む） ---
class _Ctx:
    role: str = "unknown"
    agent_id: str = "unknown"
    image_gen_cfg: dict = {}
    agent_dir: str = ""
    comfy: Optional[ComfyUIManager] = None
    nas_drive: Optional[str] = None
    jobs: dict[str, ImageJob] = {}
    syncs: dict[str, SyncProgress] = {}
    # sync 用非同期イベント配信: sync_id -> list[Queue]
    sync_subscribers: dict[str, list[asyncio.Queue]] = {}


_ctx = _Ctx()


def init_image_gen(role: str, agent_config: dict, agent_dir: str, logger=None) -> None:
    """agent.py の lifespan から呼び出す初期化。"""
    image_gen_cfg = (agent_config.get("image_gen") or {}) if agent_config else {}
    _ctx.role = role
    _ctx.agent_id = f"{role}-pc"
    _ctx.image_gen_cfg = image_gen_cfg
    _ctx.agent_dir = agent_dir
    if not image_gen_cfg.get("enabled", False):
        return
    # NAS マウント（失敗しても起動は継続、呼び出し時にエラー返却）
    _ctx.nas_drive = ensure_mounted(image_gen_cfg, agent_dir)
    # ComfyUI マネージャ生成（lifespan では起動しない）
    comfy_cfg = image_gen_cfg.get("comfyui") or {}
    root = image_gen_cfg.get("root") or "C:/secretary-bot"
    _ctx.comfy = ComfyUIManager(
        root=root,
        host=comfy_cfg.get("host", "127.0.0.1"),
        port=int(comfy_cfg.get("port", 8188)),
        startup_timeout_seconds=int(comfy_cfg.get("startup_timeout_seconds", 60)),
        health_check_interval_seconds=int(comfy_cfg.get("health_check_interval_seconds", 30)),
        crash_restart_max_retries=int(comfy_cfg.get("crash_restart_max_retries", 3)),
        logger=logger,
    )


# --- 共通ユーティリティ ---

def _verify(request: Request) -> None:
    if not _SECRET_TOKEN:
        return
    if request.headers.get("X-Agent-Token", "") != _SECRET_TOKEN:
        raise HTTPException(status_code=401, detail={"error_class": "AuthError", "message": "invalid token", "retryable": False})


def _trace_id(request: Request) -> str:
    return request.headers.get("X-Trace-Id") or f"agent_{uuid.uuid4().hex[:12]}"


def _error_response(status: int, error_class: str, message: str, retryable: bool, detail: Optional[dict] = None, trace_id: str = "") -> JSONResponse:
    body = {
        "error_class": error_class,
        "message": message,
        "retryable": retryable,
        "detail": {**(detail or {}), **({"trace_id": trace_id} if trace_id else {})},
    }
    headers = {"X-Trace-Id": trace_id} if trace_id else {}
    return JSONResponse(status_code=status, content=body, headers=headers)


def _require_enabled(trace_id: str) -> Optional[JSONResponse]:
    if not _ctx.image_gen_cfg.get("enabled", False):
        return _error_response(503, "ResourceUnavailableError", "image_gen disabled on this agent", True, trace_id=trace_id)
    return None


def _cache_root() -> str:
    return _ctx.image_gen_cfg.get("cache") or "C:/secretary-bot-cache"


# --- GPU 情報取得（ベストエフォート） ---
def _gpu_info() -> dict:
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,compute_cap", "--format=csv,noheader,nounits"],
            text=True, errors="replace", timeout=5,
        )
        line = out.strip().splitlines()[0]
        name, total, free, compute = [p.strip() for p in line.split(",")]
        return {
            "name": name,
            "vram_total_mb": int(total),
            "vram_free_mb": int(free),
            "cuda_compute": compute,
        }
    except Exception as e:
        return {"name": "unknown", "vram_total_mb": 0, "vram_free_mb": 0, "cuda_compute": "", "error": str(e)}


# --- /capability ---
@router.get("/capability")
async def capability(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    cache_root = _cache_root()

    entries = iter_cache_entries(cache_root)

    def _filter(t: str) -> list[dict]:
        return [{"type": e.type, "filename": e.filename} for e in entries if e.type == t]

    comfy_status = _ctx.comfy.status_snapshot() if _ctx.comfy else {
        "running": False, "available": False, "pid": None, "base_url": "",
        "started_at": None, "last_health_at": None, "last_error": None, "restart_count": 0,
    }

    body = {
        "agent_id": _ctx.agent_id,
        "role": _ctx.role,
        "comfyui_available": bool(comfy_status["available"]),
        "has_kohya": bool((_ctx.image_gen_cfg.get("kohya") or {}).get("enabled", False)),
        "busy": False,
        "updates_available": {"comfyui": False, "kohya": False, "custom_nodes": []},
        "custom_nodes": [],
        "models": _filter("checkpoints"),
        "loras": _filter("loras"),
        "vaes": _filter("vae"),
        "embeddings": _filter("embeddings"),
        "upscale_models": _filter("upscale_models"),
        "gpu_info": _gpu_info(),
        "cache_usage": {
            "used_gb": cache_usage_gb(cache_root),
            "limit_gb": int((_ctx.image_gen_cfg.get("cache_lru") or {}).get("max_size_gb", 100)),
        },
        "nas": {"drive": _ctx.nas_drive, "base": _ctx.nas_drive, "mounted": bool(_ctx.nas_drive)},
    }
    return JSONResponse(body, headers={"X-Trace-Id": trace_id})


# --- /comfyui/status ---
@router.get("/comfyui/status")
async def comfyui_status(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    if _ctx.comfy is None:
        return _error_response(503, "ResourceUnavailableError", "image_gen not initialized", True, trace_id=trace_id)
    snap = _ctx.comfy.status_snapshot()
    snap["recent_logs"] = _ctx.comfy.recent_logs(50)
    return JSONResponse(snap, headers={"X-Trace-Id": trace_id})


@router.post("/comfyui/restart")
async def comfyui_restart(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    if _ctx.comfy is None:
        return _error_response(503, "ResourceUnavailableError", "image_gen not initialized", True, trace_id=trace_id)
    _ctx.comfy.stop()
    result = _ctx.comfy.start()
    if not result.get("ok"):
        return _error_response(503, "ResourceUnavailableError", result.get("error", "start failed"), bool(result.get("transient", True)), trace_id=trace_id)
    ok = await _ctx.comfy.wait_until_ready()
    if not ok:
        return _error_response(503, "ResourceUnavailableError", "ComfyUI did not become ready", True, trace_id=trace_id)
    return JSONResponse({"ok": True, "pid": result.get("pid")}, headers={"X-Trace-Id": trace_id})


# --- /cache/sync ---
def _nas_base_fallback() -> str:
    """nas_drive（ensure_mounted 返却値）が未設定のとき用の実効ベースパス生成。"""
    nas_cfg = (_ctx.image_gen_cfg.get("nas") or {}) if _ctx.image_gen_cfg else {}
    drive = (nas_cfg.get("mount_drive") or "N:").rstrip("\\")
    subpath = (nas_cfg.get("subpath") or "").strip("\\/").replace("/", "\\")
    return f"{drive}\\{subpath}" if subpath else drive


def _decide_source_path(cache_root: str, type_: str, filename: str, nas_path: Optional[str]) -> str:
    """NAS の絶対 SMB パス or image_gen.nas.mount_drive ベースで解決。"""
    if nas_path:
        # `//nas/share/path` → Windows UNC `\\nas\share\path` に変換
        p = nas_path.replace("/", os.sep)
        if p.startswith(os.sep + os.sep) is False and nas_path.startswith("//"):
            p = "\\\\" + nas_path.lstrip("/").replace("/", "\\")
        return p
    # ベース（ドライブ + 任意 subpath）配下から `models/<type>/<filename>` を参照
    base = (_ctx.nas_drive or _nas_base_fallback()).rstrip("\\")
    return os.path.join(base + os.sep, "models", type_, *filename.replace("\\", "/").split("/"))


async def _publish_sync(sync_id: str, event: str, data: dict) -> None:
    for q in list(_ctx.sync_subscribers.get(sync_id, [])):
        try:
            await q.put({"event": event, "data": data})
        except Exception:
            pass


async def _run_sync_job(sync_id: str, files: list[dict], verify_sha256: bool) -> None:
    prog = _ctx.syncs[sync_id]
    prog.status = "running"
    await _publish_sync(sync_id, "status", {"status": "running"})

    cache_root = _cache_root()
    prog.total_bytes = sum(int(f.get("size") or 0) for f in files)

    try:
        for f in files:
            if prog.cancelled:
                prog.status = "cancelled"
                await _publish_sync(sync_id, "status", {"status": "cancelled"})
                return
            type_ = f["type"]
            filename = f["filename"]
            expected_sha = (f.get("sha256") or "").lower()
            src = _decide_source_path(cache_root, type_, filename, f.get("nas_path"))
            dst = resolve_local_path(cache_root, type_, filename)
            prog.current_file = filename

            # 既にローカルに正しい sha のファイルがあればスキップ
            if os.path.exists(dst):
                existing_sha = sidecar_sha256(dst) if verify_sha256 else None
                if verify_sha256 and expected_sha:
                    if existing_sha is None:
                        # サイドカーがなければ計算
                        existing_sha = sha256_of_file(dst)
                        write_sha256_sidecar(dst, existing_sha)
                    if existing_sha == expected_sha:
                        prog.bytes_done += int(f.get("size") or os.path.getsize(dst))
                        prog.file_results.append({"filename": filename, "sha256_ok": True, "skipped": True})
                        await _publish_sync(sync_id, "file_done", {"filename": filename, "sha256_ok": True})
                        continue
                elif not expected_sha:
                    # sha256 未指定時は filename 一致のみでスキップ（Phase1 挙動）。
                    # ComfyUI が同ファイルを開いていると os.replace が WinError 5 を起こすため、
                    # 既存を信頼して再コピーを避ける。
                    size = os.path.getsize(dst)
                    prog.bytes_done += int(f.get("size") or size)
                    prog.file_results.append({"filename": filename, "sha256_ok": None, "skipped": True})
                    await _publish_sync(sync_id, "file_done", {"filename": filename, "sha256_ok": None})
                    continue

            if not os.path.exists(src):
                raise FileNotFoundError(f"source not found: {src}")

            def _on_chunk(n: int, _prog=prog, _sid=sync_id):
                _prog.bytes_done += n

            await asyncio.to_thread(copy_with_progress, src, dst, prog, _on_chunk)

            # sha256 検証
            sha_ok = True
            if verify_sha256 and expected_sha:
                actual = await asyncio.to_thread(sha256_of_file, dst)
                sha_ok = (actual == expected_sha)
                if sha_ok:
                    write_sha256_sidecar(dst, actual)
                else:
                    # 破損ファイル削除
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                    raise RuntimeError(f"sha256 mismatch for {filename}")

            prog.file_results.append({"filename": filename, "sha256_ok": sha_ok})
            await _publish_sync(sync_id, "file_done", {"filename": filename, "sha256_ok": sha_ok})
            await _publish_sync(sync_id, "progress", prog.snapshot())

        prog.status = "done"
        await _publish_sync(sync_id, "status", {"status": "done"})
    except Exception as e:
        prog.status = "failed"
        prog.error = {
            "error_class": "CacheSyncError",
            "message": str(e),
            "retryable": True,
        }
        await _publish_sync(sync_id, "error", prog.error)
    finally:
        await _publish_sync(sync_id, "done", {})


@router.post("/cache/sync")
async def cache_sync(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "ValidationError", "invalid json body", False, trace_id=trace_id)

    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        return _error_response(400, "ValidationError", "files[] required", False, trace_id=trace_id)

    for f in files:
        if not isinstance(f, dict) or not f.get("type") or not f.get("filename"):
            return _error_response(400, "ValidationError", "each file requires type and filename", False, trace_id=trace_id)

    sync_id = f"sync_{uuid.uuid4().hex[:16]}"
    prog = SyncProgress(sync_id=sync_id, total_bytes=sum(int(f.get("size") or 0) for f in files))
    _ctx.syncs[sync_id] = prog
    _ctx.sync_subscribers[sync_id] = []

    verify = bool(body.get("verify_sha256", True))
    asyncio.create_task(_run_sync_job(sync_id, files, verify))

    return JSONResponse(
        {
            "sync_id": sync_id,
            "status": "queued",
            "total_bytes": prog.total_bytes,
            "progress_url": f"/tools/image-gen/cache/sync/{sync_id}/stream",
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/cache/sync/{sync_id}/stream")
async def cache_sync_stream(sync_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    if sync_id not in _ctx.syncs:
        return _error_response(404, "ValidationError", f"sync_id not found: {sync_id}", False, trace_id=trace_id)
    q: asyncio.Queue = asyncio.Queue()
    _ctx.sync_subscribers.setdefault(sync_id, []).append(q)

    async def _gen():
        # 現状スナップショットを先に送信
        snap = _ctx.syncs[sync_id].snapshot()
        yield f"event: status\ndata: {json.dumps({'status': snap['status']})}\n\n"
        if snap["status"] in ("done", "failed", "cancelled"):
            yield "event: done\ndata: {}\n\n"
            return
        try:
            last_keepalive = time.time()
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    if time.time() - last_keepalive > 15:
                        yield ": keepalive\n\n"
                        last_keepalive = time.time()
                    continue
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
                if evt["event"] == "done":
                    return
        finally:
            try:
                _ctx.sync_subscribers.get(sync_id, []).remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"X-Trace-Id": trace_id, "Cache-Control": "no-store"},
    )


@router.post("/cache/sync/{sync_id}/cancel")
async def cache_sync_cancel(sync_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    prog = _ctx.syncs.get(sync_id)
    if not prog:
        return _error_response(404, "ValidationError", "sync_id not found", False, trace_id=trace_id)
    if prog.status in ("done", "failed", "cancelled"):
        return _error_response(409, "ValidationError", f"already terminal: {prog.status}", False, trace_id=trace_id)
    prog.cancelled = True
    return JSONResponse({"ok": True, "status": "cancelled"}, headers={"X-Trace-Id": trace_id})


# --- /image/generate ---
def _build_output_dir() -> str:
    base = (_ctx.nas_drive or _nas_base_fallback()).rstrip("\\")
    today = dt.datetime.now()
    rel = os.path.join("outputs", today.strftime("%Y-%m"), today.strftime("%Y-%m-%d"))
    return os.path.join(base + os.sep, rel)


async def _ensure_comfyui_started() -> Optional[dict]:
    """起動保証。エラーがあれば error 辞書を返す、成功なら None。"""
    if _ctx.comfy is None:
        return {"error_class": "ResourceUnavailableError", "message": "comfyui manager not initialized", "retryable": False}
    if _ctx.comfy.status_snapshot()["available"]:
        return None
    res = _ctx.comfy.start()
    if not res.get("ok"):
        return {
            "error_class": "ResourceUnavailableError",
            "message": res.get("error", "ComfyUI start failed"),
            "retryable": bool(res.get("transient", True)),
        }
    ok = await _ctx.comfy.wait_until_ready()
    if not ok:
        return {"error_class": "ResourceUnavailableError", "message": "ComfyUI did not become ready", "retryable": True}
    return None


@router.post("/image/generate")
async def image_generate(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate

    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "ValidationError", "invalid json body", False, trace_id=trace_id)

    job_id = body.get("job_id")
    workflow_json = body.get("workflow_json")
    inputs = body.get("inputs") or {}
    timeout_sec = int(body.get("timeout_sec") or 300)

    if not job_id or not isinstance(workflow_json, dict):
        return _error_response(400, "ValidationError", "job_id and workflow_json required", False, trace_id=trace_id)

    # 冪等: 既存 job なら現状を再掲
    existing = _ctx.jobs.get(job_id)
    if existing:
        return JSONResponse(
            {
                "job_id": job_id,
                "status": existing.status,
                "progress_url": f"/tools/image-gen/image/jobs/{job_id}/stream",
                "comfyui_prompt_id": existing.comfyui_prompt_id,
            },
            status_code=202,
            headers={"X-Trace-Id": trace_id},
        )

    # NAS マウント再確認
    if not _ctx.nas_drive:
        _ctx.nas_drive = ensure_mounted(_ctx.image_gen_cfg, _ctx.agent_dir)

    # ComfyUI 起動保証
    err = await _ensure_comfyui_started()
    if err:
        return _error_response(503, err["error_class"], err["message"], err["retryable"], trace_id=trace_id)

    # output_dir 決定（inputs に明示されていればそれ優先）
    output_dir = inputs.get("output_dir") or _build_output_dir()
    # `//nas/...` 表記なら UNC に変換
    if isinstance(output_dir, str) and output_dir.startswith("//"):
        output_dir = "\\\\" + output_dir.lstrip("/").replace("/", "\\")

    # filename_prefix 既定: 日時_job_seed
    if "filename_prefix" not in inputs:
        seed = inputs.get("seed", 0)
        inputs["filename_prefix"] = f"{dt.datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{job_id}_{seed}"

    # プレースホルダ置換
    try:
        resolved_workflow = substitute_placeholders(workflow_json, inputs)
    except Exception as e:
        return _error_response(400, "ValidationError", f"placeholder substitution failed: {e}", False, trace_id=trace_id)

    job = ImageJob(job_id=job_id, trace_id=trace_id)
    _ctx.jobs[job_id] = job

    runner = WorkflowRunner(
        comfyui_base_url=_ctx.comfy.base_url,
        comfyui_root=os.path.join(_ctx.image_gen_cfg.get("root") or "C:/secretary-bot", "comfyui"),
    )

    async def _runner_task():
        await runner.run(job, resolved_workflow, output_dir, timeout_sec)

    asyncio.create_task(_runner_task())

    # 投入直後は comfyui_prompt_id はまだ空の可能性があるため少し待って取得
    for _ in range(20):
        if job.comfyui_prompt_id or job.status != "queued":
            break
        await asyncio.sleep(0.1)

    return JSONResponse(
        {
            "job_id": job_id,
            "status": job.status if job.status != "queued" else "running",
            "progress_url": f"/tools/image-gen/image/jobs/{job_id}/stream",
            "comfyui_prompt_id": job.comfyui_prompt_id,
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/image/jobs/{job_id}")
async def image_job_status(job_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    job = _ctx.jobs.get(job_id)
    if not job:
        return _error_response(404, "ValidationError", "job_id not found", False, trace_id=trace_id)
    return JSONResponse(
        {
            "job_id": job.job_id,
            "status": job.status,
            "progress": job.progress,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "comfyui_prompt_id": job.comfyui_prompt_id,
            "result_paths": job.result_paths,
            "last_error": job.last_error,
        },
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/image/jobs/{job_id}/stream")
async def image_job_stream(job_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    job = _ctx.jobs.get(job_id)
    if not job:
        return _error_response(404, "ValidationError", "job_id not found", False, trace_id=trace_id)

    async def _gen():
        # 既に終端なら状態だけ吐いて終了
        if job.status in ("done", "failed", "cancelled"):
            yield f"event: status\ndata: {json.dumps({'status': job.status})}\n\n"
            if job.result_paths:
                yield f"event: result\ndata: {json.dumps({'result_paths': job.result_paths}, ensure_ascii=False)}\n\n"
            if job.last_error:
                yield f"event: error\ndata: {json.dumps(job.last_error, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
        last_keepalive = time.time()
        while True:
            try:
                evt = await asyncio.wait_for(job.events.get(), timeout=5.0)
            except asyncio.TimeoutError:
                if time.time() - last_keepalive > 15:
                    yield ": keepalive\n\n"
                    last_keepalive = time.time()
                continue
            yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
            if evt["event"] == "done":
                return

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"X-Trace-Id": trace_id, "Cache-Control": "no-store"},
    )


@router.post("/image/jobs/{job_id}/cancel")
async def image_job_cancel(job_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    job = _ctx.jobs.get(job_id)
    if not job:
        return _error_response(404, "ValidationError", "job_id not found", False, trace_id=trace_id)
    if job.status in ("done", "failed", "cancelled"):
        return _error_response(409, "ValidationError", f"already terminal: {job.status}", False, trace_id=trace_id)
    job.cancelled = True
    if _ctx.comfy and _ctx.comfy.status_snapshot()["available"]:
        runner = WorkflowRunner(
            comfyui_base_url=_ctx.comfy.base_url,
            comfyui_root=os.path.join(_ctx.image_gen_cfg.get("root") or "C:/secretary-bot", "comfyui"),
        )
        try:
            await runner.interrupt()
        except Exception:
            pass
    return JSONResponse({"ok": True, "status": "cancelled"}, headers={"X-Trace-Id": trace_id})
