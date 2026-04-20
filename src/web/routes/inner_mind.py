"""InnerMind 関連 API: monologue / inner-mind/* / pending/*。"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException, Request

from src.web._context import WebContext
from src.web.routes.config import coerce_setting, serialize_setting


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    @app.get("/api/monologue", dependencies=[Depends(ctx.verify)])
    async def get_monologues(limit: int = 50):
        rows = await bot.database.get_monologues(limit=limit)
        return {"monologues": rows}

    @app.get("/api/inner-mind/status", dependencies=[Depends(ctx.verify)])
    async def get_inner_mind_status():
        """InnerMindの現在状態（self_model, activity, 最終思考等）。"""
        self_model = await bot.database.get_self_model()
        last_mono = await bot.database.get_last_monologue()

        # アクティビティ状態
        activity = {}
        if hasattr(bot, "activity_detector"):
            try:
                activity = await bot.activity_detector.get_status()
            except Exception:
                activity = {"error": "取得失敗"}

        # InnerMind有効/無効
        im = getattr(bot, "inner_mind", None)
        enabled = False
        if im:
            enabled_val = await im._get_setting("enabled", False)
            from src.inner_mind.core import _to_bool
            enabled = _to_bool(enabled_val)

        return {
            "self_model": self_model,
            "last_monologue": last_mono,
            "activity": activity,
            "enabled": enabled,
        }

    @app.get("/api/inner-mind/context", dependencies=[Depends(ctx.verify)])
    async def get_inner_mind_context():
        """InnerMindの現在のコンテキストソース一覧を返す。"""
        im = getattr(bot, "inner_mind", None)
        if not im:
            return {"sources": [], "error": "InnerMind not initialized"}
        try:
            results = await im.registry.collect_all({})
            sources = []
            for sr in results:
                sources.append({
                    "name": sr["name"],
                    "text": sr["text"],
                })
            return {"sources": sources}
        except Exception as e:
            return {"sources": [], "error": str(e)}

    @app.get("/api/inner-mind/settings", dependencies=[Depends(ctx.verify)])
    async def get_inner_mind_settings():
        im_cfg = bot.config.get("inner_mind", {})
        # DB保存値を優先読み込み
        enabled = await bot.database.get_setting("inner_mind.enabled")
        interval = await bot.database.get_setting("inner_mind.min_speak_interval_minutes")
        channel_id = await bot.database.get_setting("inner_mind.speak_channel_id")
        user_id = await bot.database.get_setting("inner_mind.target_user_id")
        tavily_raw = await bot.database.get_setting("inner_mind.tavily_news.queries")
        if tavily_raw:
            tavily_queries = [s.strip() for s in tavily_raw.split(",") if s.strip()]
        else:
            tavily_queries = list(im_cfg.get("tavily_news", {}).get("queries", []) or [])
        return {
            "enabled": (enabled == "true") if enabled is not None else im_cfg.get("enabled", False),
            "min_speak_interval_minutes": int(interval) if interval is not None else im_cfg.get("min_speak_interval_minutes", 0),
            "thinking_interval_ticks": im_cfg.get("thinking_interval_ticks", 2),
            "speak_channel_id": channel_id if channel_id is not None else im_cfg.get("speak_channel_id", ""),
            "target_user_id": user_id if user_id is not None else im_cfg.get("target_user_id", ""),
            "tavily_queries": tavily_queries,
        }

    @app.post("/api/inner-mind/settings", dependencies=[Depends(ctx.verify)])
    async def set_inner_mind_settings(request: Request):
        body = await request.json()
        im_cfg = bot.config.setdefault("inner_mind", {})

        if "enabled" in body:
            val = bool(body["enabled"])
            im_cfg["enabled"] = val
            await bot.database.set_setting("inner_mind.enabled", str(val).lower())

        if "min_speak_interval_minutes" in body:
            val = int(body["min_speak_interval_minutes"])
            if val < 0:
                raise HTTPException(400, "min_speak_interval_minutes must be >= 0")
            im_cfg["min_speak_interval_minutes"] = val
            await bot.database.set_setting("inner_mind.min_speak_interval_minutes", str(val))

        if "speak_channel_id" in body:
            val = str(body["speak_channel_id"])
            im_cfg["speak_channel_id"] = val
            await bot.database.set_setting("inner_mind.speak_channel_id", val)
        if "target_user_id" in body:
            val = str(body["target_user_id"])
            im_cfg["target_user_id"] = val
            await bot.database.set_setting("inner_mind.target_user_id", val)

        if "tavily_queries" in body:
            raw = body["tavily_queries"]
            if isinstance(raw, list):
                items = [str(s).strip() for s in raw if str(s).strip()]
            else:
                items = [s.strip() for s in str(raw).split(",") if s.strip()]
            # CSV形式で保存（内部カンマを含むクエリは想定外）
            csv = ",".join(items)
            im_cfg.setdefault("tavily_news", {})["queries"] = items
            await bot.database.set_setting("inner_mind.tavily_news.queries", csv)

        return {"ok": True}

    @app.get("/api/inner-mind/dispatches", dependencies=[Depends(ctx.verify)])
    async def get_inner_mind_dispatches(limit: int = 20):
        """直近の dispatch 結果（mimi_monologue のうち action 実行あり）を返す。"""
        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit = 20
        rows = await bot.database.fetchall(
            "SELECT id, created_at, action, reasoning, action_result, pending_id "
            "FROM mimi_monologue "
            "WHERE action IS NOT NULL AND action != 'no_op' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return {"dispatches": rows}

    # --- InnerMind 自律: pending_actions ---

    @app.get("/api/inner-mind/autonomy", dependencies=[Depends(ctx.verify)])
    async def get_autonomy_settings():
        raw = await bot.database.get_all_settings("inner_mind.autonomy.")
        return {k.removeprefix("inner_mind.autonomy."): coerce_setting(v) for k, v in raw.items()}

    @app.post("/api/inner-mind/autonomy", dependencies=[Depends(ctx.verify)])
    async def set_autonomy_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        saved = []
        for short_key, val in body.items():
            key = f"inner_mind.autonomy.{short_key}"
            await bot.database.set_setting(key, serialize_setting(val))
            saved.append(key)
        return {"ok": True, "saved": saved}

    @app.get("/api/inner-mind/autonomy/units", dependencies=[Depends(ctx.verify)])
    async def get_autonomy_units():
        """全ユニットの (tier, AUTONOMOUS_ACTIONS) を列挙。UI のチェックリスト用。"""
        tier2, tier3 = [], []
        for cog in list(bot.cogs.values()):
            tier = getattr(cog, "AUTONOMY_TIER", 4)
            actions = getattr(cog, "AUTONOMOUS_ACTIONS", []) or []
            unit_name = getattr(cog, "UNIT_NAME", "") or cog.__class__.__name__
            if not actions:
                continue
            for m in actions:
                entry = {
                    "unit_name": unit_name,
                    "method": m,
                    "description": getattr(cog, "UNIT_DESCRIPTION", ""),
                    "key": f"{unit_name}.{m}",
                }
                if tier == 2:
                    tier2.append(entry)
                elif tier == 3:
                    tier3.append(entry)
        return {"tier2": tier2, "tier3": tier3}

    @app.get("/api/pending", dependencies=[Depends(ctx.verify)])
    async def list_pending(status: str | None = None, limit: int = 100):
        items = await bot.database.list_pending_actions(status=status, limit=limit)
        pending_count = await bot.database.count_pending_unread()
        return {"items": items, "counts": {"pending": pending_count}}

    @app.get("/api/pending/unread-count", dependencies=[Depends(ctx.verify)])
    async def pending_unread_count():
        c = await bot.database.count_pending_unread()
        return {"count": c}

    @app.get("/api/pending/{pid}", dependencies=[Depends(ctx.verify)])
    async def get_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        return p

    @app.post("/api/pending/{pid}/approve", dependencies=[Depends(ctx.verify)])
    async def approve_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        if p.get("status") != "pending":
            raise HTTPException(409, f"already {p.get('status')}")
        try:
            params = json.loads(p.get("params") or "{}")
        except Exception:
            params = {}
        result = await bot.actuator._execute_unit(
            p.get("unit_name") or "", p.get("method") or "",
            {"params": params}, p.get("monologue_id"),
        )
        if result.get("status") == "executed":
            await bot.database.resolve_pending_action(
                pid, "executed",
                json.dumps(result.get("result"), ensure_ascii=False), None,
            )
            await bot.actuator._rewrite_approval_message(p, "✅ WebGUIで承認")
        else:
            await bot.database.resolve_pending_action(
                pid, "failed", None, result.get("reason"),
            )
        return {"ok": True, "result": result}

    @app.post("/api/pending/{pid}/reject", dependencies=[Depends(ctx.verify)])
    async def reject_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        if p.get("status") != "pending":
            raise HTTPException(409, f"already {p.get('status')}")
        await bot.database.resolve_pending_action(pid, "rejected", None, None)
        await bot.actuator._rewrite_approval_message(p, "❌ WebGUIで却下")
        return {"ok": True}

    @app.post("/api/pending/{pid}/cancel", dependencies=[Depends(ctx.verify)])
    async def cancel_pending(pid: int):
        p = await bot.database.get_pending_action(pid)
        if p is None:
            raise HTTPException(404, "not found")
        if p.get("status") != "pending":
            raise HTTPException(409, f"already {p.get('status')}")
        await bot.database.resolve_pending_action(pid, "cancelled", None, None)
        await bot.actuator._rewrite_approval_message(p, "🚫 キャンセル")
        return {"ok": True}
