"""画像/動画/音声生成ジョブ・ワークフロー関連のDBメソッド。"""

from src.database._base import jst_now


class GenerationJobMixin:
    # === 画像生成: workflows ===

    async def workflow_upsert(
        self, *, name: str, description: str | None = None,
        category: str = "t2i", workflow_json: str,
        required_nodes: str | None = None,
        required_models: str | None = None,
        required_loras: str | None = None,
        main_pc_only: bool = False, starred: bool = False,
        default_timeout_sec: int = 300,
    ) -> int:
        """プリセットを name UNIQUE で upsert し、id を返す。"""
        await self.execute(
            "INSERT INTO workflows "
            "(name, description, category, workflow_json, required_nodes, "
            " required_models, required_loras, main_pc_only, starred, "
            " default_timeout_sec, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            " description=excluded.description, category=excluded.category, "
            " workflow_json=excluded.workflow_json, "
            " required_nodes=excluded.required_nodes, "
            " required_models=excluded.required_models, "
            " required_loras=excluded.required_loras, "
            " main_pc_only=excluded.main_pc_only, "
            " default_timeout_sec=excluded.default_timeout_sec, "
            " updated_at=excluded.updated_at",
            (name, description, category, workflow_json, required_nodes,
             required_models, required_loras, 1 if main_pc_only else 0,
             1 if starred else 0, default_timeout_sec, jst_now(), jst_now()),
        )
        row = await self.fetchone("SELECT id FROM workflows WHERE name = ?", (name,))
        return int(row["id"]) if row else 0

    async def workflow_get_by_name(self, name: str) -> dict | None:
        return await self.fetchone("SELECT * FROM workflows WHERE name = ?", (name,))

    async def workflow_get(self, workflow_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM workflows WHERE id = ?", (workflow_id,))

    async def workflow_list(self, category: str | None = None) -> list[dict]:
        if category:
            return await self.fetchall(
                "SELECT * FROM workflows WHERE category = ? ORDER BY starred DESC, name ASC",
                (category,),
            )
        return await self.fetchall(
            "SELECT * FROM workflows ORDER BY starred DESC, category ASC, name ASC"
        )

    # === 画像/動画/音声生成ジョブ: generation_jobs ===
    # 旧称 image_jobs は VIEW として残存（読み取り専用）。
    # 旧メソッド名 image_job_* はこのセクション末尾で薄いエイリアスを定義している。

    async def generation_job_insert(
        self, *, user_id: str, platform: str,
        workflow_id: int | None, positive: str | None,
        negative: str | None, params_json: str,
        modality: str = "image",
        sections_json: str | None = None,
        priority: int = 0, max_retries: int = 2,
    ) -> str:
        """ジョブを queued で登録し、UUID を返す。"""
        import uuid
        job_id = uuid.uuid4().hex
        await self.execute(
            "INSERT INTO generation_jobs "
            "(id, user_id, platform, workflow_id, modality, sections_json, "
            " positive, negative, params_json, status, priority, max_retries, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, user_id, platform, workflow_id, modality, sections_json,
             positive, negative, params_json, priority, max_retries, jst_now()),
        )
        await self._generation_job_event(
            job_id=job_id, from_status=None, to_status="queued",
            agent_id=None, detail_json=None,
        )
        return job_id

    async def generation_job_get(self, job_id: str) -> dict | None:
        return await self.fetchone("SELECT * FROM generation_jobs WHERE id = ?", (job_id,))

    async def generation_job_list(
        self, user_id: str | None = None, status: str | None = None,
        modality: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if modality:
            conditions.append("modality = ?")
            params.append(modality)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM generation_jobs{where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    async def generation_job_claim_queued(self) -> dict | None:
        """楽観ロックで 1 件 queued → dispatching へ遷移させ、該当行を返す。

        設計書の UPDATE 文に準拠:
          WHERE status='queued'
            AND (next_attempt_at IS NULL OR next_attempt_at <= now)

        時刻列は全て JST 文字列で保存されるため（jst_now / _future）、
        SQL の比較も `datetime('now', '+9 hours')` で JST に揃える。
        """
        row = await self.fetchone(
            "SELECT id FROM generation_jobs "
            "WHERE status = 'queued' "
            "  AND (next_attempt_at IS NULL "
            "       OR next_attempt_at <= datetime('now', '+9 hours')) "
            "ORDER BY priority DESC, created_at ASC LIMIT 1"
        )
        if not row:
            return None
        job_id = row["id"]
        rowcount = await self.execute_returning_rowcount(
            "UPDATE generation_jobs "
            "SET status = 'dispatching', "
            "    dispatcher_lock_at = ?, "
            "    timeout_at = datetime('now', '+9 hours', '+30 seconds') "
            "WHERE id = ? AND status = 'queued' "
            "  AND (next_attempt_at IS NULL "
            "       OR next_attempt_at <= datetime('now', '+9 hours'))",
            (jst_now(), job_id),
        )
        if rowcount != 1:
            return None  # 他 worker に取られた
        await self._generation_job_event(
            job_id=job_id, from_status="queued", to_status="dispatching",
            agent_id=None, detail_json=None,
        )
        return await self.generation_job_get(job_id)

    async def generation_job_update_status(
        self, job_id: str, to_status: str,
        expected_from: str | None = None,
        **fields,
    ) -> bool:
        """status を UPDATE し generation_job_events に記録する。
        expected_from 指定時は from チェック付きで更新される（race 回避）。
        """
        allowed = {
            "assigned_agent", "progress", "error_message", "result_paths", "result_kinds",
            "retry_count", "last_error", "cache_sync_id", "next_attempt_at",
            "dispatcher_lock_at", "timeout_at", "started_at", "finished_at",
        }
        sets: list[str] = ["status = ?"]
        params: list = [to_status]
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = ?")
            params.append(v)
        where_sql = "id = ?"
        params.append(job_id)
        if expected_from is not None:
            where_sql += " AND status = ?"
            params.append(expected_from)
        rowcount = await self.execute_returning_rowcount(
            f"UPDATE generation_jobs SET {', '.join(sets)} WHERE {where_sql}",
            tuple(params),
        )
        if rowcount == 1:
            detail = {k: v for k, v in fields.items() if k in allowed}
            import json as _json
            await self._generation_job_event(
                job_id=job_id, from_status=expected_from, to_status=to_status,
                agent_id=fields.get("assigned_agent"),
                detail_json=_json.dumps(detail, ensure_ascii=False) if detail else None,
            )
            return True
        return False

    async def generation_job_update_progress(self, job_id: str, progress: int) -> None:
        """progress のみ更新（デバウンスは呼び出し側で制御）。"""
        await self.execute(
            "UPDATE generation_jobs SET progress = ? WHERE id = ?",
            (int(progress), job_id),
        )

    async def generation_job_set_result(
        self, job_id: str, result_paths_json: str,
        result_kinds_json: str | None = None,
    ) -> None:
        if result_kinds_json is None:
            await self.execute(
                "UPDATE generation_jobs SET result_paths = ? WHERE id = ?",
                (result_paths_json, job_id),
            )
        else:
            await self.execute(
                "UPDATE generation_jobs SET result_paths = ?, result_kinds = ? WHERE id = ?",
                (result_paths_json, result_kinds_json, job_id),
            )

    async def generation_job_find_timed_out(self) -> list[dict]:
        """timeout_at < now の非終端ジョブを返す。
        timeout_at は JST 文字列で書かれるので比較も JST に揃える。
        """
        return await self.fetchall(
            "SELECT * FROM generation_jobs "
            "WHERE status NOT IN ('done', 'failed', 'cancelled') "
            "  AND timeout_at IS NOT NULL "
            "  AND timeout_at < datetime('now', '+9 hours') "
            "ORDER BY created_at ASC"
        )

    async def generation_job_set_favorite(self, job_id: str, favorite: bool) -> bool:
        rc = await self.execute_returning_rowcount(
            "UPDATE generation_jobs SET favorite = ? WHERE id = ?",
            (1 if favorite else 0, job_id),
        )
        return rc > 0

    async def generation_job_set_tags(self, job_id: str, tags_json: str | None) -> bool:
        rc = await self.execute_returning_rowcount(
            "UPDATE generation_jobs SET tags = ? WHERE id = ?",
            (tags_json, job_id),
        )
        return rc > 0

    async def generation_job_cancel(self, job_id: str) -> bool:
        """非終端状態のジョブを cancelled に遷移させる。"""
        row = await self.generation_job_get(job_id)
        if not row:
            return False
        if row["status"] in ("done", "failed", "cancelled"):
            return False
        ok = await self.generation_job_update_status(
            job_id, "cancelled",
            expected_from=row["status"],
            finished_at=jst_now(),
        )
        return ok

    async def _generation_job_event(
        self, *, job_id: str, from_status: str | None, to_status: str,
        agent_id: str | None, detail_json: str | None,
    ) -> None:
        await self.execute(
            "INSERT INTO generation_job_events "
            "(job_id, from_status, to_status, agent_id, detail_json, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, from_status, to_status, agent_id, detail_json, jst_now()),
        )

    async def generation_job_events_list(self, job_id: str) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM generation_job_events "
            "WHERE job_id = ? ORDER BY occurred_at ASC, id ASC",
            (job_id,),
        )

    # --- 旧 image_job_* の後方互換エイリアス（Phase 3.5 移行期間中） ---
    # 既存呼び出し箇所（dispatcher / unit / web / agent_client 由来の文字列ログなど）が
    # 順次 generation_job_* に切り替わるまでの繋ぎ。modality は常に 'image' 固定。

    async def image_job_insert(self, **kwargs) -> str:
        kwargs.setdefault("modality", "image")
        return await self.generation_job_insert(**kwargs)

    async def image_job_get(self, job_id: str) -> dict | None:
        return await self.generation_job_get(job_id)

    async def image_job_list(
        self, user_id: str | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        return await self.generation_job_list(
            user_id=user_id, status=status, modality="image",
            limit=limit, offset=offset,
        )

    async def image_job_claim_queued(self) -> dict | None:
        return await self.generation_job_claim_queued()

    async def image_job_update_status(
        self, job_id: str, to_status: str,
        expected_from: str | None = None, **fields,
    ) -> bool:
        return await self.generation_job_update_status(
            job_id, to_status, expected_from=expected_from, **fields,
        )

    async def image_job_update_progress(self, job_id: str, progress: int) -> None:
        await self.generation_job_update_progress(job_id, progress)

    async def image_job_set_result(self, job_id: str, result_paths_json: str) -> None:
        await self.generation_job_set_result(job_id, result_paths_json)

    async def image_job_find_timed_out(self) -> list[dict]:
        return await self.generation_job_find_timed_out()

    async def image_job_cancel(self, job_id: str) -> bool:
        return await self.generation_job_cancel(job_id)

    async def image_job_events_list(self, job_id: str) -> list[dict]:
        return await self.generation_job_events_list(job_id)
