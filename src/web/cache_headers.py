"""WebGUI / 同居ツール共通の no-cache ヘッダヘルパ。

Cloudflare Tunnel 経由で配信した際に Edge / ブラウザのどちらにも古いアセットが
キャッシュされないようにする。`Cache-Control` (RFC 標準) と `CDN-Cache-Control`
(Cloudflare などの CDN 専用 directive) の両方を付与する。
"""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles

# JS / CSS / HTML すべてに適用する no-cache 指示。
# - no-cache: cache はしてよいが必ず origin で revalidate する
# - must-revalidate: stale を一切返さない
# - max-age=0: 互換のため明示
NO_CACHE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache, must-revalidate, max-age=0",
    "CDN-Cache-Control": "no-cache, must-revalidate, max-age=0",
}


class NoCacheStaticFiles(StaticFiles):
    """すべての静的ファイル応答に no-cache ヘッダを強制する StaticFiles。"""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        for k, v in NO_CACHE_HEADERS.items():
            response.headers[k] = v
        return response
