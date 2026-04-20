"""LoRA 学習 API: /api/lora/projects/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from src.errors import AgentCommunicationError, ValidationError
from src.units.lora_train.agent_client import LoRATagAgentClient
from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    def _get_unit():
        u = bot.unit_manager.get("lora_train")
        if not u:
            raise HTTPException(503, "lora_train unit not loaded")
        return u

    @app.get("/api/lora/projects", dependencies=[Depends(ctx.verify)])
    async def lora_project_list(status: str | None = None):
        unit = _get_unit()
        items = await unit.list_projects(status=status)
        return {"items": items}

    @app.get("/api/lora/projects/{project_id}", dependencies=[Depends(ctx.verify)])
    async def lora_project_get(project_id: int):
        unit = _get_unit()
        row = await unit.get_project(project_id)
        if not row:
            raise HTTPException(404, "project not found")
        return row

    @app.post("/api/lora/projects", dependencies=[Depends(ctx.verify)])
    async def lora_project_create(request: Request):
        unit = _get_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        try:
            row = await unit.create_project(
                name=body.get("name") or "",
                description=body.get("description"),
                base_model=body.get("base_model"),
            )
        except ValidationError as e:
            raise HTTPException(400, str(e))
        return row

    @app.patch("/api/lora/projects/{project_id}", dependencies=[Depends(ctx.verify)])
    async def lora_project_update(project_id: int, request: Request):
        unit = _get_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        try:
            row = await unit.update_project(
                project_id,
                description=body.get("description"),
                base_model=body.get("base_model"),
                status=body.get("status"),
            )
        except ValidationError as e:
            raise HTTPException(400, str(e))
        return row

    @app.delete("/api/lora/projects/{project_id}", dependencies=[Depends(ctx.verify)])
    async def lora_project_delete(project_id: int, purge_files: bool = True):
        unit = _get_unit()
        try:
            await unit.delete_project(project_id, purge_files=purge_files)
        except ValidationError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @app.get(
        "/api/lora/projects/{project_id}/dataset",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_list(project_id: int, reviewed_only: bool = False):
        unit = _get_unit()
        items = await unit.list_dataset_items(
            project_id, reviewed_only=reviewed_only,
        )
        return {"items": items}

    @app.post(
        "/api/lora/projects/{project_id}/dataset",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_upload(
        project_id: int, files: list[UploadFile] = File(...),
    ):
        if not files:
            raise HTTPException(400, "no files")
        unit = _get_unit()
        try:
            _, target_dir = await unit.open_dataset_dir(project_id)
        except ValidationError as e:
            raise HTTPException(404, str(e))
        items: list[dict] = []
        for f in files:
            content = await f.read()
            try:
                row = await unit.add_dataset_item(
                    project_id, target_dir,
                    filename=f.filename, content=content,
                )
            except ValidationError as e:
                raise HTTPException(400, str(e))
            finally:
                del content
            if row:
                items.append(row)
        return {"items": items}

    @app.delete(
        "/api/lora/projects/{project_id}/dataset/{item_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_delete(project_id: int, item_id: int):
        unit = _get_unit()
        try:
            await unit.delete_dataset_item(item_id)
        except ValidationError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @app.patch(
        "/api/lora/projects/{project_id}/dataset/{item_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_update(
        project_id: int, item_id: int, request: Request,
    ):
        unit = _get_unit()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        row = await unit.get_dataset_item(item_id)
        if not row or row["project_id"] != project_id:
            raise HTTPException(404, "item not found")
        caption = body.get("caption")
        tags = body.get("tags")
        if caption is not None:
            caption = str(caption)
        if tags is not None:
            tags = str(tags)
        try:
            updated = await unit.update_dataset_item(
                item_id,
                caption=caption,
                tags=tags,
                mark_reviewed=bool(body.get("mark_reviewed", False)),
                sync_caption_file=bool(body.get("sync_caption_file", True)),
            )
        except ValidationError as e:
            raise HTTPException(400, str(e))
        return updated

    @app.get(
        "/api/lora/projects/{project_id}/dataset/{item_id}/image",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_image(project_id: int, item_id: int):
        unit = _get_unit()
        row = await unit.get_dataset_item(item_id)
        if not row or row["project_id"] != project_id:
            raise HTTPException(404, "item not found")
        path = row["image_path"]
        if not unit.is_dataset_path_safe(path):
            raise HTTPException(403, "forbidden path")
        return FileResponse(path)

    # --- WD14 tagging ---

    @app.post(
        "/api/lora/projects/{project_id}/dataset/tag",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_tag(project_id: int, request: Request):
        unit = _get_unit()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            threshold = float(body.get("threshold") or 0.35)
        except (TypeError, ValueError):
            raise HTTPException(400, "threshold must be a number")
        repo_id = (body.get("repo_id") or "").strip() or None
        prepend_trigger = bool(body.get("prepend_trigger", True))
        agent_id = (body.get("agent_id") or "").strip() or None
        try:
            entry = await unit.start_tagging(
                project_id,
                threshold=threshold,
                repo_id=repo_id,
                prepend_trigger=prepend_trigger,
                agent_id=agent_id,
            )
        except ValidationError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"agent error: {e}")
        return entry

    @app.get(
        "/api/lora/projects/{project_id}/dataset/tag/{task_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_tag_status(project_id: int, task_id: str):
        unit = _get_unit()
        entry = unit.get_tagging_task(task_id)
        if not entry:
            raise HTTPException(404, "task not found")
        if entry["project_id"] != project_id:
            raise HTTPException(404, "task not for this project")
        return entry

    @app.get(
        "/api/lora/projects/{project_id}/dataset/tag",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_dataset_tag_list(project_id: int):
        unit = _get_unit()
        return {"items": unit.list_tagging_tasks(project_id)}

    # --- prepare (TOML + sample_prompts) + sync ---

    @app.post(
        "/api/lora/projects/{project_id}/prepare",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_prepare(project_id: int, request: Request):
        unit = _get_unit()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            produced = await unit.prepare_project(
                project_id,
                dataset_overrides=body.get("dataset_overrides"),
                config_overrides=body.get("config_overrides"),
            )
        except ValidationError as e:
            raise HTTPException(400, str(e))
        return produced

    @app.post(
        "/api/lora/projects/{project_id}/sync",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_sync_start(project_id: int, request: Request):
        unit = _get_unit()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        agent_id = (body.get("agent_id") or "").strip() or None
        try:
            entry = await unit.start_sync(project_id, agent_id=agent_id)
        except ValidationError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"agent error: {e}")
        return entry

    @app.get(
        "/api/lora/projects/{project_id}/sync/{task_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_sync_task_get(project_id: int, task_id: str):
        unit = _get_unit()
        entry = unit.get_sync_task(task_id)
        if not entry:
            raise HTTPException(404, "task not found")
        if entry["project_id"] != project_id:
            raise HTTPException(404, "task not for this project")
        return entry

    @app.get(
        "/api/lora/projects/{project_id}/sync",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_sync_task_list(project_id: int):
        unit = _get_unit()
        return {"items": unit.list_sync_tasks(project_id)}

    # --- promotion (Phase H) ---

    @app.get(
        "/api/lora/projects/{project_id}/checkpoints",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_checkpoints_list(project_id: int):
        unit = _get_unit()
        try:
            items = await unit.list_checkpoints(project_id)
        except ValidationError as e:
            raise HTTPException(404, str(e))
        return {"items": items}

    @app.post(
        "/api/lora/projects/{project_id}/promote",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_promote(project_id: int, request: Request):
        unit = _get_unit()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        checkpoint = (body.get("checkpoint_filename") or "").strip()
        if not checkpoint:
            raise HTTPException(400, "checkpoint_filename is required")
        target = (body.get("target_filename") or "").strip() or None
        try:
            result = await unit.promote_checkpoint(
                project_id,
                checkpoint_filename=checkpoint,
                target_filename=target,
            )
        except ValidationError as e:
            raise HTTPException(400, str(e))
        return result

    # --- training (Phase F) ---

    @app.post(
        "/api/lora/projects/{project_id}/train",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_train_start(project_id: int, request: Request):
        unit = _get_unit()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        agent_id = (body.get("agent_id") or "").strip() or None
        try:
            entry = await unit.start_training(project_id, agent_id=agent_id)
        except ValidationError as e:
            raise HTTPException(400, str(e))
        except AgentCommunicationError as e:
            raise HTTPException(502, f"agent error: {e}")
        return entry

    @app.get(
        "/api/lora/projects/{project_id}/train/{task_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_train_task_get(project_id: int, task_id: str):
        unit = _get_unit()
        entry = unit.get_training_task(task_id)
        if not entry:
            raise HTTPException(404, "task not found")
        if entry["project_id"] != project_id:
            raise HTTPException(404, "task not for this project")
        return entry

    @app.get(
        "/api/lora/projects/{project_id}/train",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_train_task_list(project_id: int):
        unit = _get_unit()
        return {"items": unit.list_training_tasks(project_id)}

    @app.post(
        "/api/lora/projects/{project_id}/train/{task_id}/cancel",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_train_task_cancel(project_id: int, task_id: str):
        unit = _get_unit()
        entry = unit.get_training_task(task_id)
        if not entry or entry["project_id"] != project_id:
            raise HTTPException(404, "task not found")
        try:
            resp = await unit.cancel_training(task_id)
        except ValidationError as e:
            raise HTTPException(404, str(e))
        except AgentCommunicationError as e:
            raise HTTPException(502, f"agent error: {e}")
        return resp

    @app.get(
        "/api/lora/projects/{project_id}/train/{task_id}/stream",
        dependencies=[Depends(ctx.verify)],
    )
    async def lora_train_task_stream(
        project_id: int, task_id: str, after_seq: int = 0,
    ):
        unit = _get_unit()
        agent = unit.get_training_agent(task_id)
        entry = unit.get_training_task(task_id)
        if not agent or not entry or entry["project_id"] != project_id:
            raise HTTPException(404, "task not found")

        async def _proxy():
            import json
            client = LoRATagAgentClient(agent)
            try:
                async for event, data in client.train_stream(
                    task_id, after_seq=after_seq,
                ):
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            except AgentCommunicationError as e:
                yield (
                    f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                )
            finally:
                await client.close()

        return StreamingResponse(
            _proxy(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
