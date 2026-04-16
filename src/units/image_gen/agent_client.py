"""AgentClient — Windows Agent との HTTP / SSE 通信ラッパー。"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from src.errors import (
    AgentCommunicationError, ComfyUIError, OOMError,
    CacheSyncError, ResourceUnavailableError, TransientError,
    ValidationError, WorkflowValidationError,
)
from src.logger import get_logger, get_trace_id

log = get_logger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_SSE_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)


_ERROR_CLASS_MAP: dict[str, type] = {
    "ValidationError": ValidationError,
    "WorkflowValidationError": WorkflowValidationError,
    "ComfyUIError": ComfyUIError,
    "ComfyUIError.OOMError": OOMError,
    "OOMError": OOMError,
    "CacheSyncError": CacheSyncError,
    "ResourceUnavailableError": ResourceUnavailableError,
    "TransientError": TransientError,
    "AgentCommunicationError": AgentCommunicationError,
}


def _map_error_response(status: int, body: dict | None) -> Exception:
    """Agent のエラーレスポンスを ImageGenError 階層へマップ。"""
    if body is None:
        body = {}
    cls_name = body.get("error_class", "")
    msg = body.get("message") or f"HTTP {status}"
    cls = _ERROR_CLASS_MAP.get(cls_name)
    if cls is None:
        # フォールバック: ステータスコードから推定
        if status == 400:
            cls = ValidationError
        elif status == 423:
            cls = ResourceUnavailableError
        elif status in (503, 504):
            cls = ResourceUnavailableError
        elif status == 500:
            cls = ComfyUIError
        else:
            cls = AgentCommunicationError
    return cls(msg)


class AgentClient:
    """1 エージェントに対する通信ラッパー。使い回し可能な httpx client を内部保持。"""

    def __init__(self, agent: dict):
        self.agent = agent
        self.agent_id = agent.get("id", "")
        self.base_url = f"http://{agent['host']}:{agent['port']}"
        self._token = os.environ.get("AGENT_SECRET_TOKEN", "")
        self._client: httpx.AsyncClient | None = None

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        h = {"X-Agent-Token": self._token}
        tid = get_trace_id()
        if tid:
            h["X-Trace-Id"] = tid
        if extra:
            h.update(extra)
        return h

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # --- capability / health ---

    async def capability(self) -> dict:
        try:
            resp = await self._get_client().get("/capability", headers=self._headers())
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"capability request failed: {e}"
            ) from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    # --- image generate ---

    async def image_generate(
        self, *, job_id: str, workflow_json: dict,
        inputs: dict[str, Any] | None = None,
        timeout_sec: int | None = None,
        required_models: list[dict] | None = None,
    ) -> dict:
        """POST /image/generate。202 を返す想定。"""
        payload = {
            "job_id": job_id,
            "workflow_json": workflow_json,
            "inputs": inputs or {},
        }
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        if required_models:
            payload["required_models"] = required_models
        try:
            resp = await self._get_client().post(
                "/image/generate", json=payload, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"image_generate request failed: {e}"
            ) from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def image_job_status(self, job_id: str) -> dict:
        try:
            resp = await self._get_client().get(
                f"/image/jobs/{job_id}", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"image_job_status failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def image_job_cancel(self, job_id: str) -> dict:
        try:
            resp = await self._get_client().post(
                f"/image/jobs/{job_id}/cancel", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"image_job_cancel failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def image_job_stream(self, job_id: str) -> AsyncIterator[dict]:
        """SSE 購読。イベントごとに {event, data} を yield。"""
        url = f"{self.base_url}/image/jobs/{job_id}/stream"
        async for ev in _sse_stream(url, self._headers()):
            yield ev

    # --- generation_* (image/video/audio 汎用。Phase 3.5 以降の推奨 API) ---

    async def generation_submit(
        self, *, job_id: str, workflow_json: dict,
        inputs: dict[str, Any] | None = None,
        timeout_sec: int | None = None,
        required_models: list[dict] | None = None,
        modality: str = "image",
    ) -> dict:
        """POST /generation/submit。image_generate と同じく 202 を返す。
        サーバ側は image_generate と共通ハンドラで処理される。"""
        payload: dict[str, Any] = {
            "job_id": job_id,
            "workflow_json": workflow_json,
            "inputs": inputs or {},
            "modality": modality,
        }
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        if required_models:
            payload["required_models"] = required_models
        try:
            resp = await self._get_client().post(
                "/generation/submit", json=payload, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"generation_submit request failed: {e}"
            ) from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def generation_job_status(self, job_id: str) -> dict:
        try:
            resp = await self._get_client().get(
                f"/generation/jobs/{job_id}", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"generation_job_status failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def generation_job_cancel(self, job_id: str) -> dict:
        try:
            resp = await self._get_client().post(
                f"/generation/jobs/{job_id}/cancel", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"generation_job_cancel failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def generation_job_stream(self, job_id: str) -> AsyncIterator[dict]:
        url = f"{self.base_url}/generation/jobs/{job_id}/stream"
        async for ev in _sse_stream(url, self._headers()):
            yield ev

    # --- cache sync ---

    async def cache_manifest(self) -> dict:
        try:
            resp = await self._get_client().get(
                "/cache/manifest", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"cache_manifest failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def cache_sync(self, files: list[dict], *, reason: str = "") -> dict:
        payload = {"files": files, "reason": reason, "verify_sha256": True}
        try:
            resp = await self._get_client().post(
                "/cache/sync", json=payload, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"cache_sync failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def cache_sync_stream(self, sync_id: str) -> AsyncIterator[dict]:
        url = f"{self.base_url}/cache/sync/{sync_id}/stream"
        async for ev in _sse_stream(url, self._headers()):
            yield ev

    async def cache_sync_cancel(self, sync_id: str) -> dict:
        try:
            resp = await self._get_client().post(
                f"/cache/sync/{sync_id}/cancel", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"cache_sync_cancel failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()


def _safe_json(resp: httpx.Response) -> dict | None:
    try:
        return resp.json()
    except Exception:
        return None


async def _sse_stream(url: str, headers: dict) -> AsyncIterator[dict]:
    """最小 SSE クライアント。`event:` と `data:` のペアで dict を yield する。

    keepalive 行（`: ...`）は無視。data は JSON 想定だがパース失敗なら raw で包む。
    """
    async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    body = None
                    try:
                        body = json.loads(await resp.aread())
                    except Exception:
                        pass
                    raise _map_error_response(resp.status_code, body)
                event_name: str | None = None
                data_buf: list[str] = []
                async for line in resp.aiter_lines():
                    if line is None:
                        continue
                    if line == "":
                        if data_buf:
                            raw = "\n".join(data_buf)
                            try:
                                parsed = json.loads(raw)
                            except Exception:
                                parsed = {"raw": raw}
                            yield {"event": event_name or "message", "data": parsed}
                        event_name = None
                        data_buf = []
                        continue
                    if line.startswith(":"):
                        continue  # keepalive
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_buf.append(line[5:].lstrip())
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"SSE stream failed: {e}") from e
