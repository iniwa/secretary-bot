"""LoRA プロジェクト・データセット・学習ジョブ関連のDBメソッド。"""

from src.database._base import jst_now


class LoRAMixin:
    # === LoRA: projects / dataset / train jobs ===

    async def lora_project_create(
        self, *, name: str, description: str | None = None,
        dataset_path: str | None = None, base_model: str | None = None,
        config_json: str | None = None, status: str = "draft",
        output_path: str | None = None,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO lora_projects "
            "(name, description, dataset_path, base_model, config_json, "
            " status, output_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description, dataset_path, base_model, config_json,
             status, output_path, jst_now(), jst_now()),
        )
        return int(cur.lastrowid or 0)

    async def lora_project_get(self, project_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM lora_projects WHERE id = ?", (project_id,),
        )

    async def lora_project_get_by_name(self, name: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM lora_projects WHERE name = ?", (name,),
        )

    async def lora_project_list(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.fetchall(
                "SELECT * FROM lora_projects WHERE status = ? "
                "ORDER BY updated_at DESC", (status,),
            )
        return await self.fetchall(
            "SELECT * FROM lora_projects ORDER BY updated_at DESC",
        )

    async def lora_project_update(
        self, project_id: int, *,
        description: str | None = None, dataset_path: str | None = None,
        base_model: str | None = None, config_json: str | None = None,
        status: str | None = None, output_path: str | None = None,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        for col, val in [
            ("description", description), ("dataset_path", dataset_path),
            ("base_model", base_model), ("config_json", config_json),
            ("status", status), ("output_path", output_path),
        ]:
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(jst_now())
        params.append(project_id)
        await self.execute(
            f"UPDATE lora_projects SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return True

    async def lora_project_delete(self, project_id: int) -> None:
        await self.execute(
            "DELETE FROM lora_dataset_items WHERE project_id = ?", (project_id,),
        )
        await self.execute(
            "DELETE FROM lora_train_jobs WHERE project_id = ?", (project_id,),
        )
        await self.execute(
            "DELETE FROM lora_projects WHERE id = ?", (project_id,),
        )

    async def lora_dataset_item_insert(
        self, *, project_id: int, image_path: str,
        caption: str | None = None, tags: str | None = None,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO lora_dataset_items "
            "(project_id, image_path, caption, tags) VALUES (?, ?, ?, ?)",
            (project_id, image_path, caption, tags),
        )
        return int(cur.lastrowid or 0)

    async def lora_dataset_item_get(self, item_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM lora_dataset_items WHERE id = ?", (item_id,),
        )

    async def lora_dataset_item_list(
        self, project_id: int, *, reviewed_only: bool = False,
    ) -> list[dict]:
        if reviewed_only:
            return await self.fetchall(
                "SELECT * FROM lora_dataset_items "
                "WHERE project_id = ? AND reviewed_at IS NOT NULL "
                "ORDER BY id ASC", (project_id,),
            )
        return await self.fetchall(
            "SELECT * FROM lora_dataset_items WHERE project_id = ? "
            "ORDER BY id ASC", (project_id,),
        )

    async def lora_dataset_item_update(
        self, item_id: int, *,
        caption: str | None = None, tags: str | None = None,
        mark_reviewed: bool = False,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        if caption is not None:
            sets.append("caption = ?")
            params.append(caption)
        if tags is not None:
            sets.append("tags = ?")
            params.append(tags)
        if mark_reviewed:
            sets.append("reviewed_at = ?")
            params.append(jst_now())
        if not sets:
            return False
        params.append(item_id)
        await self.execute(
            f"UPDATE lora_dataset_items SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return True

    async def lora_dataset_item_delete(self, item_id: int) -> None:
        await self.execute(
            "DELETE FROM lora_dataset_items WHERE id = ?", (item_id,),
        )

    async def lora_train_job_insert(
        self, *, project_id: int, tb_logdir: str | None = None,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO lora_train_jobs "
            "(project_id, status, progress, tb_logdir) "
            "VALUES (?, 'queued', 0, ?)",
            (project_id, tb_logdir),
        )
        return int(cur.lastrowid or 0)

    async def lora_train_job_get(self, job_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM lora_train_jobs WHERE id = ?", (job_id,),
        )

    async def lora_train_job_list(
        self, project_id: int | None = None, limit: int = 50,
    ) -> list[dict]:
        if project_id is not None:
            return await self.fetchall(
                "SELECT * FROM lora_train_jobs WHERE project_id = ? "
                "ORDER BY id DESC LIMIT ?", (project_id, limit),
            )
        return await self.fetchall(
            "SELECT * FROM lora_train_jobs ORDER BY id DESC LIMIT ?", (limit,),
        )

    async def lora_train_job_update(
        self, job_id: int, *,
        status: str | None = None, progress: int | None = None,
        sample_images: str | None = None, error_message: str | None = None,
        set_started: bool = False, set_finished: bool = False,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if progress is not None:
            sets.append("progress = ?")
            params.append(progress)
        if sample_images is not None:
            sets.append("sample_images = ?")
            params.append(sample_images)
        if error_message is not None:
            sets.append("error_message = ?")
            params.append(error_message)
        if set_started:
            sets.append("started_at = ?")
            params.append(jst_now())
        if set_finished:
            sets.append("finished_at = ?")
            params.append(jst_now())
        if not sets:
            return False
        params.append(job_id)
        await self.execute(
            f"UPDATE lora_train_jobs SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return True
