"""Windows Agent /tools/zzz-disc/extract を呼ぶ VLM 抽出クライアント。

画像 bytes を multipart で送って JSON を返してもらう。
`capture-and-extract` も使えるとネットワーク往復が減る。
"""

from __future__ import annotations

import os
import httpx


def _headers() -> dict[str, str]:
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    return {"X-Agent-Token": token} if token else {}


async def extract_from_image(bot, image_bytes: bytes,
                             *, timeout: float = 120.0) -> dict:
    """PNG bytes を送って VLM 抽出結果 JSON を返す。"""
    pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if pool is None:
        raise RuntimeError("agent_pool is not available")
    agent = await pool.select_agent(preferred="windows")
    if agent is None:
        raise RuntimeError("no windows agent available")

    url = f"http://{agent['host']}:{agent['port']}/tools/zzz-disc/extract"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url, headers=_headers(),
            files={"file": ("disc.png", image_bytes, "image/png")},
        )
        resp.raise_for_status()
        return resp.json()


async def capture_and_extract(bot, *, source: str = "capture-mss",
                              timeout: float = 120.0) -> tuple[bytes | None, dict]:
    """画像キャプチャと VLM 抽出を Windows Agent 側で一括実行。

    Returns (png_bytes_or_None, extraction_dict)
    Windows 側で保存不要なら png_bytes は None で返る。
    """
    pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if pool is None:
        raise RuntimeError("agent_pool is not available")
    agent = await pool.select_agent(preferred="windows")
    if agent is None:
        raise RuntimeError("no windows agent available")

    url = f"http://{agent['host']}:{agent['port']}/tools/zzz-disc/capture-and-extract"
    body = {
        "backend": "obs" if source == "capture-obs" else "mss",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        data = resp.json()
    import base64
    b64 = data.get("png_base64")
    png = base64.b64decode(b64) if b64 else None
    return png, data.get("extraction") or {}
