"""HoYoLAB アカウント cookie / 自動ログイン / 同期ハウスキーピング。"""

from ._base import _now

__all__ = [
    "get_hoyolab_account",
    "upsert_hoyolab_account",
    "update_hoyolab_cookies",
    "record_auto_login_error",
    "update_hoyolab_synced",
    "delete_characters_without_builds",
    "reset_hoyolab_synced_data",
]


# ---------- HoYoLAB アカウント ----------

async def get_hoyolab_account(db) -> dict | None:
    """単一アカウント想定（自宅 Pi 用途）。"""
    row = await db.fetchone(
        "SELECT id, uid, region, ltuid_v2, ltoken_v2, nickname, last_synced_at, "
        "email, password, auto_login_enabled, last_auto_login_at, last_auto_login_error, "
        "account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2 "
        "FROM zzz_hoyolab_accounts ORDER BY id LIMIT 1"
    )
    return dict(row) if row else None


async def upsert_hoyolab_account(db, *, uid: str, region: str,
                                 ltuid_v2: str, ltoken_v2: str,
                                 nickname: str | None = None,
                                 email: str | None = None,
                                 password: str | None = None,
                                 auto_login_enabled: bool | None = None,
                                 account_mid_v2: str | None = None,
                                 account_id_v2: str | None = None,
                                 cookie_token_v2: str | None = None,
                                 ltmid_v2: str | None = None) -> None:
    """主要 cookies と任意で認証情報・追加 cookies を upsert。

    credentials・追加 cookies は None のとき既存値を維持する。
    auto_login_enabled も None のとき既存値維持。
    """
    now = _now()
    auto_flag = None if auto_login_enabled is None else (1 if auto_login_enabled else 0)
    existing = await db.fetchone(
        "SELECT id FROM zzz_hoyolab_accounts WHERE uid = ?", (uid,)
    )
    if existing:
        await db.execute(
            "UPDATE zzz_hoyolab_accounts SET region = ?, ltuid_v2 = ?, ltoken_v2 = ?, "
            "nickname = COALESCE(?, nickname), "
            "email = COALESCE(?, email), "
            "password = COALESCE(?, password), "
            "auto_login_enabled = COALESCE(?, auto_login_enabled), "
            "account_mid_v2 = COALESCE(?, account_mid_v2), "
            "account_id_v2 = COALESCE(?, account_id_v2), "
            "cookie_token_v2 = COALESCE(?, cookie_token_v2), "
            "ltmid_v2 = COALESCE(?, ltmid_v2), "
            "updated_at = ? WHERE id = ?",
            (region, ltuid_v2, ltoken_v2, nickname,
             email, password, auto_flag,
             account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2,
             now, existing["id"]),
        )
        return
    await db.execute(
        "INSERT INTO zzz_hoyolab_accounts (uid, region, ltuid_v2, ltoken_v2, "
        "nickname, email, password, auto_login_enabled, "
        "account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, region, ltuid_v2, ltoken_v2, nickname, email, password,
         auto_flag if auto_flag is not None else 0,
         account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2,
         now, now),
    )


async def update_hoyolab_cookies(db, *, uid: str,
                                 ltuid_v2: str, ltoken_v2: str,
                                 account_mid_v2: str | None = None,
                                 account_id_v2: str | None = None,
                                 cookie_token_v2: str | None = None,
                                 ltmid_v2: str | None = None,
                                 error: str | None = None) -> None:
    """自動ログイン成功時に cookies のみ更新（credentials は触らない）。"""
    await db.execute(
        "UPDATE zzz_hoyolab_accounts SET ltuid_v2 = ?, ltoken_v2 = ?, "
        "account_mid_v2 = COALESCE(?, account_mid_v2), "
        "account_id_v2 = COALESCE(?, account_id_v2), "
        "cookie_token_v2 = COALESCE(?, cookie_token_v2), "
        "ltmid_v2 = COALESCE(?, ltmid_v2), "
        "last_auto_login_at = ?, last_auto_login_error = ?, updated_at = ? "
        "WHERE uid = ?",
        (ltuid_v2, ltoken_v2,
         account_mid_v2, account_id_v2, cookie_token_v2, ltmid_v2,
         _now(), error, _now(), uid),
    )


async def record_auto_login_error(db, *, uid: str, error: str) -> None:
    await db.execute(
        "UPDATE zzz_hoyolab_accounts SET last_auto_login_at = ?, "
        "last_auto_login_error = ?, updated_at = ? WHERE uid = ?",
        (_now(), error, _now(), uid),
    )


async def update_hoyolab_synced(db, uid: str) -> None:
    await db.execute(
        "UPDATE zzz_hoyolab_accounts SET last_synced_at = ? WHERE uid = ?",
        (_now(), uid),
    )


async def delete_characters_without_builds(db) -> dict:
    """ビルドが 1 件も無いキャラを削除する（current/preset 両方なし）。

    HoYoLAB 同期で所持キャラのみ current が作られる前提で、
    所持していないシードキャラ行を掃除する用途。

    Returns: {'chars': n, 'deleted_slugs': [...]}
    """
    rows = await db.fetchall(
        "SELECT c.id, c.slug, c.name_ja FROM zzz_characters c "
        "LEFT JOIN zzz_builds b ON b.character_id = c.id "
        "WHERE b.id IS NULL"
    )
    if not rows:
        return {"chars": 0, "deleted": []}
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    await db.execute(
        f"DELETE FROM zzz_characters WHERE id IN ({placeholders})",
        tuple(ids),
    )
    return {
        "chars": len(ids),
        "deleted": [{"slug": r["slug"], "name_ja": r["name_ja"]} for r in rows],
    }


async def reset_hoyolab_synced_data(db) -> dict:
    """HoYoLAB 同期で作られたデータを一掃する（再同期準備用）。

    - 全 current ビルドと build_slots を削除（再同期で復活）
    - 全 disc を削除（HoYoLAB 同期由来が大半。手動登録分があれば失われる点に注意）
    - キャラ自体は削除しない（マスタ刷新後は全キャラが hoyolab-* slug のため、
      削除すると zzz_presets が連鎖喪失する）

    Returns: {'slots': n, 'builds': n, 'discs': n, 'chars': 0}
    """
    # team_slots.build_id が current ビルドを参照していると FK 違反になるので先に NULL 化
    await db.execute(
        "UPDATE zzz_team_slots SET build_id = NULL "
        "WHERE build_id IN (SELECT id FROM zzz_builds WHERE is_current = 1)"
    )
    c = await db.execute_returning_rowcount(
        "DELETE FROM zzz_build_slots WHERE build_id IN "
        "(SELECT id FROM zzz_builds WHERE is_current = 1)"
    )
    b = await db.execute_returning_rowcount(
        "DELETE FROM zzz_builds WHERE is_current = 1"
    )
    # プリセット（is_current=0）の build_slots が disc を参照していると
    # DELETE FROM zzz_discs が FK 違反で失敗するので先に NULL 化
    await db.execute(
        "UPDATE zzz_build_slots SET disc_id = NULL WHERE disc_id IS NOT NULL"
    )
    d = await db.execute_returning_rowcount("DELETE FROM zzz_discs")
    return {"slots": c, "builds": b, "discs": d, "chars": 0}
