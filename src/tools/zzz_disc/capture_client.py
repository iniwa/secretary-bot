"""Windows Agent /tools/zzz-disc/capture を呼ぶクライアント。

画像は PNG bytes として返す。
Agent 選定は bot.unit_manager.agent_pool.select_agent(preferred="windows") を使う。
"""

from __future__ import annotations

import os
import base64
import httpx


def _headers() -> dict[str, str]:
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    return {"X-Agent-Token": token} if token else {}


async def capture_screenshot(bot, *, source: str = "capture-mss",
                             obs_source_name: str | None = None,
                             crop: dict | None = None,
                             timeout: float = 15.0) -> bytes:
    """Windows Agent に /tools/zzz-disc/capture を叩いて PNG bytes を取得する。"""
    pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if pool is None:
        raise RuntimeError("agent_pool is not available")
    agent = await pool.select_agent(preferred="windows")
    if agent is None:
        raise RuntimeError("no windows agent available")

    url = f"http://{agent['host']}:{agent['port']}/tools/zzz-disc/capture"
    body = {
        "backend": "obs" if source == "capture-obs" else "mss",
        "obs_source_name": obs_source_name,
        "crop": crop,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            data = resp.json()
            b64 = data.get("png_base64")
            if not b64:
                raise RuntimeError("capture response missing png_base64")
            return base64.b64decode(b64)
        return resp.content
