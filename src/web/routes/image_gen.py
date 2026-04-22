"""画像生成 / Generation API: /api/image/*, /api/generation/*。"""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from src.units.lora_train.nas_io import ALLOWED_IMAGE_EXTS as _IMG_ALLOWED_EXTS
from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    # --- Image Gen (Phase 1) ---

    def _get_image_gen_unit():
        u = bot.unit_manager.get("image_gen")
        if not u:
            raise HTTPException(503, "image_gen unit not loaded")
        return u

    def _get_nas_mount_point() -> str:
        """NAS マウントポイントを config から取得。"""
        nas_cfg = bot.config.get("units", {}).get("image_gen", {}).get("nas", {}) or {}
        # prompt 指示は mount_point、実 config は base_path。両対応 + 既定値
        return nas_cfg.get("mount_point") or nas_cfg.get("base_path") or "/mnt/ai-image"

    @app.post("/api/image/generate", dependencies=[Depends(ctx.verify)])
    async def image_generate(request: Request):
        unit = _get_image_gen_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        workflow_name = (body.get("workflow_name") or "").strip()
        if not workflow_name:
            raise HTTPException(400, "workflow_name is required")
        positive = body.get("positive")
        negative = body.get("negative")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params must be an object")
        try:
            job_id = await unit.enqueue(
                user_id=ctx.webgui_user_id or "webgui",
                platform="web",
                workflow_name=workflow_name,
                positive=positive,
                negative=negative,
                params=params,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"job_id": job_id}

    @app.get("/api/image/jobs", dependencies=[Depends(ctx.verify)])
    async def image_jobs_list(status: str | None = None, limit: int = 50, offset: int = 0):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        jobs = await unit.list_jobs(
            user_id=None,  # WebGUI はシングルユーザーなので全件。必要なら ctx.webgui_user_id に絞る
            status=status,
            limit=limit,
            offset=offset,
        )
        return {"jobs": jobs}

    @app.get("/api/image/jobs/stream")
    async def image_jobs_stream():
        """ImageGenUnit のイベントを SSE で配信（/api/flow/stream と同じ形）。"""
        unit = _get_image_gen_unit()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        unit.subscribe_events(queue)

        async def event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                unit.unsubscribe_events(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/image/jobs/{job_id}", dependencies=[Depends(ctx.verify)])
    async def image_job_detail(job_id: str):
        unit = _get_image_gen_unit()
        job = await unit.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/image/jobs/{job_id}/cancel", dependencies=[Depends(ctx.verify)])
    async def image_job_cancel(job_id: str):
        unit = _get_image_gen_unit()
        ok = await unit.cancel_job(job_id)
        return {"ok": bool(ok)}

    @app.get("/api/image/gallery", dependencies=[Depends(ctx.verify)])
    async def image_gallery(limit: int = 50, offset: int = 0):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        rows = await unit.list_gallery(limit=limit, offset=offset)
        items: list[dict] = []
        for r in rows:
            for p in r.get("result_paths") or []:
                items.append({
                    "job_id": r.get("job_id"),
                    "path": p,
                    "thumb_url": f"/api/image/file?path={p}",
                    "url": f"/api/image/file?path={p}",
                    "created_at": r.get("finished_at"),
                    "positive": r.get("positive"),
                })
        return {"items": items}

    @app.get("/api/image/workflows", dependencies=[Depends(ctx.verify)])
    async def image_workflows():
        rows = await bot.database.workflow_list()
        out = []
        for r in rows:
            required_nodes = []
            required_loras = []
            try:
                required_nodes = json.loads(r.get("required_nodes") or "[]")
            except (TypeError, ValueError):
                pass
            try:
                required_loras = json.loads(r.get("required_loras") or "[]")
            except (TypeError, ValueError):
                pass
            out.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "description": r.get("description"),
                "category": r.get("category"),
                "main_pc_only": bool(r.get("main_pc_only")),
                "starred": bool(r.get("starred")),
                "default_timeout_sec": r.get("default_timeout_sec"),
                "required_nodes": required_nodes,
                "required_loras": required_loras,
            })
        return {"workflows": out}

    # --- /api/generation/* ( Phase 3.5c 並立 + セクション合成 ) ---

    @app.post("/api/generation/submit", dependencies=[Depends(ctx.verify)])
    async def generation_submit(request: Request):
        unit = _get_image_gen_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        workflow_name = (body.get("workflow_name") or "").strip()
        if not workflow_name:
            raise HTTPException(400, "workflow_name is required")
        positive = body.get("positive")
        negative = body.get("negative")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params must be an object")
        section_ids = body.get("section_ids") or []
        if not isinstance(section_ids, list):
            raise HTTPException(400, "section_ids must be an array")
        try:
            section_ids = [int(v) for v in section_ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "section_ids must be integers")
        user_position = str(body.get("user_position") or "tail")
        modality = body.get("modality")
        lora_overrides = body.get("lora_overrides")
        if lora_overrides is not None and not isinstance(lora_overrides, list):
            raise HTTPException(400, "lora_overrides must be an array")
        is_nsfw = bool(body.get("is_nsfw"))
        try:
            job_id = await unit.enqueue(
                user_id=ctx.webgui_user_id or "webgui",
                platform="web",
                workflow_name=workflow_name,
                positive=positive,
                negative=negative,
                params=params,
                section_ids=section_ids or None,
                user_position=user_position,
                modality=modality,
                lora_overrides=lora_overrides,
                is_nsfw=is_nsfw,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"job_id": job_id}

    @app.get("/api/generation/jobs", dependencies=[Depends(ctx.verify)])
    async def generation_jobs_list(
        status: str | None = None, limit: int = 50, offset: int = 0,
        modality: str = "image",
    ):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        # unit.list_jobs は image 固定。他モダリティは将来 DB 直参照で対応。
        if modality != "image":
            rows = await bot.database.generation_job_list(
                status=status, modality=modality, limit=limit, offset=offset,
            )
            jobs = [await unit._row_to_dict(r) for r in rows]
        else:
            jobs = await unit.list_jobs(
                user_id=None, status=status, limit=limit, offset=offset,
            )
        return {"jobs": jobs}

    @app.get("/api/generation/jobs/stream")
    async def generation_jobs_stream():
        """ImageGenUnit イベントの SSE（/api/image/jobs/stream と同一ソース）。"""
        unit = _get_image_gen_unit()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        unit.subscribe_events(queue)

        async def event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                unit.unsubscribe_events(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/generation/jobs/{job_id}", dependencies=[Depends(ctx.verify)])
    async def generation_job_detail(job_id: str):
        unit = _get_image_gen_unit()
        job = await unit.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/generation/jobs/{job_id}/cancel", dependencies=[Depends(ctx.verify)])
    async def generation_job_cancel(job_id: str):
        unit = _get_image_gen_unit()
        ok = await unit.cancel_job(job_id)
        return {"ok": bool(ok)}

    @app.get("/api/generation/gallery", dependencies=[Depends(ctx.verify)])
    async def generation_gallery(
        limit: int = 50, offset: int = 0,
        favorite: int = 0, tag: str | None = None,
        nsfw: int = 0,
    ):
        unit = _get_image_gen_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        # nsfw=0（既定）→ SFW のみ、nsfw=1 → NSFW のみ
        rows = await unit.list_gallery(
            limit=limit, offset=offset,
            favorite_only=bool(favorite), tag=(tag or None),
            nsfw=bool(nsfw),
        )
        items: list[dict] = []
        for r in rows:
            paths = r.get("result_paths") or []
            kinds = r.get("result_kinds") or []
            if len(kinds) != len(paths):
                kinds = ["image"] * len(paths)
            for p, kind in zip(paths, kinds, strict=False):
                items.append({
                    "job_id": r.get("job_id"),
                    "path": p,
                    "kind": kind,
                    "thumb_url": f"/api/image/file?path={p}",
                    "url": f"/api/image/file?path={p}",
                    "created_at": r.get("finished_at"),
                    "positive": r.get("positive"),
                    "negative": r.get("negative"),
                    "favorite": r.get("favorite", False),
                    "tags": r.get("tags") or [],
                    "is_nsfw": bool(r.get("is_nsfw", False)),
                })
        return {"items": items}

    @app.patch(
        "/api/generation/jobs/{job_id}/favorite",
        dependencies=[Depends(ctx.verify)],
    )
    async def generation_job_favorite(job_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        favorite = bool(body.get("favorite"))
        ok = await bot.database.generation_job_set_favorite(job_id, favorite)
        if not ok:
            raise HTTPException(404, "job not found")
        return {"ok": True, "favorite": favorite}

    @app.patch(
        "/api/generation/jobs/{job_id}/tags",
        dependencies=[Depends(ctx.verify)],
    )
    async def generation_job_tags(job_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        tags = body.get("tags") or []
        if not isinstance(tags, list):
            raise HTTPException(400, "tags must be an array")
        # 文字列に正規化、空除去、重複除去（順序保持）
        seen = set()
        cleaned: list[str] = []
        for t in tags:
            s = str(t).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        tags_json = json.dumps(cleaned, ensure_ascii=False) if cleaned else None
        ok = await bot.database.generation_job_set_tags(job_id, tags_json)
        if not ok:
            raise HTTPException(404, "job not found")
        return {"ok": True, "tags": cleaned}

    @app.get(
        "/api/generation/workflows/{name}/loras",
        dependencies=[Depends(ctx.verify)],
    )
    async def generation_workflow_loras(name: str):
        """workflow に含まれる LoraLoader ノード一覧（UI のセレクタ用）。"""
        unit = _get_image_gen_unit()
        try:
            loras = await unit.workflow_mgr.list_lora_nodes_by_name(name)
        except Exception as e:
            raise HTTPException(500, f"failed to load loras: {e}")
        return {"loras": loras}

    @app.get("/api/generation/checkpoints", dependencies=[Depends(ctx.verify)])
    async def generation_checkpoints():
        """各 Agent がキャッシュ済みの checkpoint を集計して返す。

        model_cache_manifest（ModelSyncUnit が定期同期）を元に、ファイル名の
        union を返す。agents には「その checkpoint を持っている agent_id の一覧」を
        添える。config の default_base_model も同梱して UI 側の初期選択に使う。
        """
        rows = await bot.database.fetchall(
            "SELECT agent_id, filename FROM model_cache_manifest "
            "WHERE file_type = 'checkpoints'"
        )
        by_file: dict[str, list[str]] = {}
        for r in rows:
            fn = r.get("filename") or ""
            aid = r.get("agent_id") or ""
            if not fn:
                continue
            by_file.setdefault(fn, [])
            if aid and aid not in by_file[fn]:
                by_file[fn].append(aid)
        items = [
            {"filename": fn, "agents": sorted(aids)}
            for fn, aids in sorted(by_file.items(), key=lambda kv: kv[0].lower())
        ]
        ig_cfg = (bot.config.get("units") or {}).get("image_gen") or {}
        default_ckpt = ig_cfg.get("default_base_model") or None
        return {"items": items, "default": default_ckpt}

    @app.get("/api/generation/gallery/tags", dependencies=[Depends(ctx.verify)])
    async def generation_gallery_tags():
        """ギャラリーで使われているタグ一覧（出現回数つき）。"""
        rows = await bot.database.fetchall(
            "SELECT tags FROM generation_jobs "
            "WHERE modality = 'image' AND status = 'done' "
            "  AND tags IS NOT NULL AND tags != ''"
        )
        counts: dict[str, int] = {}
        for r in rows:
            try:
                tags = json.loads(r.get("tags") or "[]") or []
            except Exception:
                continue
            for t in tags:
                s = str(t).strip()
                if not s:
                    continue
                counts[s] = counts.get(s, 0) + 1
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return {"tags": [{"tag": k, "count": v} for k, v in items]}

    # --- section categories ---

    @app.get("/api/generation/section-categories", dependencies=[Depends(ctx.verify)])
    async def section_categories_list():
        rows = await bot.database.section_category_list()
        return {"categories": [
            {
                "key": r["key"],
                "label": r["label"],
                "description": r.get("description"),
                "display_order": int(r.get("display_order") or 500),
                "is_builtin": bool(r.get("is_builtin")),
            }
            for r in rows
        ]}

    @app.post("/api/generation/section-categories", dependencies=[Depends(ctx.verify)])
    async def section_category_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        key = (body.get("key") or "").strip()
        label = (body.get("label") or "").strip()
        if not key or not label:
            raise HTTPException(400, "key and label are required")
        if await bot.database.section_category_get(key):
            raise HTTPException(409, "category key already exists")
        cid = await bot.database.section_category_insert(
            key=key, label=label,
            description=body.get("description"),
            display_order=int(body.get("display_order") or 500),
        )
        return {"id": cid, "key": key}

    @app.patch(
        "/api/generation/section-categories/{key}",
        dependencies=[Depends(ctx.verify)],
    )
    async def section_category_update(key: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        ok = await bot.database.section_category_update(
            key,
            label=body.get("label"),
            description=body.get("description"),
            display_order=body.get("display_order"),
        )
        if not ok:
            raise HTTPException(404, "category not found or no changes")
        return {"ok": True}

    @app.delete(
        "/api/generation/section-categories/{key}",
        dependencies=[Depends(ctx.verify)],
    )
    async def section_category_delete(key: str):
        row = await bot.database.section_category_get(key)
        if not row:
            raise HTTPException(404, "category not found")
        if row.get("is_builtin"):
            raise HTTPException(400, "builtin category cannot be deleted")
        ok = await bot.database.section_category_delete(key)
        return {"ok": bool(ok)}

    # --- sections (prompt fragments) ---

    def _section_to_dict(r: dict) -> dict:
        return {
            "id": int(r["id"]),
            "category_key": r.get("category_key"),
            "name": r.get("name"),
            "description": r.get("description"),
            "positive": r.get("positive"),
            "negative": r.get("negative"),
            "tags": r.get("tags"),
            "is_builtin": bool(r.get("is_builtin")),
            "starred": bool(r.get("starred")),
            "updated_at": r.get("updated_at"),
        }

    @app.get("/api/generation/sections", dependencies=[Depends(ctx.verify)])
    async def sections_list(
        category_key: str | None = None, starred_only: bool = False,
    ):
        rows = await bot.database.section_list(
            category_key=category_key, starred_only=bool(starred_only),
        )
        return {"sections": [_section_to_dict(r) for r in rows]}

    @app.get("/api/generation/sections/{section_id}", dependencies=[Depends(ctx.verify)])
    async def section_detail(section_id: int):
        r = await bot.database.section_get(int(section_id))
        if not r:
            raise HTTPException(404, "section not found")
        return _section_to_dict(r)

    @app.post("/api/generation/sections", dependencies=[Depends(ctx.verify)])
    async def section_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        category_key = (body.get("category_key") or "").strip()
        name = (body.get("name") or "").strip()
        if not category_key or not name:
            raise HTTPException(400, "category_key and name are required")
        if not await bot.database.section_category_get(category_key):
            raise HTTPException(400, "unknown category_key")
        sid = await bot.database.section_insert(
            category_key=category_key, name=name,
            positive=body.get("positive"), negative=body.get("negative"),
            description=body.get("description"), tags=body.get("tags"),
            starred=int(bool(body.get("starred"))),
        )
        return {"id": sid}

    @app.patch("/api/generation/sections/{section_id}", dependencies=[Depends(ctx.verify)])
    async def section_update(section_id: int, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        fields: dict = {}
        for k in ("name", "description", "positive", "negative", "tags", "category_key"):
            if k in body:
                fields[k] = body[k]
        if "starred" in body:
            fields["starred"] = int(bool(body["starred"]))
        if not fields:
            raise HTTPException(400, "no fields to update")
        ok = await bot.database.section_update(int(section_id), **fields)
        if not ok:
            raise HTTPException(404, "section not found")
        return {"ok": True}

    @app.delete("/api/generation/sections/{section_id}", dependencies=[Depends(ctx.verify)])
    async def section_delete(section_id: int):
        row = await bot.database.section_get(int(section_id))
        if not row:
            raise HTTPException(404, "section not found")
        if row.get("is_builtin"):
            raise HTTPException(400, "builtin section cannot be deleted")
        ok = await bot.database.section_delete(int(section_id))
        return {"ok": bool(ok)}

    # --- section presets (selected sections + user prompts snapshot) ---

    def _section_preset_to_dict(r: dict) -> dict:
        try:
            payload = json.loads(r.get("payload_json") or "{}")
        except Exception:
            payload = {}
        return {
            "id": int(r["id"]),
            "name": r.get("name"),
            "description": r.get("description"),
            "payload": payload,
            "updated_at": r.get("updated_at"),
        }

    def _validate_section_preset_payload(payload) -> dict:
        if not isinstance(payload, dict):
            raise HTTPException(400, "payload must be an object")
        sids = payload.get("section_ids") or []
        if not isinstance(sids, list):
            raise HTTPException(400, "section_ids must be an array")
        try:
            sids = [int(v) for v in sids]
        except (TypeError, ValueError):
            raise HTTPException(400, "section_ids must be integers")
        pos = str(payload.get("user_position") or "tail")
        return {
            "section_ids": sids,
            "user_positive": str(payload.get("user_positive") or ""),
            "user_negative": str(payload.get("user_negative") or ""),
            "user_position": pos,
        }

    @app.get("/api/generation/section-presets", dependencies=[Depends(ctx.verify)])
    async def section_presets_list():
        rows = await bot.database.section_preset_list()
        return {"presets": [_section_preset_to_dict(r) for r in rows]}

    @app.post("/api/generation/section-presets", dependencies=[Depends(ctx.verify)])
    async def section_preset_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        name = (body.get("name") or "").strip()
        if not name or len(name) > 64:
            raise HTTPException(400, "name is required (1-64 chars)")
        if await bot.database.section_preset_get_by_name(name):
            raise HTTPException(409, "name already exists")
        normalized = _validate_section_preset_payload(body.get("payload"))
        pid = await bot.database.section_preset_insert(
            name=name,
            description=(body.get("description") or None),
            payload_json=json.dumps(normalized, ensure_ascii=False),
        )
        return {"id": pid}

    @app.patch(
        "/api/generation/section-presets/{preset_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def section_preset_update(preset_id: int, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        existing = await bot.database.section_preset_get(int(preset_id))
        if not existing:
            raise HTTPException(404, "preset not found")
        kwargs: dict = {}
        if "name" in body:
            new_name = (body.get("name") or "").strip()
            if not new_name or len(new_name) > 64:
                raise HTTPException(400, "name must be 1-64 chars")
            if new_name != existing["name"]:
                if await bot.database.section_preset_get_by_name(new_name):
                    raise HTTPException(409, "name already exists")
            kwargs["name"] = new_name
        if "description" in body:
            kwargs["description"] = body.get("description") or None
        if "payload" in body:
            normalized = _validate_section_preset_payload(body.get("payload"))
            kwargs["payload_json"] = json.dumps(normalized, ensure_ascii=False)
        if not kwargs:
            raise HTTPException(400, "no fields to update")
        ok = await bot.database.section_preset_update(int(preset_id), **kwargs)
        if not ok:
            raise HTTPException(500, "update failed")
        return {"ok": True}

    @app.delete(
        "/api/generation/section-presets/{preset_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def section_preset_delete(preset_id: int):
        ok = await bot.database.section_preset_delete(int(preset_id))
        if not ok:
            raise HTTPException(404, "preset not found")
        return {"ok": True}

    @app.post("/api/generation/compose-preview", dependencies=[Depends(ctx.verify)])
    async def section_compose_preview(request: Request):
        """クライアントのプレビューと同じロジックをサーバで走らせる検証用。"""
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        section_ids = body.get("section_ids") or []
        if not isinstance(section_ids, list):
            raise HTTPException(400, "section_ids must be an array")
        try:
            section_ids = [int(v) for v in section_ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "section_ids must be integers")
        user_positive = body.get("positive")
        user_negative = body.get("negative")
        user_position = str(body.get("user_position") or "tail")
        rows = await bot.database.section_get_many(section_ids)
        from src.units.image_gen.section_composer import compose_prompt
        result = compose_prompt(
            rows,
            user_positive=user_positive,
            user_negative=user_negative,
            user_position=user_position,
        )
        return {
            "positive": result.positive,
            "negative": result.negative,
            "warnings": list(result.warnings),
            "dropped": list(result.dropped),
        }

    # --- Wildcard / Dynamic Prompts ---
    #
    # name は wildcard_files.name（主キー）。`__name__` 記法で参照される。
    # クライアントは list → 必要なファイルだけキャッシュし、投入ループ内で
    # wildcard_expander を呼び出す。サーバの /expand はプレビュー・Discord 経由
    # 入力等、クライアント展開が通らない経路のための保険。

    _wildcard_name_re = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
    _WILDCARD_MAX_BYTES = 200_000   # 1 ファイル上限 ≒ 200 KB

    @app.get("/api/generation/wildcards", dependencies=[Depends(ctx.verify)])
    async def wildcards_list():
        rows = await bot.database.wildcard_file_list()
        return {"files": rows}

    @app.get("/api/generation/wildcards/bulk", dependencies=[Depends(ctx.verify)])
    async def wildcards_bulk():
        """クライアント展開用に全ファイル (name → content) をまとめて返す。"""
        rows = await bot.database.wildcard_file_get_all()
        return {"files": {r["name"]: r["content"] for r in rows}}

    @app.post("/api/generation/wildcards/expand", dependencies=[Depends(ctx.verify)])
    async def wildcards_expand(request: Request):
        """テンプレートをサーバ側で 1 回展開する。プレビュー・検証用。"""
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        template = body.get("template")
        if not isinstance(template, str):
            raise HTTPException(400, "template must be a string")
        rng_seed = body.get("rng_seed")
        if rng_seed is not None:
            if isinstance(rng_seed, bool) or not isinstance(rng_seed, int):
                raise HTTPException(400, "rng_seed must be an integer")
        rows = await bot.database.wildcard_file_get_all()
        files = {r["name"]: r["content"] for r in rows}
        from src.units.image_gen.wildcard_expander import expand
        result = expand(template, files=files, rng_seed=rng_seed)
        return {
            "text": result.text,
            "choices": [
                {"token": c.token, "kind": c.kind, "picked": c.picked, "source": c.source}
                for c in result.choices
            ],
            "warnings": list(result.warnings),
        }

    @app.get("/api/generation/wildcards/{name}", dependencies=[Depends(ctx.verify)])
    async def wildcard_get(name: str):
        if not _wildcard_name_re.match(name):
            raise HTTPException(400, "invalid name")
        row = await bot.database.wildcard_file_get(name)
        if not row:
            raise HTTPException(404, "wildcard not found")
        return row

    @app.put("/api/generation/wildcards/{name}", dependencies=[Depends(ctx.verify)])
    async def wildcard_put(name: str, request: Request):
        if not _wildcard_name_re.match(name):
            raise HTTPException(400, "name must match [A-Za-z0-9_.-]{1,64}")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        content = body.get("content")
        if not isinstance(content, str):
            raise HTTPException(400, "content must be a string")
        if len(content.encode("utf-8")) > _WILDCARD_MAX_BYTES:
            raise HTTPException(400, f"content too large (max {_WILDCARD_MAX_BYTES // 1024} KB)")
        description = body.get("description")
        if description is not None and not isinstance(description, str):
            raise HTTPException(400, "description must be a string or null")
        await bot.database.wildcard_file_put(
            name=name, content=content, description=description,
        )
        return {"ok": True}

    @app.delete("/api/generation/wildcards/{name}", dependencies=[Depends(ctx.verify)])
    async def wildcard_delete(name: str):
        if not _wildcard_name_re.match(name):
            raise HTTPException(400, "invalid name")
        ok = await bot.database.wildcard_file_delete(name)
        if not ok:
            raise HTTPException(404, "wildcard not found")
        return {"ok": True}

    # --- prompt_crafter セッション API ---

    def _get_prompt_crafter_unit():
        u = bot.unit_manager.get("prompt_crafter")
        if not u:
            raise HTTPException(503, "prompt_crafter unit not loaded")
        return u

    def _prompt_session_to_dict(row: dict) -> dict:
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "platform": row.get("platform"),
            "positive": row.get("positive") or "",
            "negative": row.get("negative") or "",
            "base_workflow_id": row.get("base_workflow_id"),
            "updated_at": row.get("updated_at"),
            "expires_at": row.get("expires_at"),
        }

    @app.get("/api/image/prompts", dependencies=[Depends(ctx.verify)])
    async def prompts_list(limit: int = 20):
        limit = max(1, min(100, int(limit)))
        user_id = ctx.webgui_user_id or "webgui"
        rows = await bot.database.prompt_session_list(user_id=user_id, limit=limit)
        return {"sessions": [_prompt_session_to_dict(r) for r in rows]}

    @app.get("/api/image/prompts/active", dependencies=[Depends(ctx.verify)])
    async def prompts_active():
        user_id = ctx.webgui_user_id or "webgui"
        unit = _get_prompt_crafter_unit()
        sess = await unit.get_active_prompt(user_id, "web")
        return {"session": sess}

    @app.post("/api/image/prompts/craft", dependencies=[Depends(ctx.verify)])
    async def prompts_craft(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        instruction = (body.get("instruction") or "").strip()
        if not instruction:
            raise HTTPException(400, "instruction is required")
        base_workflow_id = body.get("base_workflow_id")
        if base_workflow_id is not None:
            try:
                base_workflow_id = int(base_workflow_id)
            except (TypeError, ValueError):
                raise HTTPException(400, "base_workflow_id must be integer")
        user_id = ctx.webgui_user_id or "webgui"
        unit = _get_prompt_crafter_unit()
        try:
            result = await unit.craft(
                user_id=user_id, platform="web",
                instruction=instruction,
                base_workflow_id=base_workflow_id,
            )
        except Exception as e:
            raise HTTPException(500, f"craft failed: {e}")
        return result

    @app.delete("/api/image/prompts/active", dependencies=[Depends(ctx.verify)])
    async def prompts_clear_active():
        user_id = ctx.webgui_user_id or "webgui"
        unit = _get_prompt_crafter_unit()
        ok = await unit.clear_active(user_id, "web")
        return {"ok": bool(ok)}

    @app.delete("/api/image/prompts/{session_id}", dependencies=[Depends(ctx.verify)])
    async def prompts_delete(session_id: int):
        await bot.database.prompt_session_delete(int(session_id))
        return {"ok": True}

    @app.get("/api/image/agents", dependencies=[Depends(ctx.verify)])
    async def image_agents():
        """ComfyUI へのリンク用に agent_pool の host 情報を返す。"""
        ig_cfg = (bot.config.get("units") or {}).get("image_gen") or {}
        comfy_port = int(ig_cfg.get("comfyui_port", 8188))
        agents = getattr(bot.unit_manager.agent_pool, "_agents", []) or []
        out = []
        for a in agents:
            host = a.get("host") or ""
            if not host:
                continue
            public_url = (a.get("comfyui_public_url") or "").strip()
            url = public_url if public_url else f"http://{host}:{comfy_port}/"
            out.append({
                "id": a.get("id", ""),
                "name": a.get("name") or a.get("id", ""),
                "role": a.get("role", ""),
                "comfyui_url": url,
            })
        return {"agents": out}

    def _find_agent(agent_id: str) -> dict | None:
        for a in (getattr(bot.unit_manager.agent_pool, "_agents", []) or []):
            if a.get("id") == agent_id:
                return a
        return None

    async def _comfyui_proxy(agent_id: str, method: str, path: str, timeout: float):
        agent = _find_agent(agent_id)
        if not agent:
            raise HTTPException(404, f"agent not found: {agent_id}")
        url = f"http://{agent['host']}:{agent.get('port', 7777)}{path}"
        token = os.environ.get("AGENT_SECRET_TOKEN", "")
        headers = {"X-Agent-Token": token} if token else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, headers=headers)
            return JSONResponse(
                content=resp.json() if resp.content else {},
                status_code=resp.status_code,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"agent unreachable: {e}")

    @app.get("/api/image/agents/{agent_id}/comfyui/status", dependencies=[Depends(ctx.verify)])
    async def image_comfyui_status(agent_id: str):
        return await _comfyui_proxy(agent_id, "GET", "/comfyui/status", timeout=5.0)

    @app.post("/api/image/agents/{agent_id}/comfyui/start", dependencies=[Depends(ctx.verify)])
    async def image_comfyui_start(agent_id: str):
        return await _comfyui_proxy(agent_id, "POST", "/comfyui/start", timeout=120.0)

    @app.post("/api/image/agents/{agent_id}/comfyui/stop", dependencies=[Depends(ctx.verify)])
    async def image_comfyui_stop(agent_id: str):
        return await _comfyui_proxy(agent_id, "POST", "/comfyui/stop", timeout=30.0)

    @app.get("/api/image/agents/{agent_id}/comfyui/history", dependencies=[Depends(ctx.verify)])
    async def image_comfyui_history(agent_id: str, limit: int = 20):
        limit = max(1, min(100, int(limit)))
        return await _comfyui_proxy(
            agent_id, "GET", f"/comfyui/history?limit={limit}", timeout=15.0,
        )

    # --- Workflows CRUD (プリセット管理) ---

    _WORKFLOW_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

    def _workflow_row_to_dict(row: dict, include_json: bool = False) -> dict:
        out = {
            "id": row.get("id"),
            "name": row.get("name"),
            "description": row.get("description") or "",
            "category": row.get("category") or "",
            "main_pc_only": bool(row.get("main_pc_only")),
            "starred": bool(row.get("starred")),
            "default_timeout_sec": int(row.get("default_timeout_sec") or 300),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for k in ("required_nodes", "required_models", "required_loras"):
            raw = row.get(k) or "[]"
            try:
                out[k] = json.loads(raw)
            except Exception:
                out[k] = []
        if include_json:
            try:
                out["workflow_json"] = json.loads(row.get("workflow_json") or "{}")
            except Exception:
                out["workflow_json"] = {}
        return out

    @app.get("/api/image/workflows", dependencies=[Depends(ctx.verify)])
    async def image_workflows_list(category: str | None = None):
        rows = await bot.database.workflow_list(category=category)
        return {"workflows": [_workflow_row_to_dict(r) for r in rows]}

    @app.get("/api/image/workflows/{workflow_id}", dependencies=[Depends(ctx.verify)])
    async def image_workflows_get(workflow_id: int):
        row = await bot.database.workflow_get(int(workflow_id))
        if not row:
            raise HTTPException(404, "workflow not found")
        return _workflow_row_to_dict(row, include_json=True)

    @app.post("/api/image/workflows", dependencies=[Depends(ctx.verify)])
    async def image_workflows_upsert(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        name = (body.get("name") or "").strip()
        if not _WORKFLOW_NAME_RE.match(name):
            raise HTTPException(400, "name must match [a-zA-Z0-9_-]{1,64}")
        workflow_json = body.get("workflow_json")
        if not isinstance(workflow_json, dict) or not workflow_json:
            raise HTTPException(400, "workflow_json must be a non-empty object")
        unit = _get_image_gen_unit()
        try:
            wid = await unit.workflow_mgr.register_workflow(
                name=name,
                workflow_json=workflow_json,
                description=(body.get("description") or "") or None,
                category=(body.get("category") or "t2i"),
                main_pc_only=bool(body.get("main_pc_only", False)),
                default_timeout_sec=int(body.get("default_timeout_sec") or 300),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"register failed: {e}")
        return {"ok": True, "id": wid, "name": name}

    @app.delete("/api/image/workflows/{workflow_id}", dependencies=[Depends(ctx.verify)])
    async def image_workflows_delete(workflow_id: int):
        row = await bot.database.workflow_get(int(workflow_id))
        if not row:
            raise HTTPException(404, "workflow not found")
        await bot.database.execute(
            "DELETE FROM workflows WHERE id = ?", (int(workflow_id),),
        )
        try:
            _get_image_gen_unit().workflow_mgr.invalidate_cache(row.get("name"))
        except Exception:
            pass
        return {"ok": True}

    @app.get("/api/image/file", dependencies=[Depends(ctx.verify)])
    async def image_file(path: str):
        """NAS 配下の画像ファイルを配信（path traversal ガード付き）。"""
        from pathlib import Path

        from fastapi.responses import FileResponse

        if not path:
            raise HTTPException(400, "path is required")

        mount_point = _get_nas_mount_point()
        try:
            mount_real = Path(mount_point).resolve()
        except Exception:
            raise HTTPException(500, "invalid nas mount_point")

        # 入力パス解釈: 絶対パス（mount_point 配下）または mount_point 相対
        raw = Path(path)
        target = raw if raw.is_absolute() else (mount_real / raw)
        try:
            real = target.resolve(strict=False)
        except Exception:
            raise HTTPException(400, "invalid path")

        # mount_point 配下チェック
        try:
            real.relative_to(mount_real)
        except ValueError:
            raise HTTPException(403, "path outside nas mount")

        # outputs/ 配下のみ許可
        outputs_subdir = (
            bot.config.get("units", {}).get("image_gen", {})
            .get("nas", {}).get("outputs_subdir", "outputs")
        )
        outputs_real = (mount_real / outputs_subdir).resolve()
        try:
            real.relative_to(outputs_real)
        except ValueError:
            raise HTTPException(403, "only outputs/ is allowed")

        if not real.is_file():
            raise HTTPException(404, "file not found")

        ext = real.suffix.lower()
        if ext not in _IMG_ALLOWED_EXTS:
            raise HTTPException(415, f"unsupported extension: {ext}")

        media_map = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp",
        }
        return FileResponse(
            str(real), media_type=media_map.get(ext, "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=300"},
        )
