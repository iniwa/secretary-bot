"""ドメイン別ルートモジュールの登録ファサード。"""

from __future__ import annotations

from fastapi import FastAPI

from src.web._context import WebContext
from src.web.routes import (
    activity,
    clip_pipeline,
    config,
    core,
    docker_monitor,
    flow,
    image_gen,
    inner_mind,
    input_relay,
    lora_train,
    memory,
    obs,
    rss,
    stt,
    system,
    units,
)


def register_all_routes(app: FastAPI, ctx: WebContext) -> None:
    """各ドメインモジュールの `register(app, ctx)` を順に呼ぶ。"""
    core.register(app, ctx)
    system.register(app, ctx)
    units.register(app, ctx)
    memory.register(app, ctx)
    config.register(app, ctx)
    flow.register(app, ctx)
    inner_mind.register(app, ctx)
    input_relay.register(app, ctx)
    stt.register(app, ctx)
    obs.register(app, ctx)
    activity.register(app, ctx)
    rss.register(app, ctx)
    docker_monitor.register(app, ctx)
    image_gen.register(app, ctx)
    lora_train.register(app, ctx)
    clip_pipeline.register(app, ctx)
