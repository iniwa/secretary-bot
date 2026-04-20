"""image_gen FastAPI router — /capability, /image/generate, /cache/sync, /comfyui/status。

API 仕様: docs/image_gen/api.md
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .cache_manager import (
    SyncProgress,
    cache_usage_gb,
    copy_with_progress,
    iter_cache_entries,
    resolve_local_path,
    sha256_of_file,
    sidecar_sha256,
    write_sha256_sidecar,
)
from .comfyui_manager import ComfyUIManager
from .kohya_train import (
    cancel_train as _lora_train_cancel,
)
from .kohya_train import (
    get_train_task as _lora_train_get,
)
from .kohya_train import (
    list_train_tasks as _lora_train_list,
)
from .kohya_train import (
    log_stream as _lora_train_log_stream,
)
from .kohya_train import (
    run_kohya_train,
)
from .lora_sync import local_project_dirs as _lora_local_dirs
from .lora_sync import run_lora_sync
from .nas_mount import ensure_mounted
from .setup_manager import (
    get_task as _setup_get_task,
)
from .setup_manager import (
    list_tasks as _setup_list_tasks,
)
from .setup_manager import (
    run_comfyui_setup,
    run_comfyui_update,
    run_kohya_setup,
)
from .wd14_tagger import run_wd14_tagging
from .workflow_runner import ImageJob, WorkflowRunner, substitute_placeholders

router = APIRouter()

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")


# --- コンテキスト（init_image_gen で差し込む） ---
class _Ctx:
    role: str = "unknown"
    agent_id: str = "unknown"
    image_gen_cfg: dict = {}
    agent_dir: str = ""
    comfy: ComfyUIManager | None = None
    nas_drive: str | None = None
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
    # エージェント再起動時、Code Update 等で agent.py だけが落ちて
    # ComfyUI プロセスが port を握ったまま残るケースに備えて、起動時に
    # 一発だけ port を叩き、応答があれば既存プロセスを採用する。
    # （UI 上「停止」と表示されてしまう問題を回避）
    try:
        _ctx.comfy.adopt_if_alive()
    except Exception as e:
        print(f"[image_gen] adopt_if_alive failed: {e}")


# --- 共通ユーティリティ ---

def _verify(request: Request) -> None:
    if not _SECRET_TOKEN:
        return
    if request.headers.get("X-Agent-Token", "") != _SECRET_TOKEN:
        raise HTTPException(status_code=401, detail={"error_class": "AuthError", "message": "invalid token", "retryable": False})


def _trace_id(request: Request) -> str:
    return request.headers.get("X-Trace-Id") or f"agent_{uuid.uuid4().hex[:12]}"


def _error_response(status: int, error_class: str, message: str, retryable: bool, detail: dict | None = None, trace_id: str = "") -> JSONResponse:
    body = {
        "error_class": error_class,
        "message": message,
        "retryable": retryable,
        "detail": {**(detail or {}), **({"trace_id": trace_id} if trace_id else {})},
    }
    headers = {"X-Trace-Id": trace_id} if trace_id else {}
    return JSONResponse(status_code=status, content=body, headers=headers)


def _require_enabled(trace_id: str) -> JSONResponse | None:
    if not _ctx.image_gen_cfg.get("enabled", False):
        return _error_response(503, "ResourceUnavailableError", "image_gen disabled on this agent", True, trace_id=trace_id)
    return None


def _cache_root() -> str:
    return _ctx.image_gen_cfg.get("cache") or "C:/secretary-bot-cache"


def _job_stream_url(request: Request, job_id: str) -> str:
    """submit レスポンスに載せる progress_url を、呼ばれたエンドポイントに合わせて組み立てる。
    /generation/submit で呼ばれたら /generation/jobs/{id}/stream、
    /image/generate で呼ばれたら /image/jobs/{id}/stream を返す。"""
    path = str(request.url.path)
    if "/generation/" in path:
        return f"/tools/image-gen/generation/jobs/{job_id}/stream"
    return f"/tools/image-gen/image/jobs/{job_id}/stream"


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


@router.post("/comfyui/start")
async def comfyui_start(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    if _ctx.comfy is None:
        return _error_response(503, "ResourceUnavailableError", "image_gen not initialized", True, trace_id=trace_id)
    snap = _ctx.comfy.status_snapshot()
    if snap.get("available"):
        return JSONResponse({"ok": True, "already_running": True, "pid": snap.get("pid")}, headers={"X-Trace-Id": trace_id})
    result = _ctx.comfy.start()
    if not result.get("ok"):
        return _error_response(503, "ResourceUnavailableError", result.get("error", "start failed"), bool(result.get("transient", True)), trace_id=trace_id)
    ok = await _ctx.comfy.wait_until_ready()
    if not ok:
        return _error_response(503, "ResourceUnavailableError", "ComfyUI did not become ready", True, trace_id=trace_id)
    return JSONResponse({"ok": True, "pid": result.get("pid")}, headers={"X-Trace-Id": trace_id})


@router.post("/comfyui/stop")
async def comfyui_stop(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    if _ctx.comfy is None:
        return _error_response(503, "ResourceUnavailableError", "image_gen not initialized", True, trace_id=trace_id)
    result = _ctx.comfy.stop()
    # stop() は ok / stopped / adopted_kill / pid / error / error_class / note を返し得る。
    # 失敗時（ok=False）は 4xx/5xx で詳細を返し WebGUI 側で理由表示できるようにする。
    if not result.get("ok", True):
        status = 403 if result.get("error_class") == "PermissionError" else 500
        return JSONResponse(result, status_code=status, headers={"X-Trace-Id": trace_id})
    return JSONResponse(result, headers={"X-Trace-Id": trace_id})


# --- /comfyui/history: ComfyUI の /history を整形して返す（プリセット取り込み元） ---
@router.get("/comfyui/history")
async def comfyui_history(request: Request, limit: int = 20):
    _verify(request)
    trace_id = _trace_id(request)
    if _ctx.comfy is None:
        return _error_response(503, "ResourceUnavailableError", "image_gen not initialized", True, trace_id=trace_id)
    snap = _ctx.comfy.status_snapshot()
    if not snap.get("available"):
        return JSONResponse({"items": [], "available": False}, headers={"X-Trace-Id": trace_id})
    limit = max(1, min(100, int(limit or 20)))
    url = f"{_ctx.comfy.base_url}/history"
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.get(url)
            resp.raise_for_status()
            data = resp.json() or {}
    except Exception as e:
        return _error_response(502, "ComfyUIError", f"history fetch failed: {e}", True, trace_id=trace_id)

    items: list[dict] = []
    # /history は dict[prompt_id, entry]。実行順に並んでいる想定で新しい順にする。
    for prompt_id, entry in reversed(list(data.items())):
        if not isinstance(entry, dict):
            continue
        prompt_meta = entry.get("prompt") or []
        wf_api = None
        # ComfyUI 形式: [queue_num, prompt_id, workflow_api, extra, outputs_to_execute]
        if isinstance(prompt_meta, list) and len(prompt_meta) >= 3 and isinstance(prompt_meta[2], dict):
            wf_api = prompt_meta[2]
        outputs = entry.get("outputs") or {}
        files: list[str] = []
        for _nid, out in outputs.items():
            if not isinstance(out, dict):
                continue
            for img in (out.get("images") or []):
                fn = img.get("filename") or ""
                if fn:
                    files.append(fn)
        status = entry.get("status") or {}
        items.append({
            "prompt_id": prompt_id,
            "workflow": wf_api,
            "output_files": files[:5],
            "status_str": status.get("status_str") or "",
            "completed": bool(status.get("completed", False)),
        })
        if len(items) >= limit:
            break
    return JSONResponse({"items": items, "available": True}, headers={"X-Trace-Id": trace_id})


# --- /comfyui/setup /comfyui/update /kohya/setup ---

_COMFYUI_DEFAULT_REPO = "https://github.com/comfyanonymous/ComfyUI.git"
_COMFYUI_DEFAULT_REF = "master"
_KOHYA_DEFAULT_REPO = "https://github.com/kohya-ss/sd-scripts.git"
_KOHYA_DEFAULT_REF = "main"


def _setup_cfg() -> dict:
    return (_ctx.image_gen_cfg.get("setup") or {}) if _ctx.image_gen_cfg else {}


@router.post("/comfyui/setup")
async def comfyui_setup_endpoint(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    root = _ctx.image_gen_cfg.get("root") or "C:/secretary-bot"
    cfg = _setup_cfg()
    repo = body.get("repo_url") or cfg.get("comfyui_repo") or _COMFYUI_DEFAULT_REPO
    ref = body.get("ref") or cfg.get("comfyui_ref") or _COMFYUI_DEFAULT_REF
    cuda_idx = body.get("cuda_index_url") or cfg.get("cuda_index_url")
    task = await run_comfyui_setup(root, repo_url=repo, ref=ref, cuda_index_url=cuda_idx)
    return JSONResponse(
        {"task_id": task.task_id, "status": task.status, "kind": task.kind,
         "progress_url": f"/tools/image-gen/setup/{task.task_id}"},
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.post("/comfyui/update")
async def comfyui_update_endpoint(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    root = _ctx.image_gen_cfg.get("root") or "C:/secretary-bot"
    cfg = _setup_cfg()
    ref = body.get("ref") or cfg.get("comfyui_ref") or _COMFYUI_DEFAULT_REF
    # ComfyUI プロセスが動いていたら停止してから更新
    if _ctx.comfy is not None:
        snap = _ctx.comfy.status_snapshot()
        if snap.get("available") or snap.get("running"):
            _ctx.comfy.stop()
    task = await run_comfyui_update(root, ref=ref)
    return JSONResponse(
        {"task_id": task.task_id, "status": task.status, "kind": task.kind,
         "progress_url": f"/tools/image-gen/setup/{task.task_id}"},
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.post("/kohya/setup")
async def kohya_setup_endpoint(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    kohya_cfg = _ctx.image_gen_cfg.get("kohya") or {}
    if not kohya_cfg.get("enabled", False):
        return _error_response(
            503, "ResourceUnavailableError",
            "kohya disabled on this agent", True, trace_id=trace_id,
        )
    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    root = _ctx.image_gen_cfg.get("root") or "C:/secretary-bot"
    cfg = _setup_cfg()
    repo = body.get("repo_url") or cfg.get("kohya_repo") or _KOHYA_DEFAULT_REPO
    ref = body.get("ref") or cfg.get("kohya_ref") or _KOHYA_DEFAULT_REF
    cuda_idx = body.get("cuda_index_url") or cfg.get("cuda_index_url")
    task = await run_kohya_setup(root, repo_url=repo, ref=ref, cuda_index_url=cuda_idx)
    return JSONResponse(
        {"task_id": task.task_id, "status": task.status, "kind": task.kind,
         "progress_url": f"/tools/image-gen/setup/{task.task_id}"},
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/setup/{task_id}")
async def setup_status_endpoint(task_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    task = _setup_get_task(task_id)
    if task is None:
        return _error_response(
            404, "ValidationError",
            f"task_id not found: {task_id}", False, trace_id=trace_id,
        )
    return JSONResponse(task.snapshot(), headers={"X-Trace-Id": trace_id})


@router.get("/setup")
async def setup_list_endpoint(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    return JSONResponse(
        {"tasks": _setup_list_tasks()},
        headers={"X-Trace-Id": trace_id},
    )


# --- /cache/sync ---
def _nas_base_fallback() -> str:
    """nas_drive（ensure_mounted 返却値）が未設定のとき用の実効ベースパス生成。"""
    nas_cfg = (_ctx.image_gen_cfg.get("nas") or {}) if _ctx.image_gen_cfg else {}
    drive = (nas_cfg.get("mount_drive") or "N:").rstrip("\\")
    subpath = (nas_cfg.get("subpath") or "").strip("\\/").replace("/", "\\")
    return f"{drive}\\{subpath}" if subpath else drive


def _decide_source_path(cache_root: str, type_: str, filename: str, nas_path: str | None) -> str:
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
                except TimeoutError:
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


async def _ensure_comfyui_started() -> dict | None:
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
@router.post("/generation/submit")
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
                "progress_url": _job_stream_url(request, job_id),
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
            "progress_url": _job_stream_url(request, job_id),
            "comfyui_prompt_id": job.comfyui_prompt_id,
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/image/jobs/{job_id}")
@router.get("/generation/jobs/{job_id}")
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
            "result_kinds": job.result_kinds,
            "last_error": job.last_error,
        },
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/image/jobs/{job_id}/stream")
@router.get("/generation/jobs/{job_id}/stream")
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
                payload = {
                    "result_paths": job.result_paths,
                    "result_kinds": job.result_kinds,
                }
                yield f"event: result\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if job.last_error:
                yield f"event: error\ndata: {json.dumps(job.last_error, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
        last_keepalive = time.time()
        while True:
            try:
                evt = await asyncio.wait_for(job.events.get(), timeout=5.0)
            except TimeoutError:
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
@router.post("/generation/jobs/{job_id}/cancel")
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


# --- /lora/dataset/tag : WD14 自動タグ付け ---
def _lora_nas_base() -> str:
    """NAS の実効ベースパス（lora_datasets / lora_work の親）。"""
    return (_ctx.nas_drive or _nas_base_fallback()).rstrip("\\")


def _lora_subdir_cfg() -> dict:
    """image_gen.nas.{lora_datasets_subdir,lora_work_subdir} を辞書で返す。"""
    nas_cfg = (_ctx.image_gen_cfg.get("nas") or {}) if _ctx.image_gen_cfg else {}
    return {
        "datasets": (nas_cfg.get("lora_datasets_subdir") or "lora_datasets").strip("\\/"),
        "work": (nas_cfg.get("lora_work_subdir") or "lora_work").strip("\\/"),
    }


@router.post("/lora/dataset/tag")
async def lora_dataset_tag(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    kohya_cfg = _ctx.image_gen_cfg.get("kohya") or {}
    if not kohya_cfg.get("enabled", False):
        return _error_response(
            503, "ResourceUnavailableError",
            "kohya disabled on this agent", True, trace_id=trace_id,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return _error_response(
            400, "ValidationError", "body must be an object", False, trace_id=trace_id,
        )
    project_name = (body.get("project_name") or "").strip()
    if not project_name:
        return _error_response(
            400, "ValidationError", "project_name is required", False, trace_id=trace_id,
        )
    threshold = float(body.get("threshold") or 0.35)
    repo_id = (body.get("repo_id") or "").strip() or None
    trigger_word = body.get("trigger_word")
    if trigger_word is not None:
        trigger_word = str(trigger_word).strip() or None

    subdirs = _lora_subdir_cfg()
    base = _lora_nas_base()
    dataset_dir = os.path.join(base + os.sep, subdirs["datasets"], project_name)
    if not os.path.isdir(dataset_dir):
        return _error_response(
            404, "ValidationError",
            f"dataset dir not found: {dataset_dir}", False, trace_id=trace_id,
        )

    root = _ctx.image_gen_cfg.get("root") or "C:/secretary-bot"
    kwargs = {"dataset_dir": dataset_dir, "threshold": threshold}
    if repo_id:
        kwargs["repo_id"] = repo_id
    if trigger_word:
        kwargs["trigger_word"] = trigger_word
    task = await run_wd14_tagging(root, **kwargs)
    return JSONResponse(
        {
            "task_id": task.task_id, "status": task.status, "kind": task.kind,
            "dataset_dir": dataset_dir,
            "progress_url": f"/tools/image-gen/lora/tag/{task.task_id}",
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/lora/tag/{task_id}")
async def lora_tag_status(task_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    task = _setup_get_task(task_id)
    if task is None or task.kind != "wd14_tagging":
        return _error_response(
            404, "ValidationError",
            f"task_id not found: {task_id}", False, trace_id=trace_id,
        )
    return JSONResponse(task.snapshot(), headers={"X-Trace-Id": trace_id})


@router.post("/lora/dataset/sync")
async def lora_dataset_sync(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return _error_response(
            400, "ValidationError", "body must be an object", False, trace_id=trace_id,
        )
    project_name = (body.get("project_name") or "").strip()
    if not project_name:
        return _error_response(
            400, "ValidationError", "project_name is required", False, trace_id=trace_id,
        )

    subdirs = _lora_subdir_cfg()
    base = _lora_nas_base()
    nas_dataset_dir = os.path.join(base + os.sep, subdirs["datasets"], project_name)
    nas_work_dir = os.path.join(base + os.sep, subdirs["work"], project_name)
    if not os.path.isdir(nas_dataset_dir):
        return _error_response(
            404, "ValidationError",
            f"nas dataset dir not found: {nas_dataset_dir}", False, trace_id=trace_id,
        )
    if not os.path.isdir(nas_work_dir):
        return _error_response(
            404, "ValidationError",
            f"nas work dir not found: {nas_work_dir}", False, trace_id=trace_id,
        )

    root = _ctx.image_gen_cfg.get("root") or "C:/secretary-bot"
    task = await run_lora_sync(
        root,
        project_name=project_name,
        nas_dataset_dir=nas_dataset_dir,
        nas_work_dir=nas_work_dir,
    )
    local = _lora_local_dirs(root, project_name)
    return JSONResponse(
        {
            "task_id": task.task_id, "status": task.status, "kind": task.kind,
            "local_dirs": local,
            "progress_url": f"/tools/image-gen/lora/sync/{task.task_id}",
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/lora/sync/{task_id}")
async def lora_sync_status(task_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    task = _setup_get_task(task_id)
    if task is None or task.kind != "lora_sync":
        return _error_response(
            404, "ValidationError",
            f"task_id not found: {task_id}", False, trace_id=trace_id,
        )
    return JSONResponse(task.snapshot(), headers={"X-Trace-Id": trace_id})


# --- /lora/train/* : SDXL LoRA 学習 ---

@router.post("/lora/train/start")
async def lora_train_start(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return _error_response(
            400, "ValidationError",
            "body must be an object", False, trace_id=trace_id,
        )
    project_name = (body.get("project_name") or "").strip()
    if not project_name:
        return _error_response(
            400, "ValidationError",
            "project_name is required", False, trace_id=trace_id,
        )

    root = _ctx.image_gen_cfg.get("root") or "C:/secretary-bot"
    local = _lora_local_dirs(root, project_name)
    work = local["work"]
    cfg = os.path.join(work, "config.local.toml")
    ds = os.path.join(work, "dataset.local.toml")
    if not os.path.isfile(cfg):
        return _error_response(
            404, "ValidationError",
            f"config.local.toml not found: {cfg} (sync first)",
            False, trace_id=trace_id,
        )
    if not os.path.isfile(ds):
        return _error_response(
            404, "ValidationError",
            f"dataset.local.toml not found: {ds} (sync first)",
            False, trace_id=trace_id,
        )

    task = await run_kohya_train(
        root,
        project_name=project_name,
        config_file=cfg, dataset_config=ds,
    )
    return JSONResponse(
        {
            "task_id": task.task_id, "status": task.status, "kind": task.kind,
            "progress_url": f"/tools/image-gen/lora/train/{task.task_id}",
            "stream_url": f"/tools/image-gen/lora/train/{task.task_id}/stream",
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get("/lora/train/{task_id}")
async def lora_train_status(task_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    task = _lora_train_get(task_id)
    if task is None:
        return _error_response(
            404, "ValidationError",
            f"task_id not found: {task_id}", False, trace_id=trace_id,
        )
    return JSONResponse(task.snapshot(), headers={"X-Trace-Id": trace_id})


@router.get("/lora/train")
async def lora_train_list(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    return JSONResponse(
        {"items": _lora_train_list()}, headers={"X-Trace-Id": trace_id},
    )


@router.get("/lora/train/{task_id}/stream")
async def lora_train_stream(task_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    task = _lora_train_get(task_id)
    if task is None:
        return _error_response(
            404, "ValidationError",
            f"task_id not found: {task_id}", False, trace_id=trace_id,
        )
    try:
        after_seq = int(request.query_params.get("after_seq") or 0)
    except ValueError:
        after_seq = 0

    async def _gen():
        # initial snapshot
        snap = task.snapshot()
        snap.pop("log_tail", None)
        yield f"event: status\ndata: {json.dumps(snap)}\n\n"
        try:
            async for entry in _lora_train_log_stream(
                task_id, after_seq=after_seq,
            ):
                yield f"event: log\ndata: {json.dumps(entry)}\n\n"
                # periodic status updates piggy-back on every ~10 log lines
                if entry.get("seq", 0) % 10 == 0:
                    s2 = task.snapshot()
                    s2.pop("log_tail", None)
                    yield f"event: status\ndata: {json.dumps(s2)}\n\n"
        except asyncio.CancelledError:
            return
        final = task.snapshot()
        final.pop("log_tail", None)
        yield f"event: status\ndata: {json.dumps(final)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "X-Trace-Id": trace_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/lora/train/{task_id}/cancel")
async def lora_train_cancel(task_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    task = _lora_train_get(task_id)
    if task is None:
        return _error_response(
            404, "ValidationError",
            f"task_id not found: {task_id}", False, trace_id=trace_id,
        )
    ok = await _lora_train_cancel(task_id)
    return JSONResponse(
        {"ok": ok, "status": task.status},
        headers={"X-Trace-Id": trace_id},
    )
