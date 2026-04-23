"""楽天 Web Service API 共通 HTTP クライアント（2026 年 2 月新仕様）。

新仕様要件:
- ドメイン: openapi.rakuten.co.jp
- 認証: applicationId（UUID）+ accessKey（pk_...）両方必須
- Referer または Origin ヘッダー必須
- Allow IP 事前登録制
- 1.5 秒以上の間隔推奨
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.errors import (
    RakutenApiError,
    RakutenAuthError,
    RakutenRateLimitError,
    RakutenRefererError,
)
from src.logger import get_logger

log = get_logger(__name__)


@dataclass
class RakutenApiConfig:
    """楽天 API 設定。`.env` と `config.yaml` から組み立てる。"""

    application_id: str
    access_key: str
    referer: str
    affiliate_id: str | None = None
    rate_limit_ms: int = 1500
    timeout_sec: float = 10.0

    @classmethod
    def from_env(cls, *, referer: str, rate_limit_ms: int = 1500) -> RakutenApiConfig:
        """環境変数から設定を読み込む。

        必須: RAKUTEN_APPLICATION_ID, RAKUTEN_ACCESS_KEY
        任意: RAKUTEN_AFFILIATE_ID
        """
        app_id = os.environ.get("RAKUTEN_APPLICATION_ID", "").strip()
        access_key = os.environ.get("RAKUTEN_ACCESS_KEY", "").strip()
        if not app_id or not access_key:
            raise RakutenAuthError(
                ".env に RAKUTEN_APPLICATION_ID と RAKUTEN_ACCESS_KEY を設定してね"
            )
        return cls(
            application_id=app_id,
            access_key=access_key,
            affiliate_id=(os.environ.get("RAKUTEN_AFFILIATE_ID") or "").strip() or None,
            referer=referer,
            rate_limit_ms=int(rate_limit_ms),
        )


class RakutenApiClient:
    """楽天 API 共通 HTTP クライアント（httpx ベース）。

    - 認証クエリと Referer ヘッダーを自動付与
    - 前回リクエストからの最小間隔（既定 1.5s）を `asyncio.Lock` で直列保証
    - HTTP ステータスを専用例外にマップ
    """

    def __init__(self, config: RakutenApiConfig):
        self.config = config
        self._last_request_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_sec),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def request(
        self, endpoint: str, params: dict[str, Any],
        *, include_affiliate: bool = True,
    ) -> dict[str, Any]:
        """楽天 API に GET リクエストし、JSON を dict で返す。"""
        async with self._lock:
            await self._throttle()

            full_params: dict[str, Any] = dict(params)
            full_params["applicationId"] = self.config.application_id
            full_params["accessKey"] = self.config.access_key
            full_params.setdefault("format", "json")
            if include_affiliate and self.config.affiliate_id:
                full_params["affiliateId"] = self.config.affiliate_id

            headers = {
                "Referer": self.config.referer,
                "User-Agent": "ai-mimich-agent/0.1 (+https://github.com/iniwa/ai-mimich-agent)",
            }

            log.info(
                "rakuten_api_request endpoint=%s params=%s",
                endpoint,
                {k: v for k, v in full_params.items()
                 if k not in ("applicationId", "accessKey")},
            )

            try:
                resp = await self._get_client().get(
                    endpoint, params=full_params, headers=headers,
                )
            except httpx.HTTPError as e:
                raise RakutenApiError(f"ネットワークエラー: {e}") from e
            finally:
                self._last_request_ts = time.monotonic()

            self._raise_for_status(resp.status_code, resp.text)
            try:
                return resp.json()
            except ValueError as e:
                raise RakutenApiError(
                    f"レスポンスが JSON でない: {e}",
                    status=resp.status_code, body=resp.text[:200],
                ) from e

    async def _throttle(self) -> None:
        if self._last_request_ts == 0.0:
            return
        elapsed_ms = (time.monotonic() - self._last_request_ts) * 1000
        wait_ms = self.config.rate_limit_ms - elapsed_ms
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)

    @staticmethod
    def _raise_for_status(status: int, body: str) -> None:
        if 200 <= status < 300:
            return
        if status in (400, 401):
            raise RakutenAuthError(
                "認証エラー: accessKey または applicationId を確認してね",
                status=status, body=body[:200],
            )
        if status == 403:
            raise RakutenRefererError(
                "Referer 不足 or Allow IP 不一致: 楽天管理画面の IP を確認してね",
                status=status, body=body[:200],
            )
        if status == 429:
            raise RakutenRateLimitError(
                "レート超過: 1.5 秒以上間隔を空けて再試行してね",
                status=status, body=body[:200],
            )
        raise RakutenApiError(
            f"楽天 API HTTP {status}", status=status, body=body[:200],
        )
