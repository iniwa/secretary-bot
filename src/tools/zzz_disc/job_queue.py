"""ジョブキューワーカー。

- `asyncio.Queue` に job_id を積み、`max_concurrent` 個のワーカーで捌く
- 起動時に DB から未完了ジョブを復元
- ジョブ状態の変化は `subscribers` に登録された `asyncio.Queue` へ push（SSE 用）
- ジョブ失敗は `status="failed"` + `error_message` 記録、UIから再試行可能
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from src.logger import get_logger
from . import models
from . import normalizer
from .capture_client import capture_screenshot
from .extractor import extract_from_image, capture_and_extract

log = get_logger(__name__)


class ZzzDiscJobQueue:
    def __init__(self, bot, *, max_concurrent: int = 1,
                 history_retention: int = 200,
                 images_dir: str = "/app/data/zzz_disc_images",
                 use_capture_and_extract: bool = True):
        self.bot = bot
        self.db = bot.database
        self.max_concurrent = max_concurrent
        self.history_retention = history_retention
        self.images_dir = images_dir
        self.use_capture_and_extract = use_capture_and_extract
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._subscribers: set[asyncio.Queue] = set()
        os.makedirs(self.images_dir, exist_ok=True)

    # ---------------- Pub/Sub ----------------

    def subscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.add(q)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ---------------- Queue ops ----------------

    async def enqueue(self, job_id: int) -> None:
        await self._queue.put(job_id)
        await self.publish({"type": "enqueue", "job_id": job_id})

    async def start(self) -> None:
        # 起動時復元
        pending = await models.list_jobs_to_resume(self.db)
        for j in pending:
            # capturing/extracting は途中で落ちた可能性あるので queued に戻す
            if j["status"] != "queued":
                await models.update_job(self.db, j["id"], status="queued")
            await self._queue.put(j["id"])

        for i in range(self.max_concurrent):
            task = asyncio.create_task(self._worker_loop(i), name=f"zzz-disc-worker-{i}")
            self._workers.append(task)
        log.info("ZzzDiscJobQueue started: %d worker(s), %d resumed",
                 self.max_concurrent, len(pending))

    async def stop(self) -> None:
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()

    # ---------------- Worker ----------------

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._process_job(job_id)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.exception("worker_loop error for job %s: %s", job_id, e)
                try:
                    await models.update_job(self.db, job_id,
                                            status="failed", error_message=str(e))
                    await self.publish({"type": "update", "job_id": job_id, "status": "failed"})
                except Exception:
                    pass
            finally:
                try:
                    await models.prune_finished_jobs(self.db, self.history_retention)
                except Exception:
                    pass

    async def _process_job(self, job_id: int) -> None:
        job = await models.get_job(self.db, job_id)
        if not job:
            return
        source = job["source"]

        # upload 経由は画像が既にある
        if source == "upload":
            image_path = job["image_path"]
            if not image_path or not os.path.exists(image_path):
                raise RuntimeError(f"uploaded image not found: {image_path}")
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            await self._set_status(job_id, "extracting")
            extraction = await extract_from_image(self.bot, image_bytes)
        else:
            # capture-mss / capture-obs
            if self.use_capture_and_extract:
                await self._set_status(job_id, "capturing")
                png, extraction = await capture_and_extract(self.bot, source=source)
                if png is not None:
                    image_path = self._save_image(png)
                    await models.update_job(self.db, job_id, image_path=image_path)
            else:
                await self._set_status(job_id, "capturing")
                png = await capture_screenshot(self.bot, source=source)
                image_path = self._save_image(png)
                await models.update_job(self.db, job_id, image_path=image_path)
                await self._set_status(job_id, "extracting")
                extraction = await extract_from_image(self.bot, png)

        # セット名正規化
        sets = await models.list_set_masters(self.db)
        normalized = normalizer.normalize_extraction(extraction, sets)

        await models.update_job(
            self.db, job_id,
            status="ready",
            extracted_json=extraction,
            normalized_json=normalized,
        )
        await self.publish({"type": "update", "job_id": job_id, "status": "ready"})

    async def _set_status(self, job_id: int, status: str) -> None:
        await models.update_job(self.db, job_id, status=status)
        await self.publish({"type": "update", "job_id": job_id, "status": status})

    def _save_image(self, png_bytes: bytes) -> str:
        name = f"cap_{uuid.uuid4().hex}.png"
        path = os.path.join(self.images_dir, name)
        with open(path, "wb") as f:
            f.write(png_bytes)
        return path
