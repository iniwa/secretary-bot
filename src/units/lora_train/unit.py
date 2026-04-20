"""LoRATrainUnit — LoRA プロジェクト管理の Pi 側ユニット。

WebGUI のルートが直接公開メソッドを呼ぶ構造（image_gen と同じパターン）。
"""

from __future__ import annotations

import asyncio

from src.errors import ValidationError
from src.logger import get_logger
from src.units.base_unit import BaseUnit
from src.units.lora_train import nas_io

log = get_logger(__name__)

_DEFAULT_BASE_MODEL = "ChenkinNoob-XL-V0.5.safetensors"
_ALLOWED_STATUSES = ("draft", "ready", "training", "done", "failed")


class LoRATrainUnit(BaseUnit):
    UNIT_NAME = "lora_train"
    UNIT_DESCRIPTION = "LoRA 学習のプロジェクト管理・データセット投入・kohya 学習。"
    DELEGATE_TO = None
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

    def is_dataset_path_safe(self, path: str) -> bool:
        return nas_io.is_inside_dataset_dir(
            path, self._nas_base, self._datasets_subdir,
        )
