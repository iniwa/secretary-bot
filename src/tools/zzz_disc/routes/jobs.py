"""ZZZ Disc Manager ルータ: Jobs（capture / upload / list / stream / image / confirm / delete）。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import models
from ..schema import JobCaptureIn, JobConfirmIn


def build_jobs_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database
    images_dir = config.get("images_dir") or "/app/data/zzz_disc_images"
    os.makedirs(images_dir, exist_ok=True)

    def _job_queue():
        return getattr(bot, "_zzz_disc_job_queue", None)

    # ---------------- Jobs ----------------

    @router.post("/api/jobs/capture")
    async def post_job_capture(payload: JobCaptureIn):
        jq = _job_queue()
        if jq is None:
            raise HTTPException(503, "job queue not ready")
        job_id = await models.create_job(db, source=payload.source)
        await jq.enqueue(job_id)
        job = await models.get_job(db, job_id)
        return {"job": job}

    @router.post("/api/jobs/upload")
    async def post_job_upload(file: UploadFile = File(...)):
        jq = _job_queue()
        if jq is None:
            raise HTTPException(503, "job queue not ready")
        ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
        name = f"upload_{uuid.uuid4().hex}{ext}"
        path = os.path.join(images_dir, name)
        with open(path, "wb") as f:
            f.write(await file.read())
        job_id = await models.create_job(db, source="upload", image_path=path)
        await jq.enqueue(job_id)
        job = await models.get_job(db, job_id)
        return {"job": job}

    @router.get("/api/jobs")
    async def get_jobs(
        status: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ):
        statuses = [s.strip() for s in status.split(",")] if status else None
        jobs = await models.list_jobs(db, statuses=statuses, limit=limit)
        return {"jobs": jobs}

    @router.get("/api/jobs/stream")
    async def jobs_stream(request: Request):
        jq = _job_queue()
        if jq is None:
            raise HTTPException(503, "job queue not ready")
        queue: asyncio.Queue = asyncio.Queue()
        jq.subscribe(queue)

        async def event_gen():
            try:
                active = await models.list_jobs(db,
                    statuses=["queued", "capturing", "extracting", "ready", "failed"],
                    limit=50)
                yield f"data: {json.dumps({'type': 'snapshot', 'jobs': active}, ensure_ascii=False)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                jq.unsubscribe(queue)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @router.get("/api/jobs/{job_id}")
    async def get_job(job_id: int):
        job = await models.get_job(db, job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return {"job": job}

    @router.get("/api/jobs/{job_id}/image")
    async def get_job_image(job_id: int):
        job = await models.get_job(db, job_id)
        if not job:
            raise HTTPException(404, "job not found")
        path = job.get("image_path")
        if not path:
            raise HTTPException(404, "image not captured yet")
        # パストラバーサル対策: images_dir 配下のファイルのみ許可
        abs_path = os.path.abspath(path)
        abs_dir = os.path.abspath(images_dir)
        if not abs_path.startswith(abs_dir + os.sep) and abs_path != abs_dir:
            raise HTTPException(403, "forbidden path")
        if not os.path.exists(abs_path):
            raise HTTPException(404, "image file missing")
        return FileResponse(abs_path, media_type="image/png")

    @router.post("/api/jobs/{job_id}/confirm")
    async def post_job_confirm(job_id: int, payload: JobConfirmIn):
        job = await models.get_job(db, job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if job["status"] != "ready":
            raise HTTPException(400, f"job status must be 'ready', got '{job['status']}'")
        disc_id = await models.insert_disc(
            db,
            slot=payload.disc.slot,
            set_id=payload.disc.set_id,
            main_stat_name=payload.disc.main_stat_name,
            main_stat_value=payload.disc.main_stat_value,
            sub_stats=[s.model_dump() for s in payload.disc.sub_stats],
            level=payload.disc.level,
            rarity=payload.disc.rarity,
            source_image_path=payload.disc.source_image_path or job.get("image_path"),
            note=payload.disc.note,
        )
        await models.update_job(db, job_id, status="saved")
        jq = _job_queue()
        if jq is not None:
            await jq.publish({"type": "update", "job_id": job_id, "status": "saved"})
        disc = await models.get_disc(db, disc_id)
        return {"disc": disc}

    @router.delete("/api/jobs/{job_id}")
    async def del_job(job_id: int):
        rowcount = await models.delete_job(db, job_id)
        if rowcount == 0:
            raise HTTPException(404, "job not found")
        jq = _job_queue()
        if jq is not None:
            await jq.publish({"type": "delete", "job_id": job_id})
        return {"deleted": True}

    return router
