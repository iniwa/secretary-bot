"""ZZZ Disc Manager ルータ: キャラクター編集系（推奨サブステ・スキル・推奨セット・メモ）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import models
from ..schema import CharacterSkillsIn


def build_characters_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database

    @router.put("/api/characters/{character_id}/recommended-substats")
    async def put_recommended_substats(character_id: int, payload: dict):
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        stats = payload.get("stats") or []
        if not isinstance(stats, list) or not all(isinstance(s, str) for s in stats):
            raise HTTPException(400, "stats must be list[str]")
        await models.update_character_recommended_substats(db, character_id, stats)
        ch = await models.get_character(db, character_id)
        return {"character": ch}

    @router.put("/api/characters/{character_id}/skills")
    async def put_character_skills(character_id: int, payload: CharacterSkillsIn):
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        await models.update_character_skills(
            db, character_id,
            skills=[s.model_dump() for s in payload.skills],
            summary=payload.summary,
        )
        ch = await models.get_character(db, character_id)
        return {"character": ch}

    @router.put("/api/characters/{character_id}/recommended-disc-sets")
    async def put_recommended_disc_sets(character_id: int, payload: dict):
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        sets = payload.get("sets") or []
        if not isinstance(sets, list) or not all(isinstance(s, str) for s in sets):
            raise HTTPException(400, "sets must be list[str]")
        await models.update_character_recommended_disc_sets(db, character_id, sets)
        ch = await models.get_character(db, character_id)
        return {"character": ch}

    @router.put("/api/characters/{character_id}/recommended-notes")
    async def put_recommended_notes(character_id: int, payload: dict):
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise HTTPException(400, "notes must be string or null")
        await models.update_character_recommended_notes(
            db, character_id, (notes or None))
        ch = await models.get_character(db, character_id)
        return {"character": ch}

    @router.put("/api/characters/{character_id}/recommended-team-notes")
    async def put_recommended_team_notes(character_id: int, payload: dict):
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise HTTPException(400, "notes must be string or null")
        await models.update_character_recommended_team_notes(
            db, character_id, (notes or None))
        ch = await models.get_character(db, character_id)
        return {"character": ch}

    return router
