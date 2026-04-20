"""LoRATrainUnit — LoRA プロジェクト管理の Pi 側ユニット。

WebGUI のルートが直接公開メソッドを呼ぶ構造（image_gen と同じパターン）。
"""

from __future__ import annotations

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
