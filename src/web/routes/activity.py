"""Activity 関連 API: /api/activity/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from src.web._agent_helpers import agent_request
from src.web._context import WebContext


def _activity_cutoff(days: int) -> str | None:
    """days=0 は全期間（None）。それ以外は「今日を含む直近 days 日」の起点 00:00（JST）。
    例: days=7 かつ今日=2026-04-15 → '2026-04-09 00:00:00'。"""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from datetime import timezone as _tz
    if days <= 0:
        return None
    _JST = _tz(_td(hours=9))
    start_date = _dt.now(tz=_JST).date() - _td(days=days - 1)
    return start_date.strftime("%Y-%m-%d 00:00:00")


def _activity_range(
    days: int, start: str | None = None, end: str | None = None
) -> tuple[list[str], list, dict]:
    """期間指定を where 断片とパラメータに変換。
    start/end (YYYY-MM-DD) が与えられたら優先。end は排他的終端として翌日00:00を使う。
    """
    from datetime import date as _date
    from datetime import timedelta as _td
    parts: list[str] = []
    params: list = []
    meta: dict = {}
    if start or end:
        if start:
            parts.append("start_at >= ?")
            params.append(f"{start} 00:00:00")
            meta["start"] = start
        if end:
            try:
                end_excl = (_date.fromisoformat(end) + _td(days=1)).strftime(
                    "%Y-%m-%d 00:00:00"
                )
            except ValueError:
                end_excl = f"{end} 23:59:59"
            parts.append("start_at < ?")
            params.append(end_excl)
            meta["end"] = end
        return parts, params, meta
    cutoff = _activity_cutoff(days)
    if cutoff:
        parts.append("start_at >= ?")
        params.append(cutoff)
    meta["days"] = days
    meta["since"] = cutoff
    return parts, params, meta


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    # --- Main PC アクティビティ（ゲームプレイ状況） ---

    @app.get("/api/activity/main", )
    async def activity_main():
        """Main PC のフォアグラウンド / ゲーム状況を返す。"""
        results = await agent_request(bot, "GET", "/activity", role="main")
        if not results:
            return {"alive": False, "error": "Main PC agent not reachable"}
        return results[0]

    # --- Activity history (Main PC 過去プレイ履歴) ---

    @app.get("/api/activity/stats", dependencies=[Depends(ctx.verify)])
    async def activity_stats(
        days: int = 7, start: str | None = None, end: str | None = None,
        day: str = "",
    ):
        """期間内のゲーム / フォアグラウンド集計（ランキング）。
        優先度: day > start/end > days。"""
        days = max(0, min(int(days or 7), 3650))
        if day:
            parts = ["date(start_at) = ?"]
            params: list = [day]
            meta = {"day": day}
        else:
            parts, params, meta = _activity_range(days, start, end)
        where_clause = " AND ".join(["end_at IS NOT NULL", *parts])
        games = await bot.database.fetchall(
            f"""
            SELECT game_name, SUM(COALESCE(duration_sec, 0)) AS sec, COUNT(*) AS sessions,
                   MAX(start_at) AS last_played, MAX(COALESCE(duration_sec, 0)) AS longest_sec
            FROM game_sessions WHERE {where_clause}
            GROUP BY game_name ORDER BY sec DESC
            """,
            tuple(params),
        )
        fg = await bot.database.fetchall(
            f"""
            SELECT pc, process_name, during_game,
                   SUM(COALESCE(duration_sec, 0)) AS sec, COUNT(*) AS sessions
            FROM foreground_sessions WHERE {where_clause}
            GROUP BY pc, process_name, during_game ORDER BY sec DESC
            """,
            tuple(params),
        )
        # 両 PC 同時操作時間（Main サンプルの active_pcs に main と sub の両方が含まれる回数 × poll間隔）
        sim_where_parts = ["pc='main'", "active_pcs LIKE '%main%'", "active_pcs LIKE '%sub%'"]
        sim_params: list = []
        if day:
            sim_where_parts.append("date(ts) = ?")
            sim_params.append(day)
        elif start or end:
            r_parts, r_params, _ = _activity_range(days, start, end)
            # activity_samples.ts を起点にするため start_at を ts へ書き換え
            for rp in r_parts:
                sim_where_parts.append(rp.replace("start_at", "ts"))
            sim_params.extend(r_params)
        else:
            cutoff_sim = _activity_cutoff(days)
            if cutoff_sim:
                sim_where_parts.append("ts >= ?")
                sim_params.append(cutoff_sim)
        sim_row = await bot.database.fetchone(
            f"SELECT COUNT(*) AS c FROM activity_samples WHERE {' AND '.join(sim_where_parts)}",
            tuple(sim_params),
        )
        poll_interval = int(bot.config.get("activity", {}).get("poll_interval_seconds", 60))
        simultaneous_sec = (sim_row["c"] if sim_row else 0) * poll_interval
        return {**meta, "games": games, "foreground": fg, "simultaneous_sec": simultaneous_sec}

    @app.get("/api/activity/summary", dependencies=[Depends(ctx.verify)])
    async def activity_summary(
        days: int = 7, start: str | None = None, end: str | None = None,
        day: str = "",
    ):
        """期間サマリ: 総プレイ時間・セッション数・アクティブ日数・最長セッション。
        優先度: day > start/end > days。"""
        days = max(0, min(int(days or 7), 3650))
        if day:
            parts = ["date(start_at) = ?"]
            params: list = [day]
            meta = {"day": day}
        else:
            parts, params, meta = _activity_range(days, start, end)
        where_clause = " AND ".join(["end_at IS NOT NULL", *parts])

        row = await bot.database.fetchone(
            f"""
            SELECT COUNT(*) AS sessions,
                   COALESCE(SUM(duration_sec), 0) AS total_sec,
                   COALESCE(MAX(duration_sec), 0) AS longest_sec,
                   COUNT(DISTINCT date(start_at)) AS active_days,
                   COUNT(DISTINCT game_name) AS distinct_games
            FROM game_sessions WHERE {where_clause}
            """,
            tuple(params),
        )
        earliest = await bot.database.fetchone(
            "SELECT MIN(start_at) AS earliest FROM game_sessions WHERE end_at IS NOT NULL"
        )
        return {
            **meta,
            "sessions": row["sessions"] if row else 0,
            "total_sec": row["total_sec"] if row else 0,
            "longest_sec": row["longest_sec"] if row else 0,
            "active_days": row["active_days"] if row else 0,
            "distinct_games": row["distinct_games"] if row else 0,
            "earliest": earliest["earliest"] if earliest else None,
        }

    @app.get("/api/activity/daily", dependencies=[Depends(ctx.verify)])
    async def activity_daily(
        days: int = 30,
        year: int | None = None,
        month: int | None = None,
        start: str | None = None,
        end: str | None = None,
        pc: str = "main",
    ):
        """日別の活動時間。棒グラフ / カレンダー用。
        pc='main' (default): game_sessions（ゲームプレイ時間）
        pc='sub': foreground_sessions WHERE pc='sub'（Sub PC 作業時間）
        pc='both': 両方を含めて返す（クライアントで並列表示）
        start/end (YYYY-MM-DD) が最優先、次に year/month、最後に直近 days 日。"""
        from datetime import date as _date
        if pc not in ("main", "sub", "both"):
            pc = "main"
        if start or end:
            parts, params_list, meta = _activity_range(days, start, end)
            base_where = " AND ".join(["end_at IS NOT NULL", *parts])
            params: tuple = tuple(params_list)
        elif year and month:
            month_start = _date(year, month, 1)
            if month == 12:
                next_month = _date(year + 1, 1, 1)
            else:
                next_month = _date(year, month + 1, 1)
            m_start = month_start.strftime("%Y-%m-%d 00:00:00")
            m_end = next_month.strftime("%Y-%m-%d 00:00:00")
            base_where = "end_at IS NOT NULL AND start_at >= ? AND start_at < ?"
            params = (m_start, m_end)
            meta = {"year": year, "month": month}
        else:
            days = max(1, min(int(days or 30), 3650))
            cutoff = _activity_cutoff(days)
            base_where = "end_at IS NOT NULL AND start_at >= ?"
            params = (cutoff,)
            meta = {"days": days, "since": cutoff}

        # Main: game_sessions（pc カラムなし → そのまま）
        main_by_day: dict[str, dict] = {}
        if pc in ("main", "both"):
            rows = await bot.database.fetchall(
                f"""
                SELECT date(start_at) AS day, game_name,
                       SUM(COALESCE(duration_sec, 0)) AS sec
                FROM game_sessions
                WHERE {base_where}
                GROUP BY day, game_name ORDER BY day, sec DESC
                """,
                params,
            )
            for r in rows:
                d = r["day"]
                if d not in main_by_day:
                    main_by_day[d] = {"sec": 0, "items": []}
                main_by_day[d]["sec"] += int(r["sec"] or 0)
                main_by_day[d]["items"].append(
                    {"name": r["game_name"], "sec": int(r["sec"] or 0)}
                )

        # Sub: foreground_sessions WHERE pc='sub'
        sub_by_day: dict[str, dict] = {}
        if pc in ("sub", "both"):
            sub_where = f"{base_where} AND pc = 'sub'"
            rows = await bot.database.fetchall(
                f"""
                SELECT date(start_at) AS day, process_name,
                       SUM(COALESCE(duration_sec, 0)) AS sec
                FROM foreground_sessions
                WHERE {sub_where}
                GROUP BY day, process_name ORDER BY day, sec DESC
                """,
                params,
            )
            for r in rows:
                d = r["day"]
                if d not in sub_by_day:
                    sub_by_day[d] = {"sec": 0, "items": []}
                sub_by_day[d]["sec"] += int(r["sec"] or 0)
                sub_by_day[d]["items"].append(
                    {"name": r["process_name"], "sec": int(r["sec"] or 0)}
                )

        all_days = sorted(set(main_by_day.keys()) | set(sub_by_day.keys()))
        daily: list[dict] = []
        for d in all_days:
            m = main_by_day.get(d)
            s = sub_by_day.get(d)
            entry: dict = {"day": d}
            main_sec = (m["sec"] if m else 0) if pc in ("main", "both") else 0
            sub_sec = (s["sec"] if s else 0) if pc in ("sub", "both") else 0
            if pc in ("main", "both"):
                entry["main_sec"] = main_sec
                entry["main_items"] = m["items"] if m else []
            if pc in ("sub", "both"):
                entry["sub_sec"] = sub_sec
                entry["sub_items"] = s["items"] if s else []
            # 後方互換: 既存の単独モード呼び出し用
            if pc == "main":
                entry["total_sec"] = main_sec
                entry["games"] = [
                    {"game_name": i["name"], "sec": i["sec"]} for i in entry["main_items"]
                ]
            elif pc == "sub":
                entry["total_sec"] = sub_sec
                entry["processes"] = [
                    {"process_name": i["name"], "sec": i["sec"]} for i in entry["sub_items"]
                ]
            else:
                entry["total_sec"] = main_sec + sub_sec
            daily.append(entry)
        return {**meta, "pc": pc, "daily": daily}

    @app.get("/api/activity/sessions", dependencies=[Depends(ctx.verify)])
    async def activity_sessions(
        days: int = 30,
        game: str = "",
        day: str = "",
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """個別プレイセッション一覧（新しい順）。game / day / start+end で絞込み。
        優先度: day > start/end > days。"""
        days = max(0, min(int(days or 30), 3650))
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))

        where_parts = ["end_at IS NOT NULL"]
        params: list = []
        if day:
            where_parts.append("date(start_at) = ?")
            params.append(day)
        elif start or end:
            range_parts, range_params, _ = _activity_range(days, start, end)
            where_parts.extend(range_parts)
            params.extend(range_params)
        else:
            cutoff = _activity_cutoff(days)
            if cutoff:
                where_parts.append("start_at >= ?")
                params.append(cutoff)
        if game:
            where_parts.append("game_name = ?")
            params.append(game)
        where = "WHERE " + " AND ".join(where_parts)

        total_row = await bot.database.fetchone(
            f"SELECT COUNT(*) AS c FROM game_sessions {where}", tuple(params)
        )
        rows = await bot.database.fetchall(
            f"""
            SELECT id, game_name, start_at, end_at, duration_sec
            FROM game_sessions {where}
            ORDER BY start_at DESC LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        )
        return {
            "total": total_row["c"] if total_row else 0,
            "limit": limit, "offset": offset,
            "sessions": rows,
        }

    # --- Daily diary（日記） ---

    @app.get("/api/activity/diary", dependencies=[Depends(ctx.verify)])
    async def activity_diary(date: str = ""):
        """指定日（YYYY-MM-DD）の日記を返す。未指定なら昨日。未生成なら exists=False。"""
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz
        if not date:
            _JST = _tz(_td(hours=9))
            date = (_dt.now(tz=_JST) - _td(days=1)).strftime("%Y-%m-%d")
        row = await bot.database.fetchone(
            "SELECT date, diary, streaming_detected, total_game_sec, total_stream_sec, created_at "
            "FROM daily_diaries WHERE date = ?",
            (date,),
        )
        if not row:
            return {"date": date, "exists": False}
        return {"exists": True, **dict(row)}

    @app.get("/api/activity/diary/list", dependencies=[Depends(ctx.verify)])
    async def activity_diary_list(
        days: int = 30, start: str | None = None, end: str | None = None,
    ):
        """日記が存在する日の一覧（新しい順）。カレンダー等で「日記あり」マーク用。"""
        days = max(0, min(int(days or 30), 3650))
        where_parts: list[str] = []
        params: list = []
        if start:
            where_parts.append("date >= ?")
            params.append(start)
        if end:
            where_parts.append("date <= ?")
            params.append(end)
        if not (start or end):
            cutoff = _activity_cutoff(days)
            if cutoff:
                where_parts.append("date >= ?")
                params.append(cutoff[:10])
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        rows = await bot.database.fetchall(
            f"SELECT date, streaming_detected, total_game_sec, total_stream_sec, created_at "
            f"FROM daily_diaries {where} ORDER BY date DESC",
            tuple(params),
        )
        return {"diaries": rows}

    @app.post("/api/activity/diary/regenerate", dependencies=[Depends(ctx.verify)])
    async def activity_diary_regenerate(request: Request):
        """指定日の日記を再生成（未生成なら新規生成）。date 省略時は昨日。"""
        from datetime import date as _date
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz
        from src.activity.daily_diary import run_daily_diary

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        target = (payload.get("date") or "").strip()
        if target:
            try:
                _date.fromisoformat(target)
            except ValueError:
                raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        else:
            _JST = _tz(_td(hours=9))
            target = (_dt.now(tz=_JST) - _td(days=1)).strftime("%Y-%m-%d")

        ok = await run_daily_diary(bot, target_date=target)
        if not ok:
            return {"ok": False, "date": target, "reason": "no_data_or_llm_failed"}
        row = await bot.database.fetchone(
            "SELECT date, diary, streaming_detected, total_game_sec, total_stream_sec, created_at "
            "FROM daily_diaries WHERE date = ?",
            (target,),
        )
        return {"ok": True, **(dict(row) if row else {"date": target})}
