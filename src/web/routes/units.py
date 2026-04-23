"""Units データ閲覧 / CRUD API: reminders, todos, memos, weather, timers, loaded。"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Request

from src.web._context import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    # --- Units データ閲覧 API ---

    @app.get("/api/units/reminders", )
    async def get_reminders(active: int | None = None):
        if active is not None:
            rows = await bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = ? ORDER BY remind_at DESC LIMIT 100",
                (active,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM reminders ORDER BY remind_at DESC LIMIT 100"
            )
        return {"items": rows}

    @app.get("/api/units/todos", )
    async def get_todos(done: int | None = None):
        if done is not None:
            rows = await bot.database.fetchall(
                "SELECT * FROM todos WHERE done = ? ORDER BY created_at DESC LIMIT 100",
                (done,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM todos ORDER BY created_at DESC LIMIT 100"
            )
        return {"items": rows}

    @app.get("/api/units/memos", )
    async def get_memos(keyword: str | None = None):
        if keyword:
            rows = await bot.database.fetchall(
                "SELECT * FROM memos WHERE content LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT 100",
                (f"%{keyword}%", f"%{keyword}%"),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM memos ORDER BY created_at DESC LIMIT 100"
            )
        return {"items": rows}

    # --- リマインダー CRUD ---

    @app.put("/api/units/reminders/{rid}", )
    async def update_reminder(rid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM reminders WHERE id = ?", (rid,))
        if not row:
            raise HTTPException(404, "not found")
        message = body.get("message", row["message"])
        remind_at = body.get("remind_at", row["remind_at"])
        await bot.database.execute(
            "UPDATE reminders SET message = ?, remind_at = ?, notified = 0 WHERE id = ?",
            (message, remind_at, rid),
        )
        try:
            dt = datetime.fromisoformat(remind_at)
            bot.heartbeat.schedule_reminder(rid, dt, message, row.get("user_id", ""))
        except Exception:
            pass
        return {"ok": True}

    @app.post("/api/units/reminders/{rid}/done", )
    async def done_reminder(rid: int):
        from src.database import jst_now
        row = await bot.database.fetchone("SELECT * FROM reminders WHERE id = ? AND active = 1", (rid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute(
            "UPDATE reminders SET active = 0, done_at = ? WHERE id = ?", (jst_now(), rid)
        )
        bot.heartbeat.cancel_reminder(rid)
        return {"ok": True}

    @app.delete("/api/units/reminders/{rid}", )
    async def delete_reminder(rid: int):
        row = await bot.database.fetchone("SELECT * FROM reminders WHERE id = ?", (rid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM reminders WHERE id = ?", (rid,))
        bot.heartbeat.cancel_reminder(rid)
        return {"ok": True}

    # --- ToDo CRUD ---

    @app.put("/api/units/todos/{tid}", )
    async def update_todo(tid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM todos WHERE id = ?", (tid,))
        if not row:
            raise HTTPException(404, "not found")
        title = body.get("title", row["title"])
        due_date = body.get("due_date") if "due_date" in body else row.get("due_date")
        await bot.database.execute(
            "UPDATE todos SET title = ?, due_date = ? WHERE id = ?", (title, due_date, tid)
        )
        return {"ok": True}

    @app.post("/api/units/todos/{tid}/done", )
    async def done_todo(tid: int):
        from src.database import jst_now
        row = await bot.database.fetchone("SELECT * FROM todos WHERE id = ? AND done = 0", (tid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ?", (jst_now(), tid)
        )
        return {"ok": True}

    @app.delete("/api/units/todos/{tid}", )
    async def delete_todo(tid: int):
        row = await bot.database.fetchone("SELECT * FROM todos WHERE id = ?", (tid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM todos WHERE id = ?", (tid,))
        return {"ok": True}

    # --- メモ CRUD ---

    @app.put("/api/units/memos/{mid}", )
    async def update_memo(mid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM memos WHERE id = ?", (mid,))
        if not row:
            raise HTTPException(404, "not found")
        content = body.get("content", row["content"])
        tags = body.get("tags") if "tags" in body else row.get("tags", "")
        await bot.database.execute(
            "UPDATE memos SET content = ?, tags = ? WHERE id = ?", (content, tags, mid)
        )
        return {"ok": True}

    @app.post("/api/units/memos/{mid}/append", )
    async def append_memo(mid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM memos WHERE id = ?", (mid,))
        if not row:
            raise HTTPException(404, "not found")
        append_text = body.get("content", "")
        if not append_text:
            raise HTTPException(400, "content is required")
        updated = row["content"] + "\n" + append_text
        await bot.database.execute(
            "UPDATE memos SET content = ? WHERE id = ?", (updated, mid)
        )
        return {"ok": True}

    @app.delete("/api/units/memos/{mid}", )
    async def delete_memo(mid: int):
        row = await bot.database.fetchone("SELECT * FROM memos WHERE id = ?", (mid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM memos WHERE id = ?", (mid,))
        return {"ok": True}

    # --- 天気通知 CRUD ---

    @app.get("/api/units/weather", )
    async def get_weather_subscriptions(active: int | None = None):
        if active is not None:
            rows = await bot.database.fetchall(
                "SELECT * FROM weather_subscriptions WHERE active = ? ORDER BY id DESC LIMIT 100",
                (active,),
            )
        else:
            rows = await bot.database.fetchall(
                "SELECT * FROM weather_subscriptions ORDER BY id DESC LIMIT 100"
            )
        return {"items": rows}

    @app.put("/api/units/weather/{wid}", )
    async def update_weather_sub(wid: int, request: Request):
        body = await request.json()
        row = await bot.database.fetchone("SELECT * FROM weather_subscriptions WHERE id = ?", (wid,))
        if not row:
            raise HTTPException(404, "not found")
        notify_hour = body.get("notify_hour", row["notify_hour"])
        notify_minute = body.get("notify_minute", row["notify_minute"])
        location = row["location"]
        latitude = row["latitude"]
        longitude = row["longitude"]

        # 地域変更がリクエストされた場合、ジオコーディングして更新
        new_location = body.get("location")
        if new_location and new_location != location:
            weather_unit = bot.unit_manager.get("weather")
            if weather_unit:
                actual = getattr(weather_unit, "unit", weather_unit)
                geo = await actual._geocode(new_location)
                if not geo:
                    raise HTTPException(400, f"「{new_location}」の位置情報が見つかりません")
                location = geo["name"]
                latitude = geo["latitude"]
                longitude = geo["longitude"]

        await bot.database.execute(
            "UPDATE weather_subscriptions SET notify_hour = ?, notify_minute = ?, location = ?, latitude = ?, longitude = ? WHERE id = ?",
            (notify_hour, notify_minute, location, latitude, longitude, wid),
        )
        # スケジューラ更新
        if row["active"]:
            bot.heartbeat.schedule_weather_daily(
                wid, notify_hour, notify_minute,
                row["user_id"], latitude, longitude, location,
            )
        return {"ok": True}

    @app.delete("/api/units/weather/{wid}", )
    async def delete_weather_sub(wid: int):
        row = await bot.database.fetchone("SELECT * FROM weather_subscriptions WHERE id = ?", (wid,))
        if not row:
            raise HTTPException(404, "not found")
        await bot.database.execute("DELETE FROM weather_subscriptions WHERE id = ?", (wid,))
        bot.heartbeat.cancel_weather_daily(wid)
        return {"ok": True}

    @app.post("/api/units/weather/{wid}/toggle", )
    async def toggle_weather_sub(wid: int):
        row = await bot.database.fetchone("SELECT * FROM weather_subscriptions WHERE id = ?", (wid,))
        if not row:
            raise HTTPException(404, "not found")
        new_active = 0 if row["active"] else 1
        await bot.database.execute(
            "UPDATE weather_subscriptions SET active = ? WHERE id = ?", (new_active, wid)
        )
        if new_active:
            bot.heartbeat.schedule_weather_daily(
                wid, row["notify_hour"], row["notify_minute"],
                row["user_id"], row["latitude"], row["longitude"], row["location"],
            )
        else:
            bot.heartbeat.cancel_weather_daily(wid)
        return {"ok": True, "active": new_active}

    @app.get("/api/units/timers", )
    async def get_timers():
        import time as _time
        timer_unit = bot.unit_manager.get("timer")
        if timer_unit is None:
            return {"items": []}
        actual = getattr(timer_unit, "unit", timer_unit)
        items = []
        now = _time.time()
        for tid, info in actual._timer_info.items():
            elapsed = now - info["created_at"]
            remaining = max(0, info["minutes"] * 60 - elapsed)
            items.append({
                "id": tid,
                "message": info["message"],
                "minutes": info["minutes"],
                "remaining_sec": int(remaining),
            })
        return {"items": items}

    @app.get("/api/units/loaded", )
    async def get_loaded_units():
        """現在ロードされているユニット一覧を返す。"""
        units = []
        for unit in bot.unit_manager.units.values():
            actual = getattr(unit, "unit", unit)
            units.append({
                "name": actual.UNIT_NAME,
                "description": actual.UNIT_DESCRIPTION,
                "delegate_to": actual.DELEGATE_TO,
                "breaker_state": actual.breaker.state,
                "chat_routable": getattr(actual, "CHAT_ROUTABLE", True),
            })
        return {"units": units}

    @app.get("/api/router/info", )
    async def get_router_info():
        """ユニットルーターが選択肢として提示するユニット一覧を返す。

        WebGUI でチャットルーターの挙動を確認するためのエンドポイント。
        chat_routable=True のユニットのみを LLM プロンプトに掲載する。
        """
        routable: list[dict] = []
        excluded: list[dict] = []
        for unit in bot.unit_manager.units.values():
            actual = getattr(unit, "unit", unit)
            entry = {
                "name": getattr(actual, "UNIT_NAME", ""),
                "description": getattr(actual, "UNIT_DESCRIPTION", ""),
                "delegate_to": getattr(actual, "DELEGATE_TO", None),
                "breaker_state": getattr(getattr(actual, "breaker", None), "state", None),
            }
            if getattr(actual, "CHAT_ROUTABLE", True):
                routable.append(entry)
            else:
                excluded.append(entry)

        # 直近のセッション（チャネル別の継続中ユニット）
        router = getattr(bot, "unit_router", None)
        sessions: list[dict] = []
        timeout = 0
        if router is not None:
            from src.unit_router import _SESSION_TIMEOUT
            import time as _time
            timeout = _SESSION_TIMEOUT
            now = _time.monotonic()
            for key, sess in list(router._sessions.items()):
                age = now - sess["ts"]
                if age > timeout:
                    continue
                sessions.append({
                    "session_key": key,
                    "unit": sess["unit"],
                    "age_sec": int(age),
                    "expires_in_sec": int(timeout - age),
                })

        return {
            "routable": routable,
            "excluded": excluded,
            "sessions": sessions,
            "session_timeout_sec": timeout,
        }
