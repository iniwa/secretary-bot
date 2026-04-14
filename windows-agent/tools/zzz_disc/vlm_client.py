"""Ollama VLM（gemma4 等）呼び出し。"""
from __future__ import annotations

import base64
import json

import httpx

from .prompts import EXTRACT_PROMPT


async def extract(
    image_bytes: bytes,
    model: str = "gemma4",
    ollama_url: str = "http://localhost:11434",
) -> dict:
    """画像を Ollama VLM に投げて JSON 抽出結果を返す。

    Raises:
        ValueError: レスポンス JSON がパースできない場合。
        httpx.HTTPError: Ollama へのリクエストが失敗した場合。
    """
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "prompt": EXTRACT_PROMPT,
        "images": [img_b64],
        "stream": False,
        "format": "json",
    }
    url = ollama_url.rstrip("/") + "/api/generate"

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()

    raw = data.get("response", "")
    if not raw:
        raise ValueError("Ollama returned empty response")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse VLM JSON response: {e}; raw={raw[:200]}")
