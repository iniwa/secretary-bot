"""ZZZ Disc Manager ルータ: HoYoLAB アカウント / 認証 / 自動ログイン / 同期。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import models
from ..schema import (
    HoyolabAccountIn,
    HoyolabAutoLoginIn,
    HoyolabCredentialsIn,
)


def build_hoyolab_router(bot, config: dict) -> APIRouter:
    router = APIRouter()
    db = bot.database

    # ---------------- HoYoLAB ----------------

    @router.get("/api/hoyolab/account")
    async def get_hoyolab_account():
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(404, "no account configured")
        # 自宅 Pi 前提のため cookie は平文で返す。password は返さない。
        return {
            "uid": acc["uid"],
            "region": acc["region"],
            "ltuid_v2": acc.get("ltuid_v2"),
            "ltoken_v2": acc.get("ltoken_v2"),
            "nickname": acc.get("nickname"),
            "last_synced_at": acc.get("last_synced_at"),
            "email": acc.get("email"),
            "auto_login_enabled": bool(acc.get("auto_login_enabled")),
            "has_password": bool(acc.get("password")),
            "last_auto_login_at": acc.get("last_auto_login_at"),
            "last_auto_login_error": acc.get("last_auto_login_error"),
        }

    @router.put("/api/hoyolab/account")
    async def put_hoyolab_account(payload: HoyolabAccountIn):
        await models.upsert_hoyolab_account(
            db,
            uid=payload.uid, region=payload.region,
            ltuid_v2=payload.ltuid_v2, ltoken_v2=payload.ltoken_v2,
            nickname=payload.nickname,
        )
        return {"ok": True}

    @router.delete("/api/hoyolab/account")
    async def del_hoyolab_account():
        await db.execute("DELETE FROM zzz_hoyolab_accounts")
        return {"deleted": True}

    # ---------------- HoYoLAB 自動ログイン ----------------

    @router.put("/api/hoyolab/credentials")
    async def put_hoyolab_credentials(payload: HoyolabCredentialsIn):
        """既存アカウントに自動ログイン用 email/password を保存。"""
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400,
                "先に uid/region/cookie を登録してください")
        await models.upsert_hoyolab_account(
            db,
            uid=acc["uid"], region=acc["region"],
            ltuid_v2=acc["ltuid_v2"], ltoken_v2=acc["ltoken_v2"],
            email=payload.email, password=payload.password,
            auto_login_enabled=payload.auto_login_enabled,
        )
        return {"ok": True}

    @router.delete("/api/hoyolab/credentials")
    async def del_hoyolab_credentials():
        await db.execute(
            "UPDATE zzz_hoyolab_accounts SET email = NULL, password = NULL, "
            "auto_login_enabled = 0"
        )
        return {"ok": True}

    @router.post("/api/hoyolab/auto-login")
    async def post_hoyolab_auto_login(payload: HoyolabAutoLoginIn):
        """email/password で自動ログインし cookies を取得・保存。

        既存アカウントがある場合は uid/region を流用し cookie を更新。
        無い場合は payload の uid/region で新規作成（どちらも必須）。
        save_credentials=True なら email/password と auto_login_enabled=1 を保存。
        """
        try:
            from ..hoyolab_auth import (
                CaptchaRequired,
                HoyolabLoginError,
                InvalidCredentials,
                auto_login,
            )
        except ImportError as e:
            raise HTTPException(503, f"hoyolab auth unavailable: {e}")

        try:
            cookies = await auto_login(payload.email, payload.password)
        except InvalidCredentials as e:
            raise HTTPException(401, f"認証情報が不正です: {e}")
        except CaptchaRequired as e:
            raise HTTPException(409, f"captcha required: {e}")
        except HoyolabLoginError as e:
            raise HTTPException(502, f"ログイン失敗: {e}")

        existing = await models.get_hoyolab_account(db)
        if existing:
            uid = existing["uid"]
            region = existing["region"]
            nickname = payload.nickname or existing.get("nickname")
        else:
            if not payload.uid or not payload.region:
                raise HTTPException(400,
                    "初回登録時は uid と region が必要です")
            uid = payload.uid
            region = payload.region
            nickname = payload.nickname

        await models.upsert_hoyolab_account(
            db,
            uid=uid, region=region,
            ltuid_v2=cookies["ltuid_v2"], ltoken_v2=cookies["ltoken_v2"],
            nickname=nickname,
            email=payload.email if payload.save_credentials else None,
            password=payload.password if payload.save_credentials else None,
            auto_login_enabled=True if payload.save_credentials else None,
            account_mid_v2=cookies.get("account_mid_v2"),
            account_id_v2=cookies.get("account_id_v2"),
            cookie_token_v2=cookies.get("cookie_token_v2"),
            ltmid_v2=cookies.get("ltmid_v2"),
        )
        # last_auto_login_at を刻む
        await models.update_hoyolab_cookies(
            db, uid=uid,
            ltuid_v2=cookies["ltuid_v2"],
            ltoken_v2=cookies["ltoken_v2"],
            error=None,
        )
        return {
            "ok": True,
            "saved_credentials": payload.save_credentials,
            "ltuid_v2": cookies["ltuid_v2"],
        }

    @router.post("/api/hoyolab/refresh")
    async def post_hoyolab_refresh():
        """保存済み email/password で cookies を再取得。"""
        try:
            from ..hoyolab_auth import (
                CaptchaRequired,
                HoyolabLoginError,
                InvalidCredentials,
                refresh_account_cookies,
            )
        except ImportError as e:
            raise HTTPException(503, f"hoyolab auth unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        if not acc.get("email") or not acc.get("password"):
            raise HTTPException(400, "credentials が保存されていません")
        try:
            cookies = await refresh_account_cookies(db, acc)
        except InvalidCredentials as e:
            raise HTTPException(401, f"認証情報が不正です: {e}")
        except CaptchaRequired as e:
            raise HTTPException(409, f"captcha required: {e}")
        except HoyolabLoginError as e:
            raise HTTPException(502, f"再ログイン失敗: {e}")
        return {"ok": True, "ltuid_v2": cookies["ltuid_v2"]}

    @router.post("/api/hoyolab/reset")
    async def post_hoyolab_reset():
        """HoYoLAB 同期データを一掃（キャラ重複解消・再同期用）。"""
        result = await models.reset_hoyolab_synced_data(db)
        return {"ok": True, **result}

    @router.post("/api/characters/cleanup-empty")
    async def post_characters_cleanup():
        """ビルドが 1 件も無いキャラを削除する（未所持シードの掃除）。"""
        result = await models.delete_characters_without_builds(db)
        return {"ok": True, **result}

    async def _sweep_unpinned() -> int:
        """同期冒頭の掃除: ピン無しディスクを削除（参照スロットは NULL 化）。"""
        return await models.delete_unpinned_discs(db)

    @router.post("/api/hoyolab/sync")
    async def post_hoyolab_sync():
        try:
            from ..hoyolab_client import sync_current_builds
        except ImportError as e:
            raise HTTPException(503, f"hoyolab client unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        swept = await _sweep_unpinned()
        try:
            result = await sync_current_builds(db, acc)
        except Exception as e:
            raise HTTPException(502, f"hoyolab sync failed: {e}")
        # frontend は {results: [...]} を期待
        return {
            "ok": True,
            "synced_characters": result.get("synced_characters", 0),
            "synced_discs": result.get("synced_discs", 0),
            "swept_unpinned": swept,
            "results": result.get("results", []),
            "errors": result.get("errors", []),
        }

    @router.post("/api/hoyolab/sync/{character_id}")
    async def post_hoyolab_sync_one(character_id: int):
        """単一キャラのみ同期（frontend の「キャラ個別 同期」ボタン用）。"""
        try:
            from ..hoyolab_client import sync_current_builds
        except ImportError as e:
            raise HTTPException(503, f"hoyolab client unavailable: {e}")
        acc = await models.get_hoyolab_account(db)
        if not acc:
            raise HTTPException(400, "no hoyolab account configured")
        ch = await models.get_character(db, character_id)
        if not ch:
            raise HTTPException(404, "character not found")
        try:
            result = await sync_current_builds(
                db, acc,
                filter_hoyolab_id=ch.get("hoyolab_agent_id"),
            )
        except Exception as e:
            raise HTTPException(502, f"hoyolab sync failed: {e}")
        return {"ok": True, **result}

    return router
