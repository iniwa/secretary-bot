"""LoRA 学習 API: /api/lora/projects/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from src.errors import ValidationError
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
