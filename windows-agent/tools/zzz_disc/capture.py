"""画面キャプチャ（mss / OBS WebSocket）。同期実装。"""
from __future__ import annotations

import base64
import io
from typing import Optional


def capture_mss(monitor: int = 1, crop: Optional[dict] = None) -> bytes:
    """指定モニタをスクショして PNG bytes で返す。

    Args:
        monitor: mss のモニタ番号（0=全体, 1以降=各モニタ）。既定 1。
        crop: {"top": int, "left": int, "width": int, "height": int} で指定すると Pillow でトリミング。
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        if monitor < 0 or monitor >= len(sct.monitors):
            monitor = 1 if len(sct.monitors) > 1 else 0
        shot = sct.grab(sct.monitors[monitor])
        img = Image.frombytes("RGB", shot.size, shot.rgb)

    if crop:
        left = int(crop.get("left", 0))
        top = int(crop.get("top", 0))
        width = int(crop.get("width", img.width - left))
        height = int(crop.get("height", img.height - top))
        img = img.crop((left, top, left + width, top + height))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def capture_obs(host: str, port: int, password: str, source_name: str) -> bytes:
    """OBS WebSocket でソース画面を取得して PNG bytes で返す。"""
    from obswebsocket import obsws, requests as obsreq  # type: ignore

    ws = obsws(host, port, password)
    ws.connect()
    try:
        resp = ws.call(obsreq.GetSourceScreenshot(
            sourceName=source_name,
            imageFormat="png",
        ))
        data_uri = resp.getImageData()  # "data:image/png;base64,..."
    finally:
        ws.disconnect()

    if not data_uri:
        raise RuntimeError("OBS returned empty screenshot")
    if "," in data_uri:
        b64 = data_uri.split(",", 1)[1]
    else:
        b64 = data_uri
    return base64.b64decode(b64)
