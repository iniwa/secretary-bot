"""ZZZ Disc Manager ルータ: Teams / Team Groups / Team Slots (編成モード)。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import models
from ..schema import (
    TeamGroupIn,
    TeamGroupUpdateIn,
    TeamIn,
    TeamSlotIn,
    TeamUpdateIn,
)


def build_teams_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database

    # ---------------- Teams (編成モード) ----------------

    @router.get("/api/team-groups")
    async def get_team_groups():
        groups = await models.list_team_groups(db)
        standalone = await models.list_teams(db, standalone=True)
        return {"groups": groups, "standalone_teams": standalone}

    @router.post("/api/team-groups")
    async def post_team_group(payload: TeamGroupIn):
        gid = await models.create_team_group(
            db, name=payload.name,
            description=payload.description,
            display_order=payload.display_order,
        )
        return {"group": await models.get_team_group(db, gid)}

    @router.get("/api/team-groups/{group_id}")
    async def get_team_group_one(group_id: int):
        g = await models.get_team_group(db, group_id)
        if not g:
            raise HTTPException(404, "group not found")
        return {"group": g}

    @router.put("/api/team-groups/{group_id}")
    async def put_team_group(group_id: int, payload: TeamGroupUpdateIn):
        rc = await models.update_team_group(
            db, group_id,
            name=payload.name, description=payload.description,
            display_order=payload.display_order,
        )
        if rc == 0:
            g = await models.get_team_group(db, group_id)
            if not g:
                raise HTTPException(404, "group not found")
        return {"group": await models.get_team_group(db, group_id)}

    @router.delete("/api/team-groups/{group_id}")
    async def del_team_group(group_id: int):
        rc = await models.delete_team_group(db, group_id)
        if rc == 0:
            raise HTTPException(404, "group not found")
        return {"deleted": True}

    @router.post("/api/teams")
    async def post_team(payload: TeamIn):
        if payload.group_id is not None:
            g = await db.fetchone(
                "SELECT id FROM zzz_team_groups WHERE id = ?",
                (payload.group_id,),
            )
            if not g:
                raise HTTPException(404, "group not found")
            # 高難易度編成は最大10部隊まで
            count = await db.fetchone(
                "SELECT COUNT(*) AS n FROM zzz_teams WHERE group_id = ?",
                (payload.group_id,),
            )
            if (count or {}).get("n", 0) >= 10:
                raise HTTPException(400, "1 グループあたり最大 10 部隊までです")
        tid = await models.create_team(
            db, name=payload.name, group_id=payload.group_id,
            display_order=payload.display_order,
        )
        return {"team": await models.get_team(db, tid)}

    @router.get("/api/teams/{team_id}")
    async def get_team_one(team_id: int):
        t = await models.get_team(db, team_id)
        if not t:
            raise HTTPException(404, "team not found")
        return {"team": t}

    @router.put("/api/teams/{team_id}")
    async def put_team(team_id: int, payload: TeamUpdateIn):
        rc = await models.update_team(
            db, team_id, name=payload.name,
            display_order=payload.display_order,
        )
        if rc == 0:
            t = await models.get_team(db, team_id)
            if not t:
                raise HTTPException(404, "team not found")
        return {"team": await models.get_team(db, team_id)}

    @router.delete("/api/teams/{team_id}")
    async def del_team(team_id: int):
        rc = await models.delete_team(db, team_id)
        if rc == 0:
            raise HTTPException(404, "team not found")
        return {"deleted": True}

    @router.put("/api/teams/{team_id}/slots/{position}")
    async def put_team_slot(team_id: int, position: int, payload: TeamSlotIn):
        if not 0 <= position <= 2:
            raise HTTPException(400, "position must be 0..2")
        t = await db.fetchone(
            "SELECT id FROM zzz_teams WHERE id = ?", (team_id,),
        )
        if not t:
            raise HTTPException(404, "team not found")
        if payload.character_id is not None:
            ch = await models.get_character(db, payload.character_id)
            if not ch:
                raise HTTPException(404, "character not found")
            # build_id 未指定なら current を自動で使う
            build_id = payload.build_id
            if build_id is None:
                current = await models.get_current_build(db, payload.character_id)
                build_id = current["id"] if current else None
            else:
                b = await models.get_build(db, build_id)
                if not b:
                    raise HTTPException(404, "build not found")
                if b["character_id"] != payload.character_id:
                    raise HTTPException(
                        400, "build does not belong to the character")
            await models.set_team_slot(
                db, team_id, position,
                character_id=payload.character_id, build_id=build_id,
            )
        else:
            await models.set_team_slot(
                db, team_id, position,
                character_id=None, build_id=None,
            )
        return {"team": await models.get_team(db, team_id)}

    return router
