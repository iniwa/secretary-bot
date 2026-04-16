"""ComfyUI ワークフロー実行と成果物回収。

- POST /prompt で投入、WebSocket /ws で進捗購読
- 完了時に /history/<prompt_id> から出力ファイル名を取得
- ComfyUI の output ディレクトリから NAS の指定 output_dir へコピー
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

_log = logging.getLogger(__name__)


_PLACEHOLDER_MAP = {
    "positive": "{{POSITIVE}}",
    "negative": "{{NEGATIVE}}",
    "seed": "{{SEED}}",
    "steps": "{{STEPS}}",
    "cfg": "{{CFG}}",
    "sampler_name": "{{SAMPLER}}",
    "scheduler": "{{SCHEDULER}}",
    "width": "{{WIDTH}}",
    "height": "{{HEIGHT}}",
    "ckpt_name": "{{CKPT}}",
    "filename_prefix": "{{FILENAME_PREFIX}}",
    "lora_1": "{{LORA_1}}",
    "lora_1_w": "{{LORA_1_W}}",
}


def substitute_placeholders(workflow: dict, inputs: dict) -> dict:
    """workflow_json 内の `<<POSITIVE>>` / `{{POSITIVE}}` 等を inputs で置換。

    両記法を許容（Pi 側仕様書の例は `<<>>`、プリセット JSON は `{{}}`）。
    値は数値/文字列を問わずそのまま差し込む。ネスト構造は dict/list を再帰。
    """
    # inputs のキーを大文字化して両記法のマップを作る
    repl_map: dict[str, Any] = {}
    for k, v in inputs.items():
        upper = k.upper()
        repl_map[f"<<{upper}>>"] = v
        repl_map[f"{{{{{upper}}}}}"] = v
        # 明示マップにもマッチ
        if k in _PLACEHOLDER_MAP:
            repl_map[_PLACEHOLDER_MAP[k]] = v

    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            # 完全一致ならネイティブ型で返す（数値を文字列化しない）
            if node in repl_map:
                return repl_map[node]
            # 部分置換
            out = node
            for ph, val in repl_map.items():
                if ph in out:
                    out = out.replace(ph, str(val))
            return out
        return node

    return _walk(workflow)


_OUTPUT_KIND_MAP = {
    # ComfyUI history["outputs"] のキー → 媒体種別
    "images":   "image",
    "gifs":     "video",   # ComfyUI の GIF は video として扱う
    "videos":   "video",   # VHS 系が返すキー
    "audio":    "audio",
}

_EXT_TO_KIND = {
    ".png":  "image", ".jpg": "image", ".jpeg": "image",
    ".webp": "image", ".bmp": "image", ".gif":  "image",
    ".mp4":  "video", ".webm": "video", ".mov": "video", ".mkv": "video",
    ".wav":  "audio", ".mp3":  "audio", ".flac": "audio", ".ogg": "audio",
}


def _ext_kind(path: str, default: str = "image") -> str:
    _, ext = os.path.splitext(path or "")
    return _EXT_TO_KIND.get(ext.lower(), default)


@dataclass
class ImageJob:
    job_id: str
    status: str = "queued"     # queued / running / done / failed / cancelled
    progress: int = 0
    comfyui_prompt_id: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result_paths: list[str] = field(default_factory=list)
    result_kinds: list[str] = field(default_factory=list)
    last_error: Optional[dict] = None
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancelled: bool = False
    trace_id: Optional[str] = None

    async def put(self, event: str, data: dict) -> None:
        await self.events.put({"event": event, "data": data})


def _build_output_filename(prefix: str, suffix: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix)
    return f"{safe}{suffix}"


class WorkflowRunner:
    def __init__(self, comfyui_base_url: str, comfyui_root: str, logger=None) -> None:
        self.base_url = comfyui_base_url.rstrip("/")
        self.comfyui_root = comfyui_root
        self._logger = logger
        self._client_id = uuid.uuid4().hex

    async def queue_prompt(
        self,
        workflow: dict,
        client_id: Optional[str] = None,
    ) -> dict:
        cid = client_id or self._client_id
        payload = {"prompt": workflow, "client_id": cid}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self.base_url}/prompt", json=payload)
            if r.status_code != 200:
                raise RuntimeError(f"ComfyUI /prompt rejected {r.status_code}: {r.text}")
            return r.json()

    async def interrupt(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(f"{self.base_url}/interrupt")
            except Exception:
                pass

    async def fetch_history(self, prompt_id: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self.base_url}/history/{prompt_id}")
            if r.status_code != 200:
                return {}
            data = r.json() or {}
            return data.get(prompt_id, {})

    async def run(self, job: ImageJob, workflow: dict, output_dir: str, timeout_sec: int) -> None:
        """workflow をキュー投入し、WS 購読しつつ完了まで待ち、結果を NAS へ回収。"""
        try:
            await self._run_inner(job, workflow, output_dir, timeout_sec)
        except Exception as e:
            tb = traceback.format_exc()
            _log.error("runner.run unexpected failure job=%s: %s\n%s", job.job_id, e, tb)
            if job.status not in ("done", "failed", "cancelled"):
                job.status = "failed"
                job.last_error = {
                    "error_class": "AgentCommunicationError",
                    "message": f"runner unexpected: {e!r}",
                    "retryable": False,
                }
                try:
                    await job.put("error", job.last_error)
                    await job.put("done", {})
                except Exception:
                    pass

    async def _run_inner(self, job: ImageJob, workflow: dict, output_dir: str, timeout_sec: int) -> None:
        try:
            import websockets  # type: ignore
        except ImportError:  # pragma: no cover
            websockets = None

        job.status = "running"
        job.started_at = time.time()
        await job.put("status", {"status": "running"})

        try:
            queue_res = await self.queue_prompt(workflow)
        except Exception as e:
            tb = traceback.format_exc()
            _log.error("queue_prompt failed job=%s: %s\n%s", job.job_id, e, tb)
            job.status = "failed"
            job.last_error = {
                "error_class": "ComfyUIError.WorkflowValidationError",
                "message": f"queue_prompt: {e!r}",
                "retryable": False,
            }
            await job.put("error", job.last_error)
            await job.put("done", {})
            return

        prompt_id = queue_res.get("prompt_id")
        job.comfyui_prompt_id = prompt_id

        # WebSocket で進捗購読
        if websockets is not None:
            ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={self._client_id}"
            deadline = time.time() + timeout_sec
            try:
                async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
                    while True:
                        if job.cancelled:
                            await self.interrupt()
                            job.status = "cancelled"
                            await job.put("status", {"status": "cancelled"})
                            await job.put("done", {})
                            return
                        if time.time() > deadline:
                            await self.interrupt()
                            job.status = "failed"
                            job.last_error = {
                                "error_class": "TransientError",
                                "message": f"timeout after {timeout_sec}s",
                                "retryable": True,
                            }
                            await job.put("error", job.last_error)
                            await job.put("done", {})
                            return
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(msg, bytes):
                            # preview PNG バイナリ（8byte ヘッダ + PNG）。
                            # Phase 1 ではプレビューイベントは転送しない
                            continue
                        try:
                            evt = json.loads(msg)
                        except Exception:
                            continue
                        et = evt.get("type")
                        data = evt.get("data") or {}
                        if data.get("prompt_id") and data["prompt_id"] != prompt_id:
                            continue
                        if et == "progress":
                            value = data.get("value", 0)
                            total = data.get("max", 1) or 1
                            pct = int(min(value * 100 / total, 100))
                            job.progress = pct
                            await job.put("progress", {
                                "percent": pct, "step": value, "total": total,
                                "node_id": str(data.get("node") or ""),
                            })
                        elif et == "executing":
                            if data.get("node") is None:
                                # 完了シグナル
                                break
                        elif et == "execution_error":
                            msg_text = data.get("exception_message") or "ComfyUI execution error"
                            is_oom = "out of memory" in msg_text.lower() or "cuda oom" in msg_text.lower()
                            job.status = "failed"
                            job.last_error = {
                                "error_class": "ComfyUIError.OOMError" if is_oom else "ComfyUIError",
                                "message": msg_text,
                                "retryable": bool(is_oom),
                                "detail": {"node_id": str(data.get("node_id") or "")},
                            }
                            await job.put("error", job.last_error)
                            await job.put("done", {})
                            return
            except Exception as e:
                tb = traceback.format_exc()
                _log.error("ws monitor failed job=%s: %s\n%s", job.job_id, e, tb)
                job.status = "failed"
                job.last_error = {
                    "error_class": "AgentCommunicationError",
                    "message": f"ws error: {e!r}",
                    "retryable": True,
                }
                await job.put("error", job.last_error)
                await job.put("done", {})
                return
        else:
            # websockets ライブラリなし: /history をポーリング
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                if job.cancelled:
                    await self.interrupt()
                    job.status = "cancelled"
                    await job.put("status", {"status": "cancelled"})
                    await job.put("done", {})
                    return
                hist = await self.fetch_history(prompt_id)
                if hist:
                    break
                await asyncio.sleep(2.0)

        # 成果物回収
        try:
            history = await self.fetch_history(prompt_id)
            result_paths, result_kinds = await self._collect_outputs(history, output_dir)
            job.result_paths = result_paths
            job.result_kinds = result_kinds
            job.status = "done"
            job.finished_at = time.time()
            job.progress = 100
            await job.put("result", {
                "result_paths": result_paths,
                "result_kinds": result_kinds,
            })
            await job.put("status", {"status": "done"})
            await job.put("done", {})
        except Exception as e:
            tb = traceback.format_exc()
            _log.error("output collection failed job=%s: %s\n%s", job.job_id, e, tb)
            job.status = "failed"
            job.last_error = {
                "error_class": "ComfyUIError",
                "message": f"output collection failed: {e!r}",
                "retryable": False,
            }
            await job.put("error", job.last_error)
            await job.put("done", {})

    async def _collect_outputs(
        self, history: dict, output_dir: str,
    ) -> tuple[list[str], list[str]]:
        """history 中の出力を探し ComfyUI output から output_dir へコピー。
        戻り値: (paths, kinds) kinds は各ファイルの 'image'/'video'/'audio'。"""
        os.makedirs(output_dir, exist_ok=True)
        outputs = history.get("outputs") or {}
        results: list[str] = []
        kinds: list[str] = []
        comfy_out_root = os.path.join(self.comfyui_root, "output")
        for _node_id, payload in outputs.items():
            for key, base_kind in _OUTPUT_KIND_MAP.items():
                for item in payload.get(key, []) or []:
                    filename = item.get("filename")
                    subfolder = item.get("subfolder", "") or ""
                    if not filename:
                        continue
                    src = os.path.join(comfy_out_root, subfolder, filename)
                    if not os.path.exists(src):
                        continue
                    dst = os.path.join(output_dir, filename)
                    tmp = dst + ".tmp"
                    try:
                        shutil.copy2(src, tmp)
                        os.replace(tmp, dst)
                    except Exception:
                        if os.path.exists(tmp):
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                        continue
                    # 出力セクションが曖昧なら拡張子でフォールバック
                    kind = _ext_kind(filename, default=base_kind)
                    results.append(dst.replace("\\", "/"))
                    kinds.append(kind)
        return results, kinds
