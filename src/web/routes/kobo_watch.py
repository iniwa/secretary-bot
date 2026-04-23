"""kobo_watch（楽天 Kobo 新刊監視）WebGUI API: /api/kobo-watch/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from src.errors import RakutenApiError, RakutenAuthError
from src.logger import get_logger
from src.web._context import WebContext

log = get_logger(__name__)


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    def _get_unit():
        unit = bot.unit_manager.get("kobo_watch")
        if unit is None:
            raise HTTPException(503, "kobo_watch unit is not loaded")
        return unit

    @app.get("/api/kobo-watch/targets", dependencies=[Depends(ctx.verify)])
    async def list_targets(enabled_only: bool = False):
        targets = await bot.database.kobo_target_list(enabled_only=enabled_only)
        return {"targets": targets}

    @app.post("/api/kobo-watch/targets", dependencies=[Depends(ctx.verify)])
    async def create_target(request: Request):
        body = await request.json()
        author = (body.get("author") or "").strip()
        title_keyword = (body.get("title_keyword") or "").strip() or None
        notify_kobo_only = bool(body.get("notify_kobo_only", False))
        if not author:
            raise HTTPException(400, "author is required")

        try:
            target_id = await bot.database.kobo_target_add(
                author=author, title_keyword=title_keyword,
                user_id=ctx.webgui_user_id or "webgui",
                notify_kobo_only=notify_kobo_only,
            )
        except Exception as e:
            log.info("kobo_target_add failed: %s", e)
            raise HTTPException(409, "同じ組み合わせが既に登録されているよ") from e

        # 登録直後の backfill をユニット経由で実行（API キー無しなら 0 件で続行）
        backfilled = 0
        backfill_error: str | None = None
        unit = bot.unit_manager.get("kobo_watch")
        if unit is not None:
            try:
                backfilled = await unit._backfill_known_books(
                    target_id, author, title_keyword,
                )
            except RakutenAuthError:
                backfill_error = "no_credentials"
            except RakutenApiError as e:
                backfill_error = f"rakuten_api_error: {e}"
            except Exception as e:
                log.warning("backfill failed: %s", e)
                backfill_error = str(e)

        target = await bot.database.kobo_target_get(target_id)
        return {
            "target": target, "backfilled": backfilled,
            "backfill_error": backfill_error,
        }

    @app.delete(
        "/api/kobo-watch/targets/{target_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def delete_target(target_id: int):
        ok = await bot.database.kobo_target_remove(target_id)
        if not ok:
            raise HTTPException(404, "監視対象が見つからないよ")
        return {"ok": True}

    @app.patch(
        "/api/kobo-watch/targets/{target_id}",
        dependencies=[Depends(ctx.verify)],
    )
    async def update_target(target_id: int, request: Request):
        body = await request.json()
        ok = await bot.database.kobo_target_update(
            target_id,
            enabled=body.get("enabled"),
            notify_kobo_only=body.get("notify_kobo_only"),
        )
        if not ok:
            raise HTTPException(400, "何も更新しなかったよ（フィールド指定なし？）")
        target = await bot.database.kobo_target_get(target_id)
        return {"target": target}

    @app.get("/api/kobo-watch/detections", dependencies=[Depends(ctx.verify)])
    async def list_detections(limit: int = 50):
        limit = max(1, min(int(limit), 200))
        rows = await bot.database.kobo_detection_list(limit=limit)
        return {"detections": rows}

    @app.post("/api/kobo-watch/check-now", dependencies=[Depends(ctx.verify)])
    async def check_now():
        unit = _get_unit()
        try:
            count = await unit._check_new_releases()
        except RakutenAuthError as e:
            raise HTTPException(
                400,
                "楽天 API キーが設定されてないみたい。.env を確認してね。",
            ) from e
        except RakutenApiError as e:
            raise HTTPException(502, f"楽天 API エラー: {e}") from e
        return {"detected": count}
