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
from fastapi.responses import FileResponse, StreamingResponse

from . import models
from .schema import (
    DiscIn, BuildMetaIn, BuildSavePresetIn, BuildSlotAssignIn,
    DiscPinIn,
    HoyolabAccountIn, HoyolabCredentialsIn, HoyolabAutoLoginIn,
    JobConfirmIn, JobCaptureIn,
    TeamIn, TeamUpdateIn, TeamSlotIn, TeamGroupIn, TeamGroupUpdateIn,
    CharacterSkillsIn,
    SLOT_FIXED_MAIN_STAT, SLOT_ALLOWED_MAIN_STATS, RARITY_LEVEL_MAX,
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

    # ---------------- HoYoLAB ----------------

    @router.get("/api/hoyolab/account")
    async def get_hoyolab_account():
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(404, "no account configured")
        # 自宅 Pi 前提のため cookie は平文で返す。password は返さない。
        return {
            "uid": acc["uid"],
            "region": acc["region"],
            "ltuid_v2": acc.get("ltuid_v2"),
            "ltoken_v2": acc.get("ltoken_v2"),
            "nickname": acc.get("nickname"),
            "last_synced_at": acc.get("last_synced_at"),
            "email": acc.get("email"),
            "auto_login_enabled": bool(acc.get("auto_login_enabled")),
            "has_password": bool(acc.get("password")),
            "last_auto_login_at": acc.get("last_auto_login_at"),
            "last_auto_login_error": acc.get("last_auto_login_error"),
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

    # ---------------- HoYoLAB 自動ログイン ----------------

    @router.put("/api/hoyolab/credentials")
    async def put_hoyolab_credentials(payload: HoyolabCredentialsIn):
        """既存アカウントに自動ログイン用 email/password を保存。"""
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400,
                "先に uid/region/cookie を登録してください")
        await models.upsert_hoyolab_account(
            db,
            uid=acc["uid"], region=acc["region"],
            ltuid_v2=acc["ltuid_v2"], ltoken_v2=acc["ltoken_v2"],
            email=payload.email, password=payload.password,
            auto_login_enabled=payload.auto_login_enabled,
        )
        return {"ok": True}

    @router.delete("/api/hoyolab/credentials")
    async def del_hoyolab_credentials():
        await db.execute(
            "UPDATE zzz_hoyolab_accounts SET email = NULL, password = NULL, "
            "auto_login_enabled = 0"
        )
        return {"ok": True}

    @router.post("/api/hoyolab/auto-login")
    async def post_hoyolab_auto_login(payload: HoyolabAutoLoginIn):
        """email/password で自動ログインし cookies を取得・保存。

        既存アカウントがある場合は uid/region を流用し cookie を更新。
        無い場合は payload の uid/region で新規作成（どちらも必須）。
        save_credentials=True なら email/password と auto_login_enabled=1 を保存。
        """
        try:
            from .hoyolab_auth import (
                auto_login, HoyolabLoginError, InvalidCredentials, CaptchaRequired,
            )
        except ImportError as e:
            raise HTTPException(503, f"hoyolab auth unavailable: {e}")

        try:
            cookies = await auto_login(payload.email, payload.password)
        except InvalidCredentials as e:
            raise HTTPException(401, f"認証情報が不正です: {e}")
        except CaptchaRequired as e:
            raise HTTPException(409, f"captcha required: {e}")
        except HoyolabLoginError as e:
            raise HTTPException(502, f"ログイン失敗: {e}")

        existing = await models.get_hoyolab_account(db)
        if existing:
            uid = existing["uid"]
            region = existing["region"]
            nickname = payload.nickname or existing.get("nickname")
        else:
            if not payload.uid or not payload.region:
                raise HTTPException(400,
                    "初回登録時は uid と region が必要です")
            uid = payload.uid
            region = payload.region
            nickname = payload.nickname

        await models.upsert_hoyolab_account(
            db,
            uid=uid, region=region,
            ltuid_v2=cookies["ltuid_v2"], ltoken_v2=cookies["ltoken_v2"],
            nickname=nickname,
            email=payload.email if payload.save_credentials else None,
            password=payload.password if payload.save_credentials else None,
            auto_login_enabled=True if payload.save_credentials else None,
            account_mid_v2=cookies.get("account_mid_v2"),
            account_id_v2=cookies.get("account_id_v2"),
            cookie_token_v2=cookies.get("cookie_token_v2"),
            ltmid_v2=cookies.get("ltmid_v2"),
        )
        # last_auto_login_at を刻む
        await models.update_hoyolab_cookies(
            db, uid=uid,
            ltuid_v2=cookies["ltuid_v2"],
            ltoken_v2=cookies["ltoken_v2"],
            error=None,
        )
        return {
            "ok": True,
            "saved_credentials": payload.save_credentials,
            "ltuid_v2": cookies["ltuid_v2"],
        }

    @router.post("/api/hoyolab/refresh")
    async def post_hoyolab_refresh():
        """保存済み email/password で cookies を再取得。"""
        try:
            from .hoyolab_auth import (
                refresh_account_cookies,
                HoyolabLoginError, InvalidCredentials, CaptchaRequired,
            )
        except ImportError as e:
            raise HTTPException(503, f"hoyolab auth unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        if not acc.get("email") or not acc.get("password"):
            raise HTTPException(400, "credentials が保存されていません")
        try:
            cookies = await refresh_account_cookies(db, acc)
        except InvalidCredentials as e:
            raise HTTPException(401, f"認証情報が不正です: {e}")
        except CaptchaRequired as e:
            raise HTTPException(409, f"captcha required: {e}")
        except HoyolabLoginError as e:
            raise HTTPException(502, f"再ログイン失敗: {e}")
        return {"ok": True, "ltuid_v2": cookies["ltuid_v2"]}

    @router.post("/api/hoyolab/reset")
    async def post_hoyolab_reset():
        """HoYoLAB 同期データを一掃（キャラ重複解消・再同期用）。"""
        result = await models.reset_hoyolab_synced_data(db)
        return {"ok": True, **result}

    @router.post("/api/characters/cleanup-empty")
    async def post_characters_cleanup():
        """ビルドが 1 件も無いキャラを削除する（未所持シードの掃除）。"""
        result = await models.delete_characters_without_builds(db)
        return {"ok": True, **result}

    async def _sweep_unpinned() -> int:
        """同期冒頭の掃除: ピン無しディスクを削除（参照スロットは NULL 化）。"""
        return await models.delete_unpinned_discs(db)

    @router.post("/api/hoyolab/sync")
    async def post_hoyolab_sync():
        try:
            from .hoyolab_client import sync_current_builds
        except ImportError as e:
            raise HTTPException(503, f"hoyolab client unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        swept = await _sweep_unpinned()
        try:
            result = await sync_current_builds(db, acc)
        except Exception as e:
            raise HTTPException(502, f"hoyolab sync failed: {e}")
        # frontend は {results: [...]} を期待
        return {
            "ok": True,
            "synced_characters": result.get("synced_characters", 0),
            "synced_discs": result.get("synced_discs", 0),
            "swept_unpinned": swept,
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
