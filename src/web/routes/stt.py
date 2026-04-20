"""STT関連 API: stt-config, stt/resummarize, stt/* (Agent proxy)。"""

from __future__ import annotations

import json
import re

from fastapi import Depends, FastAPI, HTTPException, Request

from src.web._agent_helpers import agent_request, agent_request_json
from src.web._context import WebContext

_STT_CONFIG_KEYS = {
    "summary_threshold_chars": int,
    "silence_trigger_minutes": int,
    "gap_split_minutes": int,
    "min_chunk_chars": int,
    "retention_days": int,
}


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    # --- STT設定 ---

    @app.get("/api/stt-config", dependencies=[Depends(ctx.verify)])
    async def get_stt_config():
        cfg = bot.config.get("stt", {}).get("processing", {})
        return {k: cfg.get(k) for k in _STT_CONFIG_KEYS}

    @app.post("/api/stt-config", dependencies=[Depends(ctx.verify)])
    async def set_stt_config(request: Request):
        body = await request.json()
        cfg = bot.config.setdefault("stt", {}).setdefault("processing", {})
        updated = {}
        for key, caster in _STT_CONFIG_KEYS.items():
            if key in body:
                try:
                    val = caster(body[key])
                except (TypeError, ValueError):
                    continue
                cfg[key] = val
                updated[key] = val
                await bot.database.set_setting(f"stt.processing.{key}", json.dumps(val))
        return {"ok": True, "updated": updated}

    # --- 管理: STT要約の再生成 ---

    @app.post("/api/stt/resummarize", dependencies=[Depends(ctx.verify)])
    async def stt_resummarize(request: Request):
        """指定した stt_summaries 行を現在のプロンプトで作り直す。ids=[...] または all_non_japanese=true。"""
        from src.stt.processor import STTProcessor
        body = await request.json()
        processor = STTProcessor(bot)

        target_ids: list[int] = []
        if body.get("all_non_japanese"):
            rows = await bot.database.fetchall(
                "SELECT id, summary FROM stt_summaries"
            )
            non_ja = re.compile(r"[\uAC00-\uD7AF\u4E00-\u9FFF]")
            for r in rows:
                if non_ja.search(r["summary"]):
                    target_ids.append(r["id"])
        else:
            target_ids = [int(i) for i in body.get("ids", [])]

        results = []
        for sid in target_ids:
            ok = await processor.resummarize(sid)
            results.append({"id": sid, "ok": ok})
        return {"targets": target_ids, "results": results}

    # --- STT管理 (Agent proxy) ---

    @app.get("/api/stt/status", )
    async def stt_status():
        """全AgentのSTT状態を返す。"""
        results = await agent_request(bot, "GET", "/stt/status")
        return {"agents": results}

    @app.get("/api/stt/devices", )
    async def stt_devices(role: str = "sub"):
        """指定PCのマイクデバイス一覧を返す。"""
        results = await agent_request(bot, "GET", "/stt/devices", role=role)
        if not results:
            return {"devices": [], "error": f"{role} PC agent not reachable"}
        return results[0]

    @app.post("/api/stt/control", )
    async def stt_control(request: Request):
        """STT制御（init/start/stop/set_device）。roleパラメータで対象PC指定。"""
        body = await request.json()
        role = body.pop("role", "sub")
        results = await agent_request_json(bot, "POST", "/stt/control", role=role, json_body=body)
        if not results:
            raise HTTPException(503, f"{role} PC agent not reachable")
        return results[0]

    @app.get("/api/stt/model/status", )
    async def stt_model_status():
        """Sub PCのWhisperモデル状態を返す。"""
        results = await agent_request(bot, "GET", "/stt/model/status", role="sub")
        if not results:
            return {"loaded": False, "error": "Sub PC agent not reachable"}
        return results[0]

    @app.get("/api/stt/transcripts", )
    async def stt_transcripts(role: str = "sub"):
        """指定PCの最新transcriptを返す。"""
        results = await agent_request(bot, "GET", "/stt/transcripts", role=role)
        if not results:
            return {"transcripts": []}
        return results[0]

    @app.get("/api/stt/summaries")
    async def stt_summaries(limit: int = 20):
        """ローカルDBからSTT要約一覧を返す。"""
        rows = await bot.database.fetchall(
            "SELECT id, summary, transcript_ids, created_at "
            "FROM stt_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return {"summaries": [dict(r) for r in rows]}
