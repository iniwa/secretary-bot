"""フロー追跡 API: /api/flow/state, /api/flow/stream。"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from starlette.responses import StreamingResponse

from src.flow_tracker import get_flow_tracker
from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    # bot 参照は不要（tracker はシングルトン）。ctx は将来拡張のため受け取るだけ。
    _ = ctx

    @app.get("/api/flow/state", )
    async def get_flow_state():
        tracker = get_flow_tracker()
        return tracker.get_state()

    @app.get("/api/flow/stream", )
    async def flow_stream():
        tracker = get_flow_tracker()
        queue = tracker.subscribe()

        async def event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(event)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                tracker.unsubscribe(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
