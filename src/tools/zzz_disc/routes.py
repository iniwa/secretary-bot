"""ZZZ Disc Manager: FastAPI ルータ。

- /api/masters: キャラ+セットマスタ一括
- /api/discs: CRUD + candidates
- /api/presets: 取得 / UPSERT
- /api/conflicts: 候補競合ビュー
- /api/jobs: キュー投入・一覧・SSE・確定保存（job_queue 経由）
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse

from . import models
from . import matcher
from .schema import DiscIn, PresetIn, JobConfirmIn, JobCaptureIn


def build_router(bot, config: dict) -> APIRouter:
    """bot / config を捕捉したルータを返す。"""
    router = APIRouter()
    db = bot.database
    match_threshold = float(config.get("match_threshold", 3.0))
    images_dir = config.get("images_dir") or "/app/data/zzz_disc_images"
    os.makedirs(images_dir, exist_ok=True)

    # job_queue は register() 時に bot に付与される想定（bot._zzz_disc_job_queue）
    def _job_queue():
        return getattr(bot, "_zzz_disc_job_queue", None)

    # ---------------- Masters ----------------

    @router.get("/api/masters")
    async def get_masters():
        characters = await models.list_characters(db)
        sets = await models.list_set_masters(db)
        return {"characters": characters, "sets": sets}

    # ---------------- Discs ----------------

    @router.get("/api/discs")
    async def get_discs(
        slot: int | None = Query(None, ge=1, le=6),
        set_id: int | None = Query(None),
    ):
        discs = await models.list_discs(db, slot=slot, set_id=set_id)
        return {"discs": discs}

    @router.post("/api/discs")
    async def post_disc(payload: DiscIn):
        disc_id = await models.insert_disc(
            db,
            slot=payload.slot,
            set_id=payload.set_id,
            main_stat_name=payload.main_stat_name,
            main_stat_value=payload.main_stat_value,
            sub_stats=[s.model_dump() for s in payload.sub_stats],
            source_image_path=payload.source_image_path,
            note=payload.note,
        )
        disc = await models.get_disc(db, disc_id)
        return {"disc": disc}

    @router.put("/api/discs/{disc_id}")
    async def put_disc(disc_id: int, payload: DiscIn):
        rowcount = await models.update_disc(
            db, disc_id,
            slot=payload.slot,
            set_id=payload.set_id,
            main_stat_name=payload.main_stat_name,
            main_stat_value=payload.main_stat_value,
            sub_stats=[s.model_dump() for s in payload.sub_stats],
            source_image_path=payload.source_image_path,
            note=payload.note,
        )
        if rowcount == 0:
            raise HTTPException(404, "disc not found")
        disc = await models.get_disc(db, disc_id)
        return {"disc": disc}

    @router.delete("/api/discs/{disc_id}")
    async def del_disc(disc_id: int):
        rowcount = await models.delete_disc(db, disc_id)
        if rowcount == 0:
            raise HTTPException(404, "disc not found")
        return {"deleted": True}

    @router.get("/api/discs/{disc_id}/candidates")
    async def get_disc_candidates(disc_id: int, limit: int = Query(10, ge=1, le=50)):
        disc = await models.get_disc(db, disc_id)
        if not disc:
            raise HTTPException(404, "disc not found")
        presets = await models.list_all_presets(db)
        characters = await models.list_characters(db)
        by_id = {c["id"]: c for c in characters}
        candidates = matcher.top_candidates_for_disc(
            disc, presets, by_id,
            threshold=match_threshold, limit=limit,
        )
        return {"disc_id": disc_id, "candidates": candidates}

    # ---------------- Presets ----------------

    @router.get("/api/presets/{character_id}")
    async def get_presets(character_id: int):
        ch = await db.fetchone("SELECT id FROM zzz_characters WHERE id = ?", (character_id,))
        if not ch:
            raise HTTPException(404, "character not found")
        presets = await models.list_presets_for_character(db, character_id)
        return {"character_id": character_id, "presets": presets}

    @router.put("/api/presets/{character_id}/{slot}")
    async def put_preset(character_id: int, slot: int, payload: PresetIn):
        if not 1 <= slot <= 6:
            raise HTTPException(400, "slot must be 1..6")
        ch = await db.fetchone("SELECT id FROM zzz_characters WHERE id = ?", (character_id,))
        if not ch:
            raise HTTPException(404, "character not found")
        await models.upsert_preset(
            db,
            character_id=character_id, slot=slot,
            preferred_set_ids=payload.preferred_set_ids,
            preferred_main_stats=payload.preferred_main_stats,
            sub_stat_priority=payload.sub_stat_priority,
        )
        presets = await models.list_presets_for_character(db, character_id)
        return {"character_id": character_id, "presets": presets}

    # ---------------- Conflicts ----------------

    @router.get("/api/conflicts")
    async def get_conflicts(top_n: int = Query(3, ge=1, le=10)):
        """キャラ×部位マトリックス + 共有ディスク一覧。

        各 (character, slot) 上位 N 件候補 → 同じ disc_id が複数セルに出れば「共有」。
        """
        discs = await models.list_discs(db)
        presets = await models.list_all_presets(db)
        characters = await models.list_characters(db)
        by_id = {c["id"]: c for c in characters}

        # preset を (character_id, slot) → preset にマップ
        preset_by_key: dict[tuple[int, int], dict] = {
            (p["character_id"], p["slot"]): p for p in presets
        }

        # cell = (character_id, slot) → TOP N ディスク候補
        cells: list[dict] = []
        disc_slot_to_cells: dict[int, list[dict]] = {}
        for ch in characters:
            for slot in range(1, 7):
                preset = preset_by_key.get((ch["id"], slot))
                if not preset:
                    continue
                scored = []
                for disc in discs:
                    if disc["slot"] != slot:
                        continue
                    s = matcher.score_disc_against_preset(disc, preset)
                    if s < match_threshold:
                        continue
                    scored.append({"disc_id": disc["id"], "score": round(s, 2)})
                scored.sort(key=lambda x: x["score"], reverse=True)
                top = scored[:top_n]
                cell = {
                    "character_id": ch["id"],
                    "character_slug": ch["slug"],
                    "character_name_ja": ch["name_ja"],
                    "slot": slot,
                    "candidates": top,
                }
                cells.append(cell)
                for c in top:
                    disc_slot_to_cells.setdefault(c["disc_id"], []).append(cell)

        # 共有ディスク（同じ disc_id が2セル以上に出現）
        shared = []
        for disc_id, owners in disc_slot_to_cells.items():
            if len(owners) < 2:
                continue
            shared.append({
                "disc_id": disc_id,
                "claimed_by": [
                    {
                        "character_id": o["character_id"],
                        "character_slug": o["character_slug"],
                        "character_name_ja": o["character_name_ja"],
                        "slot": o["slot"],
                    } for o in owners
                ],
            })
        shared.sort(key=lambda s: len(s["claimed_by"]), reverse=True)

        return {"cells": cells, "shared_discs": shared}

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
        status: str | None = Query(None,
            description="カンマ区切りで複数指定可: queued,extracting,ready,failed"),
        limit: int = Query(100, ge=1, le=500),
    ):
        statuses = [s.strip() for s in status.split(",")] if status else None
        jobs = await models.list_jobs(db, statuses=statuses, limit=limit)
        return {"jobs": jobs}

    @router.get("/api/jobs/stream")
    async def jobs_stream(request: Request):
        """ジョブ状態の変化を SSE で通知。
        job_queue の Pub/Sub に登録して、接続中は更新を push。
        """
        jq = _job_queue()
        if jq is None:
            raise HTTPException(503, "job queue not ready")

        queue: asyncio.Queue = asyncio.Queue()
        jq.subscribe(queue)

        async def event_gen():
            try:
                # 初回: 現在のアクティブジョブを送る
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
                    except asyncio.TimeoutError:
                        # keepalive
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
