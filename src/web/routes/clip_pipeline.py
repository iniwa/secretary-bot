"""Auto-Kirinuki / Clip Pipeline API: /api/clip-pipeline/*。

Pi 側 ClipPipelineUnit を WebGUI から操作するための薄い HTTP 層。
image_gen のパターン（subscribe_events で SSE 配信）に準拠。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import StreamingResponse

from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    def _get_unit():
        u = bot.unit_manager.get("clip_pipeline")
        if not u:
            raise HTTPException(503, "clip_pipeline unit not loaded")
        return u

    # --- jobs ---

    @app.post("/api/clip-pipeline/jobs", dependencies=[Depends(ctx.verify)])
    async def cp_job_create(request: Request):
        unit = _get_unit()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        video_path = (body.get("video_path") or "").strip()
        if not video_path:
            raise HTTPException(400, "video_path is required")
        mode = (body.get("mode") or "normal").strip().lower()
        whisper_model = (body.get("whisper_model") or "").strip()
        ollama_model = (body.get("ollama_model") or "").strip()
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params must be an object")
        output_dir = body.get("output_dir")
        try:
            job_id = await unit.enqueue(
                user_id=ctx.webgui_user_id or "webgui",
                platform="web",
                video_path=video_path,
                mode=mode,
                whisper_model=whisper_model,
                ollama_model=ollama_model,
                params=params,
                output_dir=output_dir,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"job_id": job_id}

    @app.get("/api/clip-pipeline/jobs", dependencies=[Depends(ctx.verify)])
    async def cp_jobs_list(
        status: str | None = None, limit: int = 50, offset: int = 0,
    ):
        unit = _get_unit()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        jobs = await unit.list_jobs(
            user_id=None, status=status, limit=limit, offset=offset,
        )
        return {"jobs": jobs}

    @app.get("/api/clip-pipeline/jobs/stream")
    async def cp_jobs_stream():
        unit = _get_unit()
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

    @app.get("/api/clip-pipeline/jobs/{job_id}", dependencies=[Depends(ctx.verify)])
    async def cp_job_detail(job_id: str):
        unit = _get_unit()
        job = await unit.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/clip-pipeline/jobs/{job_id}/cancel", dependencies=[Depends(ctx.verify)])
    async def cp_job_cancel(job_id: str):
        unit = _get_unit()
        ok = await unit.cancel_job(job_id)
        return {"ok": bool(ok)}

    # --- capability aggregation ---

    @app.get("/api/clip-pipeline/capability", dependencies=[Depends(ctx.verify)])
    async def cp_capability():
        """登録済み全 Agent に /clip-pipeline/capability を問い合わせて集約する。"""
        from src.units.clip_pipeline.agent_client import AgentClient  # lazy (discord.py 依存経路を遅延)

        unit = _get_unit()
        agents = list(getattr(unit.bot.unit_manager.agent_pool, "_agents", []) or [])
        results: list[dict] = []

        async def _probe(a: dict) -> dict:
            agent_id = a.get("id")
            try:
                client = AgentClient(a)
                try:
                    cap = await client.capability()
                finally:
                    await client.close()
                return {"agent_id": agent_id, "ok": True, "capability": cap}
            except Exception as e:
                return {"agent_id": agent_id, "ok": False, "error": str(e)}

        if agents:
            results = await asyncio.gather(
                *[_probe(a) for a in agents], return_exceptions=False,
            )
        return {"agents": results}

    @app.get("/api/clip-pipeline/inputs", dependencies=[Depends(ctx.verify)])
    async def cp_inputs(agent_id: str):
        """指定 Agent の /clip-pipeline/inputs をプロキシする。"""
        from src.units.clip_pipeline.agent_client import AgentClient

        unit = _get_unit()
        agents = list(
            getattr(unit.bot.unit_manager.agent_pool, "_agents", []) or []
        )
        target = next((a for a in agents if a.get("id") == agent_id), None)
        if not target:
            raise HTTPException(404, f"agent not found: {agent_id}")
        client = AgentClient(target)
        try:
            return await client.inputs()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"agent inputs failed: {e}")
        finally:
            await client.close()
