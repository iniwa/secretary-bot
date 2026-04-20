"""ZZZ Disc Manager ルータ: character builds / builds CRUD / スロット / pin-all / save-as-preset / 共有 disc。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import models
from ..schema import (
    BuildMetaIn,
    BuildSavePresetIn,
    BuildSlotAssignIn,
)


def build_builds_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database

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

    @router.post("/api/builds/{build_id}/pin-all")
    async def post_build_pin_all(build_id: int):
        build = await models.get_build(db, build_id)
        if not build:
            raise HTTPException(404, "build not found")
        n = await models.pin_build_discs(db, build_id)
        out = await _build_with_slots(build_id)
        return {"build": out, "pinned": n}

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

    @router.get("/api/disc-usage")
    async def get_disc_usage():
        """disc × ビルドのフラットな対応表（フィルタUI 用）。"""
        rows = await models.list_all_disc_usage(db)
        return {"usage": rows}

    return router
