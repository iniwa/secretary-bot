"""AgentClient — Windows Agent の clip_pipeline ルーター向け HTTP / SSE ラッパー。

Agent 側は `/clip-pipeline/*` の prefix でルーター登録されている想定。
`image_gen` の AgentClient と方式を揃えているが、Whisper モデル同期と
ジョブ起動 API が分離しているため専用クラスにしている。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.errors import (
    AgentCommunicationError,
    CacheSyncError,
    ClipPipelineError,
    HighlightError,
    ResourceUnavailableError,
    TranscribeError,
    TransientError,
    ValidationError,
    WhisperError,
)
from src.logger import get_logger, get_trace_id

log = get_logger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_SSE_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)

_PREFIX = "/clip-pipeline"


_ERROR_CLASS_MAP: dict[str, type] = {
    "ValidationError": ValidationError,
    "ClipPipelineError": ClipPipelineError,
    "WhisperError": WhisperError,
    "TranscribeError": TranscribeError,
    "HighlightError": HighlightError,
    "CacheSyncError": CacheSyncError,
    "ResourceUnavailableError": ResourceUnavailableError,
    "TransientError": TransientError,
    "AgentCommunicationError": AgentCommunicationError,
}


def _map_error_response(status: int, body: dict | None) -> Exception:
    """Agent のエラーレスポンスを ClipPipeline 例外階層へマップ。"""
    if body is None:
        body = {}
    cls_name = body.get("error_class", "")
    msg = body.get("message") or f"HTTP {status}"
    cls = _ERROR_CLASS_MAP.get(cls_name)
    if cls is None:
        if status == 400:
            cls = ValidationError
        elif status == 423:
            cls = ResourceUnavailableError
        elif status in (503, 504):
            cls = ResourceUnavailableError
        elif status == 500:
            cls = ClipPipelineError
        else:
            cls = AgentCommunicationError
    return cls(msg)


class AgentClient:
    """1 エージェントに対する clip_pipeline 通信ラッパー。"""

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

    # --- capability ---

    async def capability(self) -> dict:
        """GET /clip-pipeline/capability。
        期待レスポンス:
          {gpu_info: {...}, whisper_models_local: [...], whisper_models_nas: [...],
           ffmpeg_version: str, cuda_compute: str, busy: bool}
        """
        try:
            resp = await self._get_client().get(
                f"{_PREFIX}/capability", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"clip_pipeline capability request failed: {e}"
            ) from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def inputs(self) -> dict:
        """GET /clip-pipeline/inputs。{base: str, files: list[dict]} を返す。"""
        try:
            resp = await self._get_client().get(
                f"{_PREFIX}/inputs", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"clip_pipeline inputs request failed: {e}"
            ) from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def outputs_edl(self, stem: str) -> dict:
        """GET /clip-pipeline/outputs/{stem}/edl。
        outputs_base/{stem}/timeline.edl の内容を返す。
        レスポンス: {stem, path, content, size}
        """
        try:
            resp = await self._get_client().get(
                f"{_PREFIX}/outputs/{stem}/edl", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"clip_pipeline outputs_edl request failed: {e}"
            ) from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    # --- whisper cache sync ---

    async def whisper_cache_sync(
        self, *, model: str, sha256: str | None = None,
    ) -> dict:
        """POST /clip-pipeline/whisper/cache-sync。
        NAS の Whisper モデルを Agent ローカル SSD に同期する要求を投げる。
        期待レスポンス: {sync_id: str}
        """
        payload: dict[str, Any] = {"model": model}
        if sha256:
            payload["sha256"] = sha256
        try:
            resp = await self._get_client().post(
                f"{_PREFIX}/whisper/cache-sync",
                json=payload, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"whisper_cache_sync request failed: {e}"
            ) from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def whisper_cache_sync_stream(self, sync_id: str) -> AsyncIterator[dict]:
        """SSE 購読。`{progress, log, done, error}` 系イベントが流れる。"""
        url = f"{self.base_url}{_PREFIX}/whisper/cache-sync/{sync_id}/events"
        async for ev in _sse_stream(url, self._headers()):
            yield ev

    # --- job lifecycle ---

    async def job_start(
        self, *, job_id: str, video_path: str, output_dir: str,
        whisper_model: str, ollama_model: str,
        params: dict[str, Any] | None = None,
        timeout_sec: int | None = None,
    ) -> dict:
        """POST /clip-pipeline/jobs/start。202 想定。
        video_path / output_dir は Agent から見える NAS UNC パス（例: `N:\\auto-kirinuki\\...`）
        """
        payload: dict[str, Any] = {
            "job_id": job_id,
            "video_path": video_path,
            "output_dir": output_dir,
            "whisper_model": whisper_model,
            "ollama_model": ollama_model,
            "params": params or {},
        }
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        try:
            resp = await self._get_client().post(
                f"{_PREFIX}/jobs/start",
                json=payload, headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(
                f"job_start request failed: {e}"
            ) from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def job_get(self, job_id: str) -> dict:
        try:
            resp = await self._get_client().get(
                f"{_PREFIX}/jobs/{job_id}", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"job_get failed: {e}") from e
        if resp.status_code != 200:
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()

    async def job_stream(self, job_id: str) -> AsyncIterator[dict]:
        """SSE 購読。step/progress/log/result/error イベントを流す。"""
        url = f"{self.base_url}{_PREFIX}/jobs/{job_id}/events"
        async for ev in _sse_stream(url, self._headers()):
            yield ev

    async def job_cancel(self, job_id: str) -> dict:
        try:
            resp = await self._get_client().post(
                f"{_PREFIX}/jobs/{job_id}/cancel", headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"job_cancel failed: {e}") from e
        if resp.status_code not in (200, 202):
            raise _map_error_response(resp.status_code, _safe_json(resp))
        return resp.json()


def _safe_json(resp: httpx.Response) -> dict | None:
    try:
        return resp.json()
    except Exception:
        return None


async def _sse_stream(url: str, headers: dict) -> AsyncIterator[dict]:
    """最小 SSE クライアント。`image_gen.agent_client._sse_stream` と同じ挙動。"""
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
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_buf.append(line[5:].lstrip())
        except httpx.HTTPError as e:
            raise AgentCommunicationError(f"SSE stream failed: {e}") from e
