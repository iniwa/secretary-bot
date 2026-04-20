"""HoYoLAB 自動ログイン。

email / password から `ltuid_v2` / `ltoken_v2` 等の cookies を取得し DB に保存する。
genshin.py の `Client.login_with_password` を利用。

参考: sf6-logs/services/cfn_auth.py と同様の「資格情報を保存して定期リフレッシュ」方針。

captcha（GeeTest）が発生した場合は `CaptchaRequired` を投げる。
Pi のような headless 環境では captcha 突破のためのブラウザ UI は出せないため、
ユーザー側でブラウザから手動 cookie を登録するフォールバックに回す。
"""
from __future__ import annotations

from src.logger import get_logger

from . import models

log = get_logger(__name__)


class HoyolabLoginError(Exception):
    """ログイン失敗（一般）。"""


class InvalidCredentials(HoyolabLoginError):
    """email / password が誤り。再試行しても通らない。"""


class CaptchaRequired(HoyolabLoginError):
    """GeeTest captcha が要求された。Pi では自動突破不可。"""


def _load_genshin():
    try:
        import genshin  # type: ignore
        from genshin import errors as gerrors  # type: ignore
        return genshin, gerrors
    except ImportError as e:
        raise HoyolabLoginError(
            "genshin.py is not installed. Add 'genshin>=1.7,<2.0' to requirements.txt"
        ) from e


async def auto_login(email: str, password: str, *,
                     region: str = "overseas") -> dict:
    """email/password で HoYoLAB にログインし cookies を取得。

    Args:
        region: "overseas" もしくは "cn"。ZZZ 国際版は overseas。

    Returns:
        {ltuid_v2, ltoken_v2, ltmid_v2, cookie_token_v2, account_mid_v2, account_id_v2}
    """
    genshin, gerrors = _load_genshin()
    if not email or not password:
        raise InvalidCredentials("email / password が未設定です")

    # region を Client に渡す
    region_enum = getattr(genshin.Region, "OVERSEAS", None)
    if region == "cn":
        region_enum = getattr(genshin.Region, "CHINESE", region_enum)

    client = genshin.Client(region=region_enum) if region_enum else genshin.Client()

    try:
        # encrypted=False の場合、genshin.py 側で miHoYo 公開鍵で RSA 暗号化してくれる。
        # geetest_solver=None のまま呼ぶと、captcha が必要な場合に port 5000 で
        # webserver を立ち上げようとするため、事前に captcha 判定を挟みたいが
        # 簡易にするため raise されたら CaptchaRequired に翻訳する方針にする。
        result = await client.login_with_password(email, password)
    except gerrors.AccountDoesNotExist as e:
        raise InvalidCredentials(f"アカウントが存在しません: {e}") from e
    except gerrors.AccountLoginFail as e:
        raise InvalidCredentials(f"パスワードが不正です: {e}") from e
    except gerrors.IncorrectGamePassword as e:
        raise InvalidCredentials(f"パスワードが不正です: {e}") from e
    except gerrors.IncorrectGameAccount as e:
        raise InvalidCredentials(f"アカウントが不正です: {e}") from e
    except gerrors.AccountHasLocked as e:
        raise HoyolabLoginError(f"アカウントがロックされています: {e}") from e
    except (gerrors.GeetestError, gerrors.GeetestFailed,
            gerrors.DailyGeetestTriggered) as e:
        raise CaptchaRequired(
            f"captcha が要求されました（Pi 環境では突破不可）: {e}"
        ) from e
    except Exception as e:
        # port 5000 webserver 起動系の例外もここで捕まる
        msg = str(e).lower()
        if "geetest" in msg or "captcha" in msg or "port" in msg:
            raise CaptchaRequired(f"captcha 関連でログインに失敗: {e}") from e
        raise HoyolabLoginError(f"ログインに失敗: {e}") from e

    # result は WebLoginResult（属性: ltuid_v2, ltoken_v2, ltmid_v2, cookie_token_v2, account_mid_v2, account_id_v2）
    cookies = {
        "ltuid_v2": getattr(result, "ltuid_v2", "") or "",
        "ltoken_v2": getattr(result, "ltoken_v2", "") or "",
        "ltmid_v2": getattr(result, "ltmid_v2", "") or "",
        "cookie_token_v2": getattr(result, "cookie_token_v2", "") or "",
        "account_mid_v2": getattr(result, "account_mid_v2", "") or "",
        "account_id_v2": getattr(result, "account_id_v2", "") or "",
    }
    if not cookies["ltoken_v2"] or not cookies["ltuid_v2"]:
        raise HoyolabLoginError("ログイン成功応答に ltoken_v2/ltuid_v2 が含まれません")
    log.info("HoYoLAB auto-login OK: ltuid=%s", cookies["ltuid_v2"])
    return cookies


async def refresh_account_cookies(db, account: dict) -> dict:
    """保存済み email/password で再ログインし DB の cookies を更新。

    Returns: 更新後 cookies dict
    Raises: HoyolabLoginError 系
    """
    email = account.get("email")
    password = account.get("password")
    uid = account.get("uid")
    if not email or not password:
        raise InvalidCredentials("保存された email/password がありません")
    try:
        cookies = await auto_login(email, password)
    except HoyolabLoginError as e:
        try:
            await models.record_auto_login_error(db, uid=uid, error=str(e))
        except Exception:
            pass
        raise
    await models.update_hoyolab_cookies(
        db, uid=uid,
        ltuid_v2=cookies["ltuid_v2"],
        ltoken_v2=cookies["ltoken_v2"],
        account_mid_v2=cookies.get("account_mid_v2"),
        account_id_v2=cookies.get("account_id_v2"),
        cookie_token_v2=cookies.get("cookie_token_v2"),
        ltmid_v2=cookies.get("ltmid_v2"),
        error=None,
    )
    log.info("HoYoLAB cookies refreshed for uid=%s", uid)
    return cookies
