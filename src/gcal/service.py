"""Google Calendar API サービスファクトリ（読み書き共通）。"""

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

# events スコープは読み書き両対応のため、読み取り専用スコープは追加不要
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_DEFAULT_SA_FILE = "/app/data/service_account.json"


def get_sa_file_path() -> str:
    return os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", _DEFAULT_SA_FILE)


def get_service_account_email() -> str | None:
    path = get_sa_file_path()
    try:
        with open(path) as f:
            return json.load(f).get("client_email")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_calendar_service():
    """Calendar API サービスオブジェクトを構築する。

    FileNotFoundError を送出するのは、呼び出し側で判別可能にするため。
    """
    path = get_sa_file_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"サービスアカウントファイルが見つかりません: {path}"
        )
    with open(path) as f:
        creds_data = json.load(f)
    creds = service_account.Credentials.from_service_account_info(
        creds_data, scopes=_SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
