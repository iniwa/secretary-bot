"""LoRATrainUnit — LoRA プロジェクト管理の Pi 側ユニット。

WebGUI のルートが直接公開メソッドを呼ぶ構造（image_gen と同じパターン）。
"""

from __future__ import annotations

import asyncio
import os
import time

from src.errors import ValidationError
from src.logger import get_logger
from src.units.base_unit import BaseUnit
from src.units.lora_train import nas_io, toml_builder
from src.units.lora_train.agent_client import LoRATagAgentClient

log = get_logger(__name__)

_DEFAULT_BASE_MODEL = "ChenkinNoob-XL-V0.5.safetensors"
_ALLOWED_STATUSES = ("draft", "ready", "training", "done", "failed")
_TAG_POLL_INTERVAL_SEC = 3.0
_TAG_TIMEOUT_SEC = 3600


class LoRATrainUnit(BaseUnit):
    UNIT_NAME = "lora_train"
    UNIT_DESCRIPTION = "LoRA 学習のプロジェクト管理・データセット投入・kohya 学習。"
    DELEGATE_TO = None
    CHAT_ROUTABLE = False
    AUTONOMY_TIER = 4
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        cfg = (bot.config.get("units") or {}).get(self.UNIT_NAME) or {}
        self._default_base_model = cfg.get("default_base_model") or _DEFAULT_BASE_MODEL
        nas = (bot.config.get("units") or {}).get("image_gen", {}).get("nas") or {}
        self._nas_base = nas.get("base_path") or "/mnt/ai-image"
        self._datasets_subdir = nas.get("lora_datasets_subdir") or "lora_datasets"
        self._work_subdir = nas.get("lora_work_subdir") or "lora_work"
        self._loras_subdir = nas.get("loras_subdir") or "models/loras"
        # 進行中の WD14 タグ付けタスク（task_id -> snapshot）。
        # Agent 側の状態を Pi でもキャッシュし、完了時に DB 反映まで面倒を見る。
        self._tagging_tasks: dict[str, dict] = {}
        # Agent 側 dataset sync タスクのミラー（task_id -> snapshot）
        self._sync_tasks: dict[str, dict] = {}
        # Agent 側 kohya 学習タスクのミラー（task_id -> snapshot + DB job_id）
        self._training_tasks: dict[str, dict] = {}
        # LoRA 学習用ベースモデルの Agent 側絶対パス（agent_config.yaml で上書き推奨）
        self._agent_base_model_dir = (
            cfg.get("agent_base_model_dir")
            or "C:/secretary-bot-cache/models/checkpoints"
        )

    async def create_project(
        self, *, name: str, description: str | None = None,
        base_model: str | None = None,
    ) -> dict:
        validated = nas_io.validate_project_name(name)
        existing = await self.bot.database.lora_project_get_by_name(validated)
        if existing:
            raise ValidationError(f"project '{validated}' already exists")

        dataset_path = nas_io.ensure_dataset_dir(
            self._nas_base, self._datasets_subdir, validated,
        )
        work = nas_io.ensure_work_dirs(
            self._nas_base, self._work_subdir, validated,
        )
        pid = await self.bot.database.lora_project_create(
            name=validated,
            description=description,
            dataset_path=dataset_path,
            base_model=base_model or self._default_base_model,
            output_path=work["checkpoints"],
            status="draft",
        )
        log.info("lora project created: id=%s name=%s", pid, validated)
        return await self.bot.database.lora_project_get(pid)

    async def list_projects(self, status: str | None = None) -> list[dict]:
        return await self.bot.database.lora_project_list(status=status)

    async def get_project(self, project_id: int) -> dict | None:
        return await self.bot.database.lora_project_get(project_id)

    async def update_project(
        self, project_id: int, *,
        description: str | None = None, base_model: str | None = None,
        status: str | None = None,
    ) -> dict:
        existing = await self.bot.database.lora_project_get(project_id)
        if not existing:
            raise ValidationError(f"project {project_id} not found")
        if status is not None and status not in _ALLOWED_STATUSES:
            raise ValidationError(
                f"status must be one of {_ALLOWED_STATUSES}, got '{status}'",
            )
        await self.bot.database.lora_project_update(
            project_id,
            description=description,
            base_model=base_model,
            status=status,
        )
        return await self.bot.database.lora_project_get(project_id)

    async def delete_project(self, project_id: int, *, purge_files: bool = True) -> None:
        existing = await self.bot.database.lora_project_get(project_id)
        if not existing:
            raise ValidationError(f"project {project_id} not found")
        if purge_files:
            nas_io.remove_project_dirs(
                self._nas_base, self._datasets_subdir, self._work_subdir,
                existing["name"],
            )
        await self.bot.database.lora_project_delete(project_id)
        log.info("lora project deleted: id=%s name=%s", project_id, existing["name"])

    async def open_dataset_dir(self, project_id: int) -> tuple[dict, str]:
        """バッチ追加の前準備：プロジェクト存在確認＋ NAS dir 確保（1回だけ呼ぶ）。"""
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")
        target_dir = await asyncio.to_thread(
            nas_io.ensure_dataset_dir,
            self._nas_base, self._datasets_subdir, project["name"],
        )
        return project, target_dir

    async def add_dataset_item(
        self, project_id: int, target_dir: str,
        *, filename: str | None, content: bytes,
    ) -> dict | None:
        """画像 1 枚を NAS へ書き出し、`lora_dataset_items` 行を作る。

        ループ呼び出し前提で `target_dir` を引数化（NAS への makedirs を1回に抑える）。
        ファイル書き込みは `asyncio.to_thread` でイベントループから外す。
        """
        ext = nas_io.normalize_image_ext(filename)
        path = await asyncio.to_thread(
            nas_io.write_dataset_image, target_dir, ext, content,
        )
        item_id = await self.bot.database.lora_dataset_item_insert(
            project_id=project_id, image_path=path,
        )
        return await self.bot.database.lora_dataset_item_get(item_id)

    async def list_dataset_items(
        self, project_id: int, *, reviewed_only: bool = False,
    ) -> list[dict]:
        return await self.bot.database.lora_dataset_item_list(
            project_id, reviewed_only=reviewed_only,
        )

    async def get_dataset_item(self, item_id: int) -> dict | None:
        return await self.bot.database.lora_dataset_item_get(item_id)

    async def delete_dataset_item(self, item_id: int) -> None:
        row = await self.bot.database.lora_dataset_item_get(item_id)
        if not row:
            raise ValidationError(f"dataset item {item_id} not found")
        await asyncio.to_thread(nas_io.remove_dataset_file, row["image_path"])
        await self.bot.database.lora_dataset_item_delete(item_id)

    async def update_dataset_item(
        self, item_id: int, *,
        caption: str | None = None,
        tags: str | None = None,
        mark_reviewed: bool = False,
        sync_caption_file: bool = True,
    ) -> dict:
        """caption / tags を更新し、オプションで NAS の `<image>.txt` も書き換える。

        kohya 学習は `.txt` を読むため、tags 更新時はデフォルトで caption ファイルも
        書き換える（caption 明示指定時は caption を、未指定で tags のみなら tags を
        そのまま caption として書く）。
        """
        row = await self.bot.database.lora_dataset_item_get(item_id)
        if not row:
            raise ValidationError(f"dataset item {item_id} not found")

        await self.bot.database.lora_dataset_item_update(
            item_id, caption=caption, tags=tags, mark_reviewed=mark_reviewed,
        )

        if sync_caption_file and (tags is not None or caption is not None):
            caption_to_write = caption if caption is not None else tags
            if caption_to_write is not None:
                image_path = row.get("image_path") or ""
                if image_path and nas_io.is_inside_dataset_dir(
                    image_path, self._nas_base, self._datasets_subdir,
                ):
                    await asyncio.to_thread(
                        _write_caption_file, image_path, caption_to_write,
                    )

        return await self.bot.database.lora_dataset_item_get(item_id)

    def is_dataset_path_safe(self, path: str) -> bool:
        return nas_io.is_inside_dataset_dir(
            path, self._nas_base, self._datasets_subdir,
        )

    # === WD14 タグ付け ===

    async def start_tagging(
        self, project_id: int, *,
        threshold: float = 0.35,
        repo_id: str | None = None,
        prepend_trigger: bool = True,
        agent_id: str | None = None,
    ) -> dict:
        """Agent を選定して WD14 タグ付けをキックする。"""
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")

        pool = getattr(self.bot.unit_manager, "agent_pool", None)
        if pool is None:
            raise ValidationError("agent_pool not available")
        agent = await pool.select_agent(preferred=agent_id)
        if agent is None:
            raise ValidationError("no kohya-capable agent available")

        client = LoRATagAgentClient(agent)
        try:
            trigger_word = project["name"] if prepend_trigger else None
            resp = await client.tag_start(
                project_name=project["name"],
                threshold=threshold,
                repo_id=repo_id,
                trigger_word=trigger_word,
            )
        finally:
            await client.close()

        task_id = resp.get("task_id") or ""
        if not task_id:
            raise ValidationError(
                f"agent did not return task_id: {resp!r}",
            )
        entry = {
            "task_id": task_id,
            "agent_id": agent.get("id"),
            "agent": agent,
            "project_id": project_id,
            "project_name": project["name"],
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "current_step": resp.get("kind") or "wd14_tagging",
            "log_tail": [],
            "db_updated": 0,
        }
        self._tagging_tasks[task_id] = entry
        asyncio.create_task(self._poll_tagging(task_id))
        log.info(
            "lora wd14 tagging started: project=%s task_id=%s agent=%s",
            project["name"], task_id, agent.get("id"),
        )
        return entry

    def get_tagging_task(self, task_id: str) -> dict | None:
        entry = self._tagging_tasks.get(task_id)
        if entry is None:
            return None
        # agent dict は公開しない（内部の host/port を漏らさないため）
        return {k: v for k, v in entry.items() if k != "agent"}

    def list_tagging_tasks(self, project_id: int | None = None) -> list[dict]:
        out = []
        for entry in self._tagging_tasks.values():
            if project_id is not None and entry["project_id"] != project_id:
                continue
            out.append({k: v for k, v in entry.items() if k != "agent"})
        out.sort(key=lambda e: e["started_at"], reverse=True)
        return out

    async def _poll_tagging(self, task_id: str) -> None:
        entry = self._tagging_tasks.get(task_id)
        if entry is None:
            return
        agent = entry["agent"]
        client = LoRATagAgentClient(agent)
        deadline = time.time() + _TAG_TIMEOUT_SEC
        try:
            while True:
                await asyncio.sleep(_TAG_POLL_INTERVAL_SEC)
                try:
                    snap = await client.tag_status(task_id)
                except Exception as e:
                    entry["error"] = f"poll failed: {e}"
                    log.warning("tag poll failed: %s", e)
                    if time.time() > deadline:
                        entry["status"] = "failed"
                        entry["finished_at"] = time.time()
                        return
                    continue
                entry["status"] = snap.get("status") or entry["status"]
                entry["current_step"] = snap.get("current_step") or ""
                entry["error"] = snap.get("error")
                entry["log_tail"] = snap.get("log_tail") or []
                entry["finished_at"] = snap.get("finished_at")
                if entry["status"] in ("done", "failed"):
                    break
                if time.time() > deadline:
                    entry["status"] = "failed"
                    entry["error"] = "timeout"
                    entry["finished_at"] = time.time()
                    return
        finally:
            await client.close()

        if entry["status"] == "done":
            try:
                updated = await self._apply_tags_from_nas(entry["project_id"])
                entry["db_updated"] = updated
                log.info(
                    "lora wd14 tags applied: project_id=%s updated=%d",
                    entry["project_id"], updated,
                )
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = f"db update failed: {e}"
                log.exception("tag db update failed")

    # === TOML / dataset sync (Phase E) ===

    async def prepare_project(
        self, project_id: int, *,
        dataset_overrides: dict | None = None,
        config_overrides: dict | None = None,
    ) -> dict:
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")
        base_model_name = project.get("base_model") or self._default_base_model
        base_model_path = (
            self._agent_base_model_dir.rstrip("/\\") + "/" + base_model_name
        )
        produced = await asyncio.to_thread(
            toml_builder.prepare_project_files,
            self._nas_base, self._datasets_subdir, self._work_subdir,
            project,
            base_model_path=base_model_path,
            dataset_overrides=dataset_overrides,
            config_overrides=config_overrides,
        )
        log.info("lora project prepared: id=%s files=%s", project_id, produced)
        return produced

    async def start_sync(
        self, project_id: int, *, agent_id: str | None = None,
    ) -> dict:
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")

        pool = getattr(self.bot.unit_manager, "agent_pool", None)
        if pool is None:
            raise ValidationError("agent_pool not available")
        agent = await pool.select_agent(preferred=agent_id)
        if agent is None:
            raise ValidationError("no kohya-capable agent available")

        client = LoRATagAgentClient(agent)
        try:
            resp = await client.sync_start(project_name=project["name"])
        finally:
            await client.close()

        task_id = resp.get("task_id") or ""
        if not task_id:
            raise ValidationError(f"agent did not return task_id: {resp!r}")
        entry = {
            "task_id": task_id,
            "agent_id": agent.get("id"),
            "agent": agent,
            "project_id": project_id,
            "project_name": project["name"],
            "status": "running",
            "local_dirs": resp.get("local_dirs") or {},
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "current_step": resp.get("kind") or "lora_sync",
            "log_tail": [],
        }
        self._sync_tasks[task_id] = entry
        asyncio.create_task(self._poll_sync(task_id))
        log.info(
            "lora dataset sync started: project=%s task_id=%s agent=%s",
            project["name"], task_id, agent.get("id"),
        )
        return entry

    async def _poll_sync(self, task_id: str) -> None:
        entry = self._sync_tasks.get(task_id)
        if entry is None:
            return
        agent = entry["agent"]
        client = LoRATagAgentClient(agent)
        deadline = time.time() + 1800
        try:
            while True:
                await asyncio.sleep(3.0)
                try:
                    snap = await client.sync_status(task_id)
                except Exception as e:
                    entry["error"] = f"poll failed: {e}"
                    if time.time() > deadline:
                        entry["status"] = "failed"
                        entry["finished_at"] = time.time()
                        return
                    continue
                entry["status"] = snap.get("status") or entry["status"]
                entry["current_step"] = snap.get("current_step") or ""
                entry["error"] = snap.get("error")
                entry["log_tail"] = snap.get("log_tail") or []
                entry["finished_at"] = snap.get("finished_at")
                if entry["status"] in ("done", "failed"):
                    return
                if time.time() > deadline:
                    entry["status"] = "failed"
                    entry["error"] = "timeout"
                    entry["finished_at"] = time.time()
                    return
        finally:
            await client.close()

    def get_sync_task(self, task_id: str) -> dict | None:
        entry = self._sync_tasks.get(task_id)
        if entry is None:
            return None
        return {k: v for k, v in entry.items() if k != "agent"}

    def list_sync_tasks(self, project_id: int | None = None) -> list[dict]:
        out = []
        for entry in self._sync_tasks.values():
            if project_id is not None and entry["project_id"] != project_id:
                continue
            out.append({k: v for k, v in entry.items() if k != "agent"})
        out.sort(key=lambda e: e["started_at"], reverse=True)
        return out

    # === Training (Phase F) ===

    async def start_training(
        self, project_id: int, *, agent_id: str | None = None,
    ) -> dict:
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")

        pool = getattr(self.bot.unit_manager, "agent_pool", None)
        if pool is None:
            raise ValidationError("agent_pool not available")
        agent = await pool.select_agent(preferred=agent_id)
        if agent is None:
            raise ValidationError("no kohya-capable agent available")

        client = LoRATagAgentClient(agent)
        try:
            resp = await client.train_start(project_name=project["name"])
        finally:
            await client.close()

        task_id = resp.get("task_id") or ""
        if not task_id:
            raise ValidationError(f"agent did not return task_id: {resp!r}")

        job_id = await self.bot.database.lora_train_job_insert(
            project_id=project_id, tb_logdir=None,
        )
        await self.bot.database.lora_train_job_update(
            job_id, status="running", set_started=True,
        )
        await self.bot.database.lora_project_update(
            project_id, status="training",
        )

        entry = {
            "task_id": task_id,
            "job_id": job_id,
            "agent_id": agent.get("id"),
            "agent": agent,
            "project_id": project_id,
            "project_name": project["name"],
            "status": "running",
            "current_step": resp.get("kind") or "lora_train",
            "step": 0, "total_steps": 0,
            "epoch": 0, "total_epochs": 0,
            "last_loss": None,
            "latest_sample": None,
            "latest_checkpoint": None,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "log_tail": [],
        }
        self._training_tasks[task_id] = entry
        asyncio.create_task(self._poll_training(task_id))
        log.info(
            "lora training started: project=%s task_id=%s job_id=%s agent=%s",
            project["name"], task_id, job_id, agent.get("id"),
        )
        return {k: v for k, v in entry.items() if k != "agent"}

    async def _poll_training(self, task_id: str) -> None:
        entry = self._training_tasks.get(task_id)
        if entry is None:
            return
        agent = entry["agent"]
        client = LoRATagAgentClient(agent)
        deadline = time.time() + 60 * 60 * 8  # 8h hard cap
        try:
            while True:
                await asyncio.sleep(5.0)
                try:
                    snap = await client.train_status(task_id)
                except Exception as e:
                    entry["error"] = f"poll failed: {e}"
                    if time.time() > deadline:
                        entry["status"] = "failed"
                        entry["finished_at"] = time.time()
                        await self._finalize_training(entry)
                        return
                    continue
                for k in (
                    "status", "current_step", "step", "total_steps",
                    "epoch", "total_epochs", "last_loss",
                    "latest_sample", "latest_checkpoint", "error",
                    "finished_at",
                ):
                    if k in snap:
                        entry[k] = snap.get(k)
                entry["log_tail"] = snap.get("log_tail") or []
                progress = 0
                if entry.get("total_steps"):
                    progress = int(
                        entry["step"] * 100 / entry["total_steps"],
                    )
                try:
                    await self.bot.database.lora_train_job_update(
                        entry["job_id"], progress=progress,
                    )
                except Exception:
                    log.exception("train progress db update failed")
                if entry["status"] in ("done", "failed", "cancelled"):
                    await self._finalize_training(entry)
                    return
                if time.time() > deadline:
                    entry["status"] = "failed"
                    entry["error"] = "timeout"
                    entry["finished_at"] = time.time()
                    await self._finalize_training(entry)
                    return
        finally:
            await client.close()

    async def _finalize_training(self, entry: dict) -> None:
        job_id = entry.get("job_id")
        status = entry.get("status") or "failed"
        try:
            await self.bot.database.lora_train_job_update(
                job_id,
                status=status,
                error_message=entry.get("error"),
                sample_images=entry.get("latest_sample"),
                set_finished=True,
            )
        except Exception:
            log.exception("train finalize db update failed")
        # プロジェクト側ステータスも反映
        next_status = {
            "done": "done", "failed": "failed", "cancelled": "ready",
        }.get(status, "ready")
        try:
            await self.bot.database.lora_project_update(
                entry["project_id"], status=next_status,
            )
        except Exception:
            log.exception("train finalize project update failed")

    async def cancel_training(self, task_id: str) -> dict:
        entry = self._training_tasks.get(task_id)
        if entry is None:
            raise ValidationError(f"task {task_id} not found")
        agent = entry["agent"]
        client = LoRATagAgentClient(agent)
        try:
            resp = await client.train_cancel(task_id)
        finally:
            await client.close()
        return resp

    def get_training_task(self, task_id: str) -> dict | None:
        entry = self._training_tasks.get(task_id)
        if entry is None:
            return None
        return {k: v for k, v in entry.items() if k != "agent"}

    def list_training_tasks(self, project_id: int | None = None) -> list[dict]:
        out = []
        for entry in self._training_tasks.values():
            if project_id is not None and entry["project_id"] != project_id:
                continue
            out.append({k: v for k, v in entry.items() if k != "agent"})
        out.sort(key=lambda e: e["started_at"], reverse=True)
        return out

    def get_training_agent(self, task_id: str) -> dict | None:
        entry = self._training_tasks.get(task_id)
        if entry is None:
            return None
        return entry["agent"]

    # === Promotion (Phase H) ===

    async def list_checkpoints(self, project_id: int) -> list[dict]:
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")
        return await asyncio.to_thread(
            nas_io.list_checkpoints,
            self._nas_base, self._work_subdir, project["name"],
        )

    async def promote_checkpoint(
        self, project_id: int, *,
        checkpoint_filename: str,
        target_filename: str | None = None,
    ) -> dict:
        project = await self.bot.database.lora_project_get(project_id)
        if not project:
            raise ValidationError(f"project {project_id} not found")
        dst = await asyncio.to_thread(
            nas_io.promote_checkpoint,
            self._nas_base, self._work_subdir, self._loras_subdir,
            project["name"],
            checkpoint_filename=checkpoint_filename,
            target_filename=target_filename,
        )
        await self.bot.database.lora_project_update(
            project_id, output_path=dst, status="done",
        )
        log.info(
            "lora checkpoint promoted: project=%s file=%s -> %s",
            project["name"], checkpoint_filename, dst,
        )
        return {"promoted_to": dst, "checkpoint": checkpoint_filename}

    async def _apply_tags_from_nas(self, project_id: int) -> int:
        """NAS 上の <image>.txt を読んで lora_dataset_items.tags を更新する。"""
        items = await self.bot.database.lora_dataset_item_list(project_id)
        updated = 0
        for it in items:
            image_path = it.get("image_path") or ""
            if not image_path:
                continue
            caption_path = os.path.splitext(image_path)[0] + ".txt"
            if not await asyncio.to_thread(os.path.exists, caption_path):
                continue
            try:
                body = await asyncio.to_thread(_read_text, caption_path)
            except Exception as e:
                log.warning("read caption failed: %s: %s", caption_path, e)
                continue
            body = body.strip()
            if not body:
                continue
            await self.bot.database.lora_dataset_item_update(
                it["id"], tags=body,
            )
            updated += 1
        return updated


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_caption_file(image_path: str, body: str) -> None:
    caption_path = os.path.splitext(image_path)[0] + ".txt"
    with open(caption_path, "w", encoding="utf-8") as f:
        f.write(body.strip())


