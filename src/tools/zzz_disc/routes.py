"""ZZZ Disc Manager: FastAPI ルータ（ビルド中心モデル）。

- /api/masters: キャラ+セットマスタ
- /api/discs: CRUD + 使われているビルド
- /api/characters/{id}/builds: キャラの全ビルド（current + プリセット）
- /api/builds/*: ビルド編集・プリセット保存・スロット割当
- /api/shared-discs: 複数ビルドで共有されている disc の一覧
- /api/hoyolab/*: アカウント管理 + 同期
- /api/jobs: VLM 抽出キュー
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse

from . import models
from .schema import (
    DiscIn, BuildMetaIn, BuildSavePresetIn, BuildSlotAssignIn,
    HoyolabAccountIn, JobConfirmIn, JobCaptureIn,
)


def build_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database
    images_dir = config.get("images_dir") or "/app/data/zzz_disc_images"
    os.makedirs(images_dir, exist_ok=True)

    def _job_queue():
        return getattr(bot, "_zzz_disc_job_queue", None)

    # ---------------- Masters ----------------

    @router.get("/api/masters")
    async def get_masters():
        characters = await models.list_characters(db)
        sets = await models.list_set_masters(db)
        return {"characters": characters, "sets": sets}

    @router.get("/api/characters")
    async def get_characters():
        chars = await models.list_characters_with_build_stats(db)
        return {"characters": chars}

    @router.get("/api/sets")
    async def get_sets():
        sets = await models.list_set_masters(db)
        return {"sets": sets}

    # ---------------- Discs ----------------

    @router.get("/api/discs")
    async def get_discs(
        slot: int | None = Query(None, ge=1, le=6),
        set_id: int | None = Query(None),
    ):
        discs = await models.list_discs(db, slot=slot, set_id=set_id)
        return {"discs": discs}

    @router.get("/api/discs/{disc_id}")
    async def get_disc(disc_id: int):
        disc = await models.get_disc(db, disc_id)
        if not disc:
            raise HTTPException(404, "disc not found")
        used_by = await models.list_builds_using_disc(db, disc_id)
        return {"disc": disc, "used_by": used_by}

    @router.post("/api/discs")
    async def post_disc(payload: DiscIn):
        disc_id = await models.insert_disc(
            db,
            slot=payload.slot,
            set_id=payload.set_id,
            main_stat_name=payload.main_stat_name,
            main_stat_value=payload.main_stat_value,
            sub_stats=[s.model_dump() for s in payload.sub_stats],
            level=payload.level,
            rarity=payload.rarity,
            hoyolab_disc_id=payload.hoyolab_disc_id,
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
            level=payload.level,
            rarity=payload.rarity,
            source_image_path=payload.source_image_path,
            note=payload.note,
        )
        if rowcount == -1:
            raise HTTPException(409, "fingerprint collides with another disc")
        if rowcount == 0:
            raise HTTPException(404, "disc not found")
        disc = await models.get_disc(db, disc_id)
        return {"disc": disc}

    @router.delete("/api/discs/{disc_id}")
    async def del_disc(disc_id: int):
        used_by = await models.list_builds_using_disc(db, disc_id)
        if used_by:
            raise HTTPException(409, {
                "error": "disc is used by builds",
                "used_by": used_by,
            })
        rowcount = await models.delete_disc(db, disc_id)
        if rowcount == 0:
            raise HTTPException(404, "disc not found")
        return {"deleted": True}

    # ---------------- Builds ----------------

    async def _build_with_slots(build_id: int) -> dict | None:
        build = await models.get_build(db, build_id)
        if not build:
            return None
        slots = await models.get_build_slots(db, build_id)
        # 全 6 スロットを埋める（未割当は空）
        by_slot = {s["slot"]: s for s in slots}
        build["slots"] = [
            by_slot.get(i, {"slot": i, "disc_id": None, "disc": None})
            for i in range(1, 7)
        ]
        return build

    @router.get("/api/characters/{character_id}/builds")
    async def get_character_builds(character_id: int):
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        builds = await models.list_builds_for_character(db, character_id)
        current = None
        presets = []
        for b in builds:
            full = await _build_with_slots(b["id"])
            if not full:
                continue
            if full["is_current"]:
                current = full
            else:
                presets.append(full)
        return {"character": ch, "current": current, "presets": presets,
                "builds": ([current] if current else []) + presets}

    @router.post("/api/builds")
    async def post_build(payload: dict):
        """プリセット複製。body: {source_build_id, name, tag?, rank?, notes?}."""
        source_id = payload.get("source_build_id")
        name = payload.get("name")
        if not source_id or not name:
            raise HTTPException(400, "source_build_id and name are required")
        try:
            new_id = await models.copy_build_as_preset(
                db, int(source_id), name=str(name),
                tag=payload.get("tag"), rank=payload.get("rank"),
                notes=payload.get("notes"),
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
        build = await _build_with_slots(new_id)
        return {"build": build}

    @router.get("/api/discs/{disc_id}/builds")
    async def get_disc_builds(disc_id: int):
        used_by = await models.list_builds_using_disc(db, disc_id)
        return {"builds": used_by, "used_by": used_by}

    @router.get("/api/builds/{build_id}")
    async def get_build_detail(build_id: int):
        build = await _build_with_slots(build_id)
        if not build:
            raise HTTPException(404, "build not found")
        return {"build": build}

    @router.put("/api/builds/{build_id}")
    async def put_build_meta(build_id: int, payload: BuildMetaIn):
        rowcount = await models.update_build_meta(
            db, build_id,
            name=payload.name, tag=payload.tag,
            rank=payload.rank, notes=payload.notes,
        )
        if rowcount == 0:
            raise HTTPException(404, "build not found or no changes")
        build = await _build_with_slots(build_id)
        return {"build": build}

    @router.delete("/api/builds/{build_id}")
    async def del_build(build_id: int):
        try:
            rowcount = await models.delete_build(db, build_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if rowcount == 0:
            raise HTTPException(404, "build not found")
        return {"deleted": True}

    @router.put("/api/builds/{build_id}/slots/{slot}")
    async def put_build_slot(build_id: int, slot: int, payload: BuildSlotAssignIn):
        if not 1 <= slot <= 6:
            raise HTTPException(400, "slot must be 1..6")
        build = await models.get_build(db, build_id)
        if not build:
            raise HTTPException(404, "build not found")
        if payload.disc_id is not None:
            disc = await models.get_disc(db, payload.disc_id)
            if not disc:
                raise HTTPException(404, "disc not found")
            if disc["slot"] != slot:
                raise HTTPException(400, f"disc.slot={disc['slot']} does not match slot={slot}")
        await models.set_build_slot(db, build_id, slot, payload.disc_id)
        out = await _build_with_slots(build_id)
        return {"build": out}

    @router.post("/api/builds/{build_id}/save-as-preset")
    async def post_save_as_preset(build_id: int, payload: BuildSavePresetIn):
        try:
            new_id = await models.copy_build_as_preset(
                db, build_id,
                name=payload.name, tag=payload.tag,
                rank=payload.rank, notes=payload.notes,
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
        build = await _build_with_slots(new_id)
        return {"build": build}

    # ---------------- Shared discs ----------------

    @router.get("/api/shared-discs")
    async def get_shared_discs():
        shared = await models.find_shared_discs(db)
        return {"shared_discs": shared}

    # ---------------- HoYoLAB ----------------

    @router.get("/api/hoyolab/account")
    async def get_hoyolab_account():
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(404, "no account configured")
        # 自宅 Pi 前提のため cookie は平文で返す
        return {
            "uid": acc["uid"],
            "region": acc["region"],
            "ltuid_v2": acc.get("ltuid_v2"),
            "ltoken_v2": acc.get("ltoken_v2"),
            "nickname": acc.get("nickname"),
            "last_synced_at": acc.get("last_synced_at"),
        }

    @router.put("/api/hoyolab/account")
    async def put_hoyolab_account(payload: HoyolabAccountIn):
        await models.upsert_hoyolab_account(
            db,
            uid=payload.uid, region=payload.region,
            ltuid_v2=payload.ltuid_v2, ltoken_v2=payload.ltoken_v2,
            nickname=payload.nickname,
        )
        return {"ok": True}

    @router.delete("/api/hoyolab/account")
    async def del_hoyolab_account():
        await db.execute("DELETE FROM zzz_hoyolab_accounts")
        return {"deleted": True}

    @router.post("/api/hoyolab/sync")
    async def post_hoyolab_sync():
        try:
            from .hoyolab_client import sync_current_builds
        except ImportError as e:
            raise HTTPException(503, f"hoyolab client unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        try:
            result = await sync_current_builds(db, acc)
        except Exception as e:
            raise HTTPException(502, f"hoyolab sync failed: {e}")
        # frontend は {results: [...]} を期待
        return {
            "ok": True,
            "synced_characters": result.get("synced_characters", 0),
            "synced_discs": result.get("synced_discs", 0),
            "results": result.get("results", []),
            "errors": result.get("errors", []),
        }

    @router.post("/api/hoyolab/sync/{character_id}")
    async def post_hoyolab_sync_one(character_id: int):
        """単一キャラのみ同期（frontend の「キャラ個別 同期」ボタン用）。"""
        try:
            from .hoyolab_client import sync_current_builds
        except ImportError as e:
            raise HTTPException(503, f"hoyolab client unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        try:
            result = await sync_current_builds(
                db, acc,
                filter_hoyolab_id=ch.get("hoyolab_agent_id"),
            )
        except Exception as e:
            raise HTTPException(502, f"hoyolab sync failed: {e}")
        return {"ok": True, **result}

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
                    except asyncio.TimeoutError:
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
