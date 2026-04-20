"""clip_pipeline runner — pipeline.run_pipeline を別スレッドで動かし、
イベントをメインイベントループ上の asyncio.Queue へ転送する。

image_gen の WorkflowRunner と違い、パイプラインは完全に同期 CPU/GPU
バウンド処理なので、`asyncio.to_thread` + コールバックで `loop.call_soon_threadsafe`
経由で SSE 配信する方式を採る。
"""
from __future__ import annotations

import asyncio
import os
import time
import traceback
from dataclasses import dataclass, field

from .pipeline.pipeline import run_pipeline


@dataclass
class ClipJob:
    job_id: str
    trace_id: str = ""
    status: str = "queued"   # queued | running | done | failed | cancelled
    progress: float = 0.0
    step: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict | None = None
    last_error: dict | None = None
    cancelled: bool = False
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    done_flag: bool = False


async def _emit(job: ClipJob, event: str, data: dict) -> None:
    await job.events.put({"event": event, "data": data})


def _schedule_emit(loop: asyncio.AbstractEventLoop, job: ClipJob, event: str, data: dict) -> None:
    """別スレッドからイベントを送る安全な入口。"""
    try:
        asyncio.run_coroutine_threadsafe(_emit(job, event, data), loop)
    except Exception:
        pass


async def run_clip_job(
    job: ClipJob,
    *,
    video_path: str,
    output_dir: str,
    whisper_model: str,
    ollama_model: str,
    params: dict,
    whisper_download_root: str | None,
    mode: str = "normal",
) -> None:
    """Pipeline 実行本体。エラー時は job.last_error に格納し、終端 `done` イベントも発行。"""
    loop = asyncio.get_running_loop()
    job.status = "running"
    await _emit(job, "status", {"status": "running"})

    os.makedirs(output_dir, exist_ok=True)

    def _log(msg: str):
        _schedule_emit(loop, job, "log", {"message": str(msg)})

    def _progress(frac: float, desc: str = ""):
        # 0.0 - 1.0 を 0-100 の整数 percent に変換
        try:
            pct = max(0, min(100, int(float(frac) * 100)))
        except Exception:
            pct = 0
        job.progress = float(frac)
        _schedule_emit(loop, job, "progress", {
            "percent": pct,
            "desc": desc,
        })

    def _step(name: str):
        job.step = name
        _schedule_emit(loop, job, "step", {"step": name})

    def _cancel_flag() -> bool:
        return job.cancelled

    try:
        result = await asyncio.to_thread(
            run_pipeline,
            video_path=video_path,
            whisper_model=whisper_model,
            ollama_model=ollama_model,
            output_dir=output_dir,
            sleep_sec=params.get("sleep_sec", 2),
            top_n=int(params.get("top_n", 10)),
            min_clip_sec=float(params.get("min_clip_sec", 30)),
            max_clip_sec=float(params.get("max_clip_sec", 180)),
            do_export_clips=bool(params.get("do_export_clips", False)),
            mic_track=params.get("mic_track", 1),
            use_demucs=bool(params.get("use_demucs", True)),
            log=_log,
            progress_callback=_progress,
            step_callback=_step,
            whisper_download_root=whisper_download_root,
            cancel_flag=_cancel_flag,
        )
        if job.cancelled:
            job.status = "cancelled"
            await _emit(job, "status", {"status": "cancelled"})
        else:
            job.status = "done"
            job.progress = 1.0
            job.result = result or {}
            await _emit(job, "result", {
                "highlights_count": len(job.result.get("highlights") or []),
                "edl_path": job.result.get("edl_path") or "",
                "clip_paths": job.result.get("clip_paths") or [],
                "transcript_path": job.result.get("transcript_path") or "",
            })
            await _emit(job, "status", {"status": "done"})
    except Exception as e:
        if job.cancelled:
            job.status = "cancelled"
            await _emit(job, "status", {"status": "cancelled"})
        else:
            job.status = "failed"
            # 例外クラス名 → Agent 側エラー階層にマップ。大雑把に retryable 扱い。
            cls_name = type(e).__name__
            job.last_error = {
                "error_class": _guess_error_class(cls_name, str(e)),
                "message": str(e),
                "retryable": True,
                "traceback": traceback.format_exc()[-2000:],
            }
            await _emit(job, "error", job.last_error)
            await _emit(job, "status", {"status": "failed"})
    finally:
        job.finished_at = time.time()
        job.done_flag = True
        await _emit(job, "done", {})


def _guess_error_class(cls_name: str, msg: str) -> str:
    """Python 例外名 + メッセージから Agent エラー階層のクラス名を推定。"""
    m = (msg or "").lower()
    if cls_name == "FileNotFoundError" or "not found" in m:
        return "ValidationError"
    if "whisper" in m or "ctranslate2" in m:
        return "WhisperError"
    if "ollama" in m or "highlight" in m:
        return "HighlightError"
    if "ffmpeg" in m or "demucs" in m or "librosa" in m:
        return "TranscribeError"
    if "cancel" in m:
        return "ValidationError"
    return "ClipPipelineError"
