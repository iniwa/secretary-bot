"""設定 API: gemini / unit-gemini / llm / rakuten / chat / heartbeat / persona / settings / debug / logs/llm。"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException, Request

from src.web._context import WebContext

# 汎用 settings API で許容される key プレフィックス
SCALAR_PREFIXES: tuple[str, ...] = (
    "llm.", "gemini.", "heartbeat.", "inner_mind.", "character.",
    "chat.", "rss.", "weather.", "searxng.", "rakuten_search.",
    "stt.", "delegation.", "activity.", "docker_monitor.", "memory.",
)


def serialize_setting(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


def coerce_setting(val: str):
    if val == "true":
        return True
    if val == "false":
        return False
    try:
        if "." in val:
            return float(val)
        return int(val)
    except (ValueError, TypeError):
        pass
    try:
        return json.loads(val)
    except (ValueError, json.JSONDecodeError, TypeError):
        return val


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    # --- デバッグ: 楽天検索 ---

    @app.get("/api/debug/rakuten-search", dependencies=[Depends(ctx.verify)])
    async def debug_rakuten_search():
        """最後のrakuten_search実行データを返す（検索結果・LLMプロンプト・出力）。"""
        unit = bot.cogs.get("RakutenSearchUnit")
        if unit is None:
            return {"available": False, "data": {}}
        return {"available": True, "data": getattr(unit, "last_debug", {})}

    # --- 楽天検索設定 ---

    @app.get("/api/rakuten-config", dependencies=[Depends(ctx.verify)])
    async def get_rakuten_config():
        cfg = bot.config.get("rakuten_search", {})
        return {
            "max_results": cfg.get("max_results", 5),
            "fetch_details": cfg.get("fetch_details", True),
        }

    @app.post("/api/rakuten-config", dependencies=[Depends(ctx.verify)])
    async def set_rakuten_config(request: Request):
        body = await request.json()
        cfg = bot.config.setdefault("rakuten_search", {})
        for key in ("max_results", "fetch_details"):
            if key in body:
                cfg[key] = body[key]
                await bot.database.set_setting(f"rakuten_search.{key}", json.dumps(body[key]))
        # ユニットの設定をホットリロード
        unit = bot.cogs.get("RakutenSearchUnit")
        if unit:
            unit._max_results = cfg.get("max_results", 5)
            unit._fetch_details_enabled = cfg.get("fetch_details", True)
        return {"ok": True}

    # --- 会話履歴設定 ---

    @app.get("/api/chat-config", dependencies=[Depends(ctx.verify)])
    async def get_chat_config():
        cfg = bot.config.get("units", {}).get("chat", {})
        return {
            "history_minutes": cfg.get("history_minutes", 60),
        }

    @app.post("/api/chat-config", dependencies=[Depends(ctx.verify)])
    async def set_chat_config(request: Request):
        body = await request.json()
        cfg = bot.config.setdefault("units", {}).setdefault("chat", {})
        if "history_minutes" in body:
            val = int(body["history_minutes"])
            cfg["history_minutes"] = val
            await bot.database.set_setting("units.chat.history_minutes", json.dumps(val))
        return {"ok": True}

    # --- デバッグ: LLM状態確認 ---

    @app.get("/api/debug/llm-state", )
    async def debug_llm_state():
        units_info = {}
        for name, cog in bot.cogs.items():
            if hasattr(cog, "llm"):
                llm = cog.llm
                units_info[name] = {
                    "purpose": llm._purpose,
                    "ollama_only": llm._ollama_only,
                    "gemini_allowed": llm._gemini_allowed,
                    "ollama_model": llm._ollama_model,
                    "gemini_model": llm._gemini_model,
                }
        return {
            "ollama_available": bot.llm_router.ollama_available,
            "gemini_config": bot.llm_router._gemini_config,
            "units": units_info,
        }

    # --- Gemini設定 ---

    @app.get("/api/gemini-config", dependencies=[Depends(ctx.verify)])
    async def get_gemini_config():
        return bot.config.get("gemini", {})

    @app.post("/api/gemini-config", )
    async def set_gemini_config(request: Request):
        body = await request.json()
        gemini_cfg = bot.config.setdefault("gemini", {})
        for key in ("conversation", "memory_extraction", "unit_routing", "monthly_token_limit"):
            if key in body:
                gemini_cfg[key] = body[key]
                await bot.database.set_setting(f"gemini.{key}", json.dumps(body[key]))
        bot.llm_router._gemini_config = gemini_cfg
        return {"ok": True}

    # --- ユニット別Gemini許可 ---

    @app.get("/api/unit-gemini", )
    async def get_unit_gemini():
        units_cfg = bot.config.get("units", {})
        result = {}
        for name in units_cfg:
            result[name] = units_cfg[name].get("llm", {}).get("gemini_allowed", True)
        return result

    @app.post("/api/unit-gemini", )
    async def set_unit_gemini(request: Request):
        body = await request.json()
        unit_name = body.get("unit", "")
        allowed = bool(body.get("allowed", True))
        ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
        ucfg.setdefault("llm", {})["gemini_allowed"] = allowed
        # 実行中ユニットにも反映
        cog = bot.cogs.get(unit_name)
        if cog and hasattr(cog, "llm"):
            cog.llm._gemini_allowed = allowed
        await bot.database.set_setting(f"unit_gemini.{unit_name}", "true" if allowed else "false")
        return {"ok": True}

    # --- LLMログ（Ollama/Gemini） ---

    @app.get("/api/logs/llm", dependencies=[Depends(ctx.verify)])
    async def get_llm_logs(limit: int = 50, offset: int = 0, provider: str | None = None):
        logs = await bot.database.get_llm_logs(limit=limit, offset=offset, provider=provider)
        # instance を URL/IP → エージェント名に変換（過去ログ互換）
        url_to_name = getattr(bot.llm_router, "_url_to_name", {}) or {}
        for log_row in logs:
            inst = log_row.get("instance")
            if not inst:
                continue
            # 完全一致（新形式: URL または既に名前）
            if inst in url_to_name:
                log_row["instance"] = url_to_name[inst]
                continue
            # 部分一致（旧形式: IP やホスト名のみ）
            for url, name in url_to_name.items():
                if inst in url or url.find(inst) >= 0:
                    log_row["instance"] = name
                    break
        return {"logs": logs}

    # --- デバッグ: ハートビートログ ---

    @app.get("/api/debug/heartbeat-logs", dependencies=[Depends(ctx.verify)])
    async def debug_heartbeat_logs():
        return {"logs": list(bot.heartbeat.debug_logs)}

    # --- LLM設定 ---

    @app.get("/api/llm-config", )
    async def get_llm_config():
        llm_cfg = bot.config.get("llm", {})
        units_cfg = bot.config.get("units", {})
        # ユニットごとのモデル上書き情報を収集
        unit_models = {}
        for name, ucfg in units_cfg.items():
            unit_llm = ucfg.get("llm", {})
            if unit_llm.get("ollama_model"):
                unit_models[name] = unit_llm["ollama_model"]
        return {
            "ollama_model": llm_cfg.get("ollama_model", "gemma4"),
            "ollama_timeout": int(llm_cfg.get("ollama_timeout", 300)),
            "gemini_model": bot.llm_router.gemini.model,
            "unit_models": unit_models,
        }

    @app.post("/api/llm-config", )
    async def set_llm_config(request: Request):
        body = await request.json()

        # グローバルモデル変更
        if "ollama_model" in body:
            model = body["ollama_model"].strip()
            bot.config.setdefault("llm", {})["ollama_model"] = model
            bot.llm_router.ollama.model = model
            await bot.database.set_setting("llm.ollama_model", model)
            # ユニット別上書きが無いユニットにも反映
            for cog in bot.cogs.values():
                if hasattr(cog, "llm") and cog.llm._ollama_model is None:
                    pass  # model=None → OllamaClient.model を参照するので自動反映

        # Geminiモデル変更
        if "gemini_model" in body:
            gmodel = body["gemini_model"].strip()
            if gmodel:
                bot.config.setdefault("llm", {})["gemini_model"] = gmodel
                bot.llm_router.gemini.model = gmodel
                await bot.database.set_setting("llm.gemini_model", gmodel)

        # タイムアウト変更
        if "ollama_timeout" in body:
            t = int(body["ollama_timeout"])
            if t < 10:
                raise HTTPException(400, "ollama_timeout must be >= 10")
            bot.config.setdefault("llm", {})["ollama_timeout"] = t
            bot.llm_router.ollama.timeout = t
            await bot.database.set_setting("llm.ollama_timeout", str(t))

        # ユニット別モデル変更
        if "unit_models" in body:
            for unit_name, model in body["unit_models"].items():
                model = model.strip() if model else ""
                ucfg = bot.config.setdefault("units", {}).setdefault(unit_name, {})
                if model:
                    ucfg.setdefault("llm", {})["ollama_model"] = model
                    await bot.database.set_setting(f"unit_llm.{unit_name}", model)
                else:
                    ucfg.get("llm", {}).pop("ollama_model", None)
                    await bot.database.delete_setting(f"unit_llm.{unit_name}")
                # 実行中のユニットのUnitLLMにも反映
                cog = bot.cogs.get(unit_name)
                if cog and hasattr(cog, "llm"):
                    cog.llm._ollama_model = model or None

        return {"ok": True}

    # --- ハートビート設定 ---

    @app.get("/api/heartbeat-config", dependencies=[Depends(ctx.verify)])
    async def get_heartbeat_config():
        hb_cfg = bot.config.get("heartbeat", {})
        return {
            "interval_with_ollama_minutes": hb_cfg.get("interval_with_ollama_minutes", 15),
            "interval_without_ollama_minutes": hb_cfg.get("interval_without_ollama_minutes", 180),
            "compact_threshold_messages": hb_cfg.get("compact_threshold_messages", 20),
        }

    @app.post("/api/heartbeat-config", dependencies=[Depends(ctx.verify)])
    async def set_heartbeat_config(request: Request):
        body = await request.json()
        hb_cfg = bot.config.setdefault("heartbeat", {})
        for key in ("interval_with_ollama_minutes", "interval_without_ollama_minutes", "compact_threshold_messages"):
            if key in body:
                val = int(body[key])
                if val < 1:
                    raise HTTPException(400, f"{key} must be >= 1")
                hb_cfg[key] = val
                await bot.database.set_setting(f"heartbeat.{key}", str(val))
        # 次回スケジュールに反映
        bot.heartbeat._reschedule()
        return {"ok": True}

    # --- ペルソナ設定 ---

    @app.get("/api/persona", )
    async def get_persona():
        return {"persona": bot.config.get("character", {}).get("persona", "")}

    @app.post("/api/persona", )
    async def set_persona(request: Request):
        body = await request.json()
        persona = body.get("persona", "")
        bot.config.setdefault("character", {})["persona"] = persona
        await bot.database.set_setting("character.persona", persona)
        return {"ok": True}

    # --- 汎用 settings API ---
    # フロントからセクション単位で一括取得・保存するための汎用入口。
    # 個別エンドポイント（/api/llm-config 等）は互換維持のため並存。

    @app.get("/api/settings", dependencies=[Depends(ctx.verify)])
    async def get_settings(prefix: str = ""):
        if prefix and not any(prefix.startswith(p) or p.startswith(prefix) for p in SCALAR_PREFIXES):
            raise HTTPException(400, f"prefix '{prefix}' not allowed")
        raw = await bot.database.get_all_settings(prefix)
        return {k: coerce_setting(v) for k, v in raw.items()}

    @app.post("/api/settings", dependencies=[Depends(ctx.verify)])
    async def set_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        saved: list[str] = []
        for key, val in body.items():
            if not any(key.startswith(p) for p in SCALAR_PREFIXES):
                raise HTTPException(400, f"key '{key}' not allowed")
            await bot.database.set_setting(key, serialize_setting(val))
            # bot.config にもネストで反映（次回再起動まで現行プロセスでも有効に）
            parts = key.split(".")
            cur = bot.config
            for seg in parts[:-1]:
                cur = cur.setdefault(seg, {}) if isinstance(cur, dict) else cur
            if isinstance(cur, dict):
                cur[parts[-1]] = val
            saved.append(key)
        return {"ok": True, "saved": saved}
