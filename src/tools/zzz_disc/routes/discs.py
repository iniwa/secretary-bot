"""ZZZ Disc Manager ルータ: masters / characters 参照 / sets / constraints / discs CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import models
from ..schema import (
    RARITY_LEVEL_MAX,
    SLOT_ALLOWED_MAIN_STATS,
    SLOT_FIXED_MAIN_STAT,
    DiscIn,
    DiscPinIn,
)


def build_discs_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database

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

    @router.get("/api/disc-constraints")
    async def get_disc_constraints():
        """手動登録フォーム用: スロット別メインステ候補 + rarity 上限レベル。"""
        return {
            "slot_fixed_main_stat": SLOT_FIXED_MAIN_STAT,
            "slot_allowed_main_stats": {
                k: sorted(v) for k, v in SLOT_ALLOWED_MAIN_STATS.items()
            },
            "rarity_level_max": RARITY_LEVEL_MAX,
        }

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

    @router.put("/api/discs/{disc_id}/pin")
    async def put_disc_pin(disc_id: int, payload: DiscPinIn):
        rowcount = await models.set_disc_pinned(db, disc_id, payload.pinned)
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

    return router
