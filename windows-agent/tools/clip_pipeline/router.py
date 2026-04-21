"""clip_pipeline FastAPI router — /clip-pipeline/* の API 実装。

Pi の Dispatcher からの呼び出しを受け、Whisper キャッシュ同期 / ジョブ投入 /
進捗ストリームを返す。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .runner import ClipJob, run_clip_job
from .whisper_cache import (
    WhisperSyncProgress,
    is_model_cached,
    list_local_models,
    list_nas_models,
    run_whisper_cache_sync,
)

router = APIRouter()

_SECRET_TOKEN = os.environ.get("AGENT_SECRET_TOKEN", "")
_PREFIX = "/clip-pipeline"
_API_VERSION = 2

# GET /clip-pipeline/inputs で列挙する動画拡張子（小文字で比較）
_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".ts", ".m2ts", ".webm", ".flv")


class _Ctx:
    role: str = "unknown"
    agent_id: str = "unknown"
    cfg: dict = {}
    enabled: bool = False
    agent_dir: str = ""
    # ジョブ管理
    jobs: dict[str, ClipJob] = {}
    job_tasks: dict[str, asyncio.Task] = {}
    # Whisper キャッシュ同期管理
    syncs: dict[str, WhisperSyncProgress] = {}
    sync_subscribers: dict[str, list[asyncio.Queue]] = {}


_ctx = _Ctx()


def init_clip_pipeline(role: str, agent_config: dict, agent_dir: str) -> None:
    """agent.py の lifespan から呼び出す初期化。image_gen と同じ契約。"""
    cfg = (agent_config or {}).get("clip_pipeline") or {}
    _ctx.role = role
    _ctx.agent_id = f"{role}-pc"
    _ctx.cfg = cfg
    _ctx.agent_dir = agent_dir
    _ctx.enabled = bool(cfg.get("enabled", False))


def _cache_root() -> str:
    return _ctx.cfg.get("cache") or "C:/secretary-bot-cache/whisper"


def _nas_whisper_base() -> str:
    """NAS 上の Whisper モデル置き場（例: `N:\\auto-kirinuki\\models\\whisper`）。"""
    nas = _ctx.cfg.get("nas") or {}
    base = nas.get("whisper_base")
    if base:
        return _normalize_path(base)
    # 画像生成と同じ drive を流用する方針
    drive = nas.get("mount_drive") or "N:"
    subpath = nas.get("subpath") or "auto-kirinuki"
    whisper_sub = nas.get("whisper_subdir") or "models/whisper"
    return os.path.join(drive.rstrip("\\") + os.sep, subpath, whisper_sub).replace("/", os.sep)


def _nas_inputs_base() -> str:
    """NAS 上の動画入力フォルダ（例: `N:\\auto-kirinuki\\inputs`）。"""
    nas = _ctx.cfg.get("nas") or {}
    base = nas.get("inputs_base")
    if base:
        return _normalize_path(base)
    drive = nas.get("mount_drive") or "N:"
    subpath = nas.get("subpath") or "auto-kirinuki"
    inputs_sub = nas.get("inputs_subdir") or "inputs"
    return os.path.join(drive.rstrip("\\") + os.sep, subpath, inputs_sub).replace("/", os.sep)


def _nas_outputs_base() -> str:
    """NAS 上の成果物出力フォルダ（例: `N:\\auto-kirinuki\\outputs`）。"""
    nas = _ctx.cfg.get("nas") or {}
    base = nas.get("outputs_base")
    if base:
        return _normalize_path(base)
    drive = nas.get("mount_drive") or "N:"
    subpath = nas.get("subpath") or "auto-kirinuki"
    outputs_sub = nas.get("outputs_subdir") or "outputs"
    return os.path.join(drive.rstrip("\\") + os.sep, subpath, outputs_sub).replace("/", os.sep)


def _normalize_path(p: str) -> str:
    """`//server/share/dir` → UNC に変換。"""
    if p.startswith("//"):
        return "\\\\" + p.lstrip("/").replace("/", "\\")
    return p


def _verify(request: Request) -> None:
    if not _SECRET_TOKEN:
        return
    if request.headers.get("X-Agent-Token", "") != _SECRET_TOKEN:
        raise HTTPException(
            status_code=401,
            detail={"error_class": "AuthError", "message": "invalid token", "retryable": False},
        )


def _trace_id(request: Request) -> str:
    return request.headers.get("X-Trace-Id") or f"agent_{uuid.uuid4().hex[:12]}"


def _error_response(status: int, error_class: str, message: str, retryable: bool, trace_id: str = "") -> JSONResponse:
    body = {
        "error_class": error_class,
        "message": message,
        "retryable": retryable,
        "detail": {"trace_id": trace_id} if trace_id else {},
    }
    headers = {"X-Trace-Id": trace_id} if trace_id else {}
    return JSONResponse(status_code=status, content=body, headers=headers)


def _require_enabled(trace_id: str) -> JSONResponse | None:
    if not _ctx.enabled:
        return _error_response(503, "ResourceUnavailableError", "clip_pipeline disabled on this agent", True, trace_id=trace_id)
    return None


def _gpu_info() -> dict:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,compute_cap",
             "--format=csv,noheader,nounits"],
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


def _ffmpeg_version() -> str:
    try:
        out = subprocess.check_output(["ffmpeg", "-version"], text=True, errors="replace", timeout=3)
        first = out.strip().splitlines()[0] if out else ""
        return first.split(" ")[2] if first.startswith("ffmpeg version") else first
    except Exception:
        return ""


def _busy() -> bool:
    for j in _ctx.jobs.values():
        if j.status in ("queued", "running"):
            return True
    return False


# --- /capability ---
@router.get(f"{_PREFIX}/capability")
async def capability(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    cache_root = _cache_root()
    nas_base = _nas_whisper_base()
    try:
        inputs_base = _nas_inputs_base()
    except Exception:
        inputs_base = ""
    try:
        outputs_base = _nas_outputs_base()
    except Exception:
        outputs_base = ""
    body = {
        "agent_id": _ctx.agent_id,
        "role": _ctx.role,
        "enabled": _ctx.enabled,
        "busy": _busy(),
        "gpu_info": _gpu_info(),
        "ffmpeg_version": _ffmpeg_version(),
        "whisper_models_local": list_local_models(cache_root),
        "whisper_models_nas": list_nas_models(nas_base),
        "cache_root": cache_root,
        "nas_whisper_base": nas_base,
        "nas_inputs_base": inputs_base,
        "nas_outputs_base": outputs_base,
        "api_version": _API_VERSION,
    }
    return JSONResponse(body, headers={"X-Trace-Id": trace_id})


# --- /inputs ---
@router.get(f"{_PREFIX}/inputs")
async def inputs_list(request: Request):
    """NAS inputs フォルダ直下の動画ファイル一覧を返す（再帰なし）。"""
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate

    try:
        base = _nas_inputs_base()
    except Exception as e:
        return JSONResponse(
            {"base": "", "files": [], "error": f"failed to resolve inputs base: {e}"},
            headers={"X-Trace-Id": trace_id},
        )

    if not base or not os.path.isdir(base):
        return JSONResponse(
            {"base": base, "files": [], "error": f"inputs base not found or not a directory: {base}"},
            headers={"X-Trace-Id": trace_id},
        )

    files: list[dict] = []
    try:
        with os.scandir(base) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                name = entry.name
                _, ext = os.path.splitext(name)
                if ext.lower() not in _VIDEO_EXTS:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    size = int(st.st_size)
                    mtime = int(st.st_mtime)
                except OSError:
                    size = 0
                    mtime = 0
                full_path = os.path.join(base, name)
                files.append({
                    "name": name,
                    "full_path": full_path,
                    "size": size,
                    "mtime": mtime,
                })
    except OSError as e:
        return JSONResponse(
            {"base": base, "files": [], "error": f"failed to scan inputs base: {e}"},
            headers={"X-Trace-Id": trace_id},
        )

    files.sort(key=lambda f: f["name"])

    return JSONResponse(
        {"base": base, "files": files},
        headers={"X-Trace-Id": trace_id},
    )


# --- /whisper/cache-sync ---
@router.post(f"{_PREFIX}/whisper/cache-sync")
async def whisper_cache_sync(request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate

    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "ValidationError", "invalid json body", False, trace_id=trace_id)

    model = body.get("model")
    if not model or not isinstance(model, str):
        return _error_response(400, "ValidationError", "model required", False, trace_id=trace_id)

    sync_id = f"wsync_{uuid.uuid4().hex[:16]}"
    prog = WhisperSyncProgress(sync_id=sync_id, model=model)
    _ctx.syncs[sync_id] = prog
    _ctx.sync_subscribers[sync_id] = []

    async def _publish(event: str, data: dict) -> None:
        for q in list(_ctx.sync_subscribers.get(sync_id, [])):
            try:
                await q.put({"event": event, "data": data})
            except Exception:
                pass

    async def _run():
        await run_whisper_cache_sync(prog, _nas_whisper_base(), _cache_root(), _publish)

    asyncio.create_task(_run())

    return JSONResponse(
        {
            "sync_id": sync_id,
            "status": "queued",
            "model": model,
            "progress_url": f"{_PREFIX}/whisper/cache-sync/{sync_id}/events",
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get(f"{_PREFIX}/whisper/cache-sync/{{sync_id}}/events")
async def whisper_cache_sync_events(sync_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    prog = _ctx.syncs.get(sync_id)
    if not prog:
        return _error_response(404, "ValidationError", f"sync_id not found: {sync_id}", False, trace_id=trace_id)

    q: asyncio.Queue = asyncio.Queue()
    _ctx.sync_subscribers.setdefault(sync_id, []).append(q)

    async def _gen():
        snap = prog.snapshot()
        yield f"event: status\ndata: {json.dumps({'status': snap['status']})}\n\n"
        if snap["status"] in ("done", "failed", "cancelled"):
            if prog.error:
                yield f"event: error\ndata: {json.dumps(prog.error, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
        try:
            last_ka = time.time()
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=5.0)
                except TimeoutError:
                    if time.time() - last_ka > 15:
                        yield ": keepalive\n\n"
                        last_ka = time.time()
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


@router.post(f"{_PREFIX}/whisper/cache-sync/{{sync_id}}/cancel")
async def whisper_cache_sync_cancel(sync_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    prog = _ctx.syncs.get(sync_id)
    if not prog:
        return _error_response(404, "ValidationError", "sync_id not found", False, trace_id=trace_id)
    if prog.status in ("done", "failed", "cancelled"):
        return _error_response(409, "ValidationError", f"already terminal: {prog.status}", False, trace_id=trace_id)
    prog.cancelled = True
    return JSONResponse({"ok": True, "status": "cancelled"}, headers={"X-Trace-Id": trace_id})


# --- /jobs/start ---
@router.post(f"{_PREFIX}/jobs/start")
async def jobs_start(request: Request):
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
    video_path = body.get("video_path")
    output_dir = body.get("output_dir")
    whisper_model = body.get("whisper_model")
    ollama_model = body.get("ollama_model")
    params = body.get("params") or {}

    if not job_id or not video_path or not output_dir or not whisper_model or not ollama_model:
        return _error_response(400, "ValidationError",
            "job_id, video_path, output_dir, whisper_model, ollama_model are required",
            False, trace_id=trace_id)

    # 冪等: 既存 job なら現状を再掲
    existing = _ctx.jobs.get(job_id)
    if existing:
        return JSONResponse(
            {
                "job_id": job_id,
                "status": existing.status,
                "progress_url": f"{_PREFIX}/jobs/{job_id}/events",
            },
            status_code=202,
            headers={"X-Trace-Id": trace_id},
        )

    video_path = _normalize_path(video_path)
    output_dir = _normalize_path(output_dir)

    if not os.path.exists(video_path):
        return _error_response(400, "ValidationError",
            f"video_path not found on agent: {video_path}",
            False, trace_id=trace_id)

    # Whisper モデルがローカルにキャッシュされているか確認
    if not is_model_cached(_cache_root(), whisper_model):
        return _error_response(
            423, "ResourceUnavailableError",
            f"whisper model '{whisper_model}' not cached locally; call /whisper/cache-sync first",
            True, trace_id=trace_id,
        )

    # 同時実行は 1 本まで
    for j in _ctx.jobs.values():
        if j.status in ("queued", "running"):
            return _error_response(
                423, "ResourceUnavailableError",
                f"agent busy with job {j.job_id}",
                True, trace_id=trace_id,
            )

    job = ClipJob(job_id=job_id, trace_id=trace_id)
    _ctx.jobs[job_id] = job

    cache_root = _cache_root()

    async def _run_task():
        await run_clip_job(
            job,
            video_path=video_path,
            output_dir=output_dir,
            whisper_model=whisper_model,
            ollama_model=ollama_model,
            params=params,
            whisper_download_root=cache_root,
        )

    task = asyncio.create_task(_run_task())
    _ctx.job_tasks[job_id] = task

    return JSONResponse(
        {
            "job_id": job_id,
            "status": "running",
            "progress_url": f"{_PREFIX}/jobs/{job_id}/events",
        },
        status_code=202,
        headers={"X-Trace-Id": trace_id},
    )


@router.get(f"{_PREFIX}/jobs/{{job_id}}")
async def jobs_get(job_id: str, request: Request):
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
            "step": job.step,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "result": job.result or {},
            "last_error": job.last_error,
        },
        headers={"X-Trace-Id": trace_id},
    )


@router.get(f"{_PREFIX}/jobs/{{job_id}}/events")
async def jobs_events(job_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    job = _ctx.jobs.get(job_id)
    if not job:
        return _error_response(404, "ValidationError", "job_id not found", False, trace_id=trace_id)

    async def _gen():
        # 既終端なら snapshot だけ
        if job.done_flag:
            yield f"event: status\ndata: {json.dumps({'status': job.status})}\n\n"
            if job.result:
                yield f"event: result\ndata: {json.dumps({'highlights_count': len(job.result.get('highlights') or []), 'edl_path': job.result.get('edl_path') or '', 'clip_paths': job.result.get('clip_paths') or []}, ensure_ascii=False)}\n\n"
            if job.last_error:
                yield f"event: error\ndata: {json.dumps(job.last_error, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
        last_ka = time.time()
        while True:
            try:
                evt = await asyncio.wait_for(job.events.get(), timeout=5.0)
            except TimeoutError:
                if time.time() - last_ka > 15:
                    yield ": keepalive\n\n"
                    last_ka = time.time()
                continue
            yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
            if evt["event"] == "done":
                return

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"X-Trace-Id": trace_id, "Cache-Control": "no-store"},
    )


# --- /outputs/{stem}/edl ---
@router.get(f"{_PREFIX}/outputs/{{stem}}/edl")
async def outputs_edl(stem: str, request: Request):
    """成果物ディレクトリ配下の timeline.edl を読み出して返す。

    stem は `{outputs_base}/{stem}/timeline.edl` の {stem} 部分。Pi 側 job.output_dir
    の末端 basename がそのまま入ってくる想定で、path traversal を防ぐために
    区切り文字・`..` を禁止する。
    """
    _verify(request)
    trace_id = _trace_id(request)
    gate = _require_enabled(trace_id)
    if gate:
        return gate

    if not stem or "/" in stem or "\\" in stem or stem in (".", ".."):
        return _error_response(400, "ValidationError", "invalid stem", False, trace_id=trace_id)

    try:
        outputs_base = _nas_outputs_base()
    except Exception as e:
        return _error_response(500, "ResourceUnavailableError", f"outputs base unavailable: {e}", True, trace_id=trace_id)

    if not outputs_base or not os.path.isdir(outputs_base):
        return _error_response(500, "ResourceUnavailableError",
            f"outputs base not accessible: {outputs_base}", True, trace_id=trace_id)

    edl_path = os.path.join(outputs_base, stem, "timeline.edl")
    if not os.path.isfile(edl_path):
        return _error_response(404, "ValidationError",
            f"edl not found: {edl_path}", False, trace_id=trace_id)

    try:
        with open(edl_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return _error_response(500, "ClipPipelineError",
            f"failed to read edl: {e}", True, trace_id=trace_id)

    return JSONResponse(
        {"stem": stem, "path": edl_path, "content": content, "size": len(content)},
        headers={"X-Trace-Id": trace_id},
    )


@router.post(f"{_PREFIX}/jobs/{{job_id}}/cancel")
async def jobs_cancel(job_id: str, request: Request):
    _verify(request)
    trace_id = _trace_id(request)
    job = _ctx.jobs.get(job_id)
    if not job:
        return _error_response(404, "ValidationError", "job_id not found", False, trace_id=trace_id)
    if job.status in ("done", "failed", "cancelled"):
        return _error_response(409, "ValidationError", f"already terminal: {job.status}", False, trace_id=trace_id)
    job.cancelled = True
    return JSONResponse({"ok": True, "status": "cancelled"}, headers={"X-Trace-Id": trace_id})
