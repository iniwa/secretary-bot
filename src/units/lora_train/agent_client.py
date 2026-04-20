"""LoRA 学習系 Agent API クライアント（WD14 タグ付け / sync / 学習起動）。

画像生成 AgentClient とは関心分離のため独立ファイル。Agent 側は
`windows-agent/tools/image_gen/` の同じルーター配下で LoRA 用エンドポイントも
提供するが、Pi では用途別にクライアントを分けておく。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from src.errors import (
    AgentCommunicationError,
    ResourceUnavailableError,
    ValidationError,
)
from src.logger import get_logger, get_trace_id

log = get_logger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


def _map_error(status: int, body: dict | None) -> Exception:
    body = body or {}
    msg = body.get("message") or f"HTTP {status}"
    cls_name = body.get("error_class", "")
    if cls_name == "ValidationError" or status == 400:
        return ValidationError(msg)
    if status in (423, 503, 504):
        return ResourceUnavailableError(msg)
    return AgentCommunicationError(msg)


def _safe_json(resp: httpx.Response) -> dict | None:
    try:
        return resp.json()
    except Exception:
        return None


class LoRATagAgentClient:
    """1 Agent に対する LoRA 関連 HTTP ラッパー。"""

    def __init__(self, agent: dict):
        self.agent = agent
        self.agent_id = agent.get("id", "")
        self.base_url = f"http://{agent['host']}:{agent['port']}"
        self._token = os.environ.get("AGENT_SECRET_TOKEN", "")
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        h = {"X-Agent-Token": self._token}
        tid = get_trace_id()
        if tid:
            h["X-Trace-Id"] = tid
        return h

    def _get(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def tag_start(
        self, *, project_name: str, threshold: float = 0.35,
        repo_id: str | None = None, trigger_word: str | None = None,
    ) -> dict:
        """POST /lora/dataset/tag → 202 {task_id, progress_url, dataset_dir}"""
        payload: dict[str, Any] = {
            "project_name": project_name, "threshold": threshold,
        }
        if repo_id:
            payload["repo_id"] = repo_id
        if trigger_word:
            payload["trigger_word"] = trigger_word
        try:
            resp = await self._get().post(
                "/lora/dataset/tag", json=payload, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora tag_start failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    async def tag_status(self, task_id: str) -> dict:
        try:
            resp = await self._get().get(
                f"/lora/tag/{task_id}", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora tag_status failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    async def sync_start(self, *, project_name: str) -> dict:
        try:
            resp = await self._get().post(
                "/lora/dataset/sync",
                json={"project_name": project_name}, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora sync_start failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    async def sync_status(self, task_id: str) -> dict:
        try:
            resp = await self._get().get(
                f"/lora/sync/{task_id}", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora sync_status failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    # --- Training ---

    async def train_start(self, *, project_name: str) -> dict:
        try:
            resp = await self._get().post(
                "/lora/train/start",
                json={"project_name": project_name}, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora train_start failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    async def train_status(self, task_id: str) -> dict:
        try:
            resp = await self._get().get(
                f"/lora/train/{task_id}", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora train_status failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    async def train_cancel(self, task_id: str) -> dict:
        try:
            resp = await self._get().post(
                f"/lora/train/{task_id}/cancel", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora train_cancel failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error(resp.status_code, _safe_json(resp))
        return resp.json()

    async def train_stream(self, task_id: str, *, after_seq: int = 0):
        """SSE 読み取り: `yield (event, data_dict)`。

        呼び出し側は `async for event, data in client.train_stream(tid)` で使う。
        """
        url = f"/lora/train/{task_id}/stream"
        params = {"after_seq": after_seq} if after_seq else None
        try:
            async with self._get().stream(
                "GET", url, headers=self._headers(), params=params,
                timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            ) as resp:
                if resp.status_code != 200:
                    body: dict | None = None
                    try:
                        body = (await resp.aread()) and _safe_json_bytes(
                            await resp.aread(),
                        )
                    except Exception:
                        pass
                    raise _map_error(resp.status_code, body)
                event = "message"
                data_lines: list[str] = []
                async for raw in resp.aiter_lines():
                    line = raw.rstrip("\r")
                    if not line:
                        if data_lines:
                            data_str = "\n".join(data_lines)
                            try:
                                data = json.loads(data_str)
                            except Exception:
                                data = {"raw": data_str}
                            yield event, data
                        event = "message"
                        data_lines = []
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"lora train_stream failed: {e}") from e


def _safe_json_bytes(b: bytes) -> dict | None:
    import json as _json
    try:
        return _json.loads(b.decode("utf-8", errors="replace"))
    except Exception:
        return None
