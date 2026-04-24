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
        is_nsfw: bool = False,
    ) -> str:
        """ジョブを queued で登録し、UUID を返す。"""
        import uuid
        job_id = uuid.uuid4().hex
        await self.execute(
            "INSERT INTO generation_jobs "
            "(id, user_id, platform, workflow_id, modality, sections_json, "
            " positive, negative, params_json, status, priority, max_retries, is_nsfw, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (job_id, user_id, platform, workflow_id, modality, sections_json,
             positive, negative, params_json, priority, max_retries,
             1 if is_nsfw else 0, jst_now()),
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
        nsfw: bool | None = None,
        favorite_only: bool = False,
        q: str | None = None,
        tags_all: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        workflow_name: str | None = None,
        collection_id: int | None = None,
        order: str = "new",
    ) -> list[dict]:
        """generation_jobs の絞り込み + 検索 + 並び替え。

        - q: positive/negative/tags への部分一致（スペース区切り AND）
        - tags_all: すべて含むタグ配列（JSON 文字列への LIKE）
        - date_from/date_to: finished_at の範囲（JST "YYYY-MM-DD" 想定、inclusive）
        - workflow_name: 参照する workflows.name（JOIN）
        - collection_id: image_collection_items に存在するジョブのみ
        - order: 'new' (finished_at DESC) / 'old' (ASC) / 'fav' (favorite DESC, finished_at DESC)
        """
        conditions: list[str] = []
        params: list = []
        join = ""
        if workflow_name:
            join = " JOIN workflows w ON w.id = generation_jobs.workflow_id"
            conditions.append("w.name = ?")
            params.append(workflow_name)
        if collection_id is not None:
            join += " JOIN image_collection_items ci ON ci.job_id = generation_jobs.id"
            conditions.append("ci.collection_id = ?")
            params.append(int(collection_id))
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if modality:
            conditions.append("modality = ?")
            params.append(modality)
        if nsfw is not None:
            conditions.append("is_nsfw = ?")
            params.append(1 if nsfw else 0)
        if favorite_only:
            conditions.append("favorite = 1")
        if date_from:
            conditions.append("finished_at >= ?")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            conditions.append("finished_at <= ?")
            params.append(f"{date_to} 23:59:59")
        if q:
            for token in [t for t in q.split() if t]:
                like = f"%{token}%"
                conditions.append(
                    "(positive LIKE ? OR negative LIKE ? OR tags LIKE ?)"
                )
                params.extend([like, like, like])
        if tags_all:
            for t in tags_all:
                conditions.append("tags LIKE ?")
                # JSON 配列なので ["tag"] 境界込みで検索
                params.append(f'%"{t}"%')
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        order_sql = {
            "new": "finished_at DESC, created_at DESC",
            "old": "finished_at ASC, created_at ASC",
            "fav": "favorite DESC, finished_at DESC",
            # Jobs タブ用: 投入時刻順。進行中（finished_at=NULL）を末尾送りにしない。
            "created_desc": "created_at DESC",
        }.get(order, "finished_at DESC, created_at DESC")
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT generation_jobs.* FROM generation_jobs{join}{where} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            tuple(params),
        )

    async def generation_job_delete(self, job_id: str) -> bool:
        """ジョブ 1 件を物理削除。events と collection_items も CASCADE 相当で削除。"""
        await self.execute(
            "DELETE FROM generation_job_events WHERE job_id = ?", (job_id,),
        )
        await self.execute(
            "DELETE FROM image_collection_items WHERE job_id = ?", (job_id,),
        )
        rc = await self.execute_returning_rowcount(
            "DELETE FROM generation_jobs WHERE id = ?", (job_id,),
        )
        return rc > 0

    async def generation_job_delete_by_statuses(
        self, statuses: list[str], *, modality: str | None = "image",
    ) -> int:
        """終端ステータスのジョブをバルク削除する（ファイルは残す）。

        Jobs タブの「過去 Job クリア」用。status に queued/running/dispatching/warming_cache
        などの非終端値を含めるのは危険なので、終端値（done/failed/cancelled）のみ許可する。
        """
        allowed = {"done", "failed", "cancelled"}
        clean = [s for s in statuses if isinstance(s, str) and s in allowed]
        if not clean:
            return 0
        placeholders = ",".join(["?"] * len(clean))
        where = f"status IN ({placeholders})"
        params: list = list(clean)
        if modality:
            where += " AND modality = ?"
            params.append(modality)
        # events / collection_items は job_id 経由で個別に消す（CASCADE 未設定のため）
        await self.execute(
            f"DELETE FROM generation_job_events "
            f"WHERE job_id IN (SELECT id FROM generation_jobs WHERE {where})",
            tuple(params),
        )
        await self.execute(
            f"DELETE FROM image_collection_items "
            f"WHERE job_id IN (SELECT id FROM generation_jobs WHERE {where})",
            tuple(params),
        )
        rc = await self.execute_returning_rowcount(
            f"DELETE FROM generation_jobs WHERE {where}",
            tuple(params),
        )
        return int(rc or 0)

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

    async def generation_job_set_assigned_agent(
        self, job_id: str, agent_id: str | None,
    ) -> bool:
        """assigned_agent のみを更新する（status イベントを発生させない）。

        Dispatcher が Agent を選定した直後に「予約」書き込みを行い、
        以降の claim で inflight 集計に含めるために利用する。
        """
        rc = await self.execute_returning_rowcount(
            "UPDATE generation_jobs SET assigned_agent = ? WHERE id = ?",
            (agent_id, job_id),
        )
        return rc > 0

    async def generation_job_inflight_by_agent(self) -> dict[str, int]:
        """assigned_agent 別の進行中（dispatching/warming_cache/running）ジョブ数を返す。

        複数 PC への並列分散を行う Dispatcher の Agent 選定で利用する。
        """
        rows = await self.fetchall(
            "SELECT assigned_agent AS aid, COUNT(*) AS cnt FROM generation_jobs "
            "WHERE status IN ('dispatching', 'warming_cache', 'running') "
            "  AND assigned_agent IS NOT NULL AND assigned_agent <> '' "
            "GROUP BY assigned_agent"
        )
        return {str(r["aid"]): int(r["cnt"]) for r in rows}

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

    # === Gallery: コレクション（手動グルーピング）===

    async def image_collection_list(self) -> list[dict]:
        """コレクション一覧。pinned 優先、updated_at 降順。item_count 同梱。"""
        return await self.fetchall(
            "SELECT c.*, ("
            "  SELECT COUNT(*) FROM image_collection_items i "
            "   WHERE i.collection_id = c.id"
            ") AS item_count "
            "FROM image_collections c "
            "ORDER BY c.pinned DESC, c.updated_at DESC"
        )

    async def image_collection_get(self, collection_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM image_collections WHERE id = ?", (int(collection_id),),
        )

    async def image_collection_get_by_name(self, name: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM image_collections WHERE name = ?", (name,),
        )

    async def image_collection_insert(
        self, *, name: str, description: str | None = None,
        color: str | None = None, pinned: bool = False,
    ) -> int:
        await self.execute(
            "INSERT INTO image_collections (name, description, color, pinned, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, color, 1 if pinned else 0, jst_now(), jst_now()),
        )
        row = await self.fetchone(
            "SELECT id FROM image_collections WHERE name = ?", (name,),
        )
        return int(row["id"]) if row else 0

    async def image_collection_update(
        self, collection_id: int, **fields,
    ) -> bool:
        allowed = {"name", "description", "color", "pinned"}
        sets: list[str] = []
        params: list = []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "pinned":
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            params.append(v)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(jst_now())
        params.append(int(collection_id))
        rc = await self.execute_returning_rowcount(
            f"UPDATE image_collections SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return rc > 0

    async def image_collection_delete(self, collection_id: int) -> bool:
        await self.execute(
            "DELETE FROM image_collection_items WHERE collection_id = ?",
            (int(collection_id),),
        )
        rc = await self.execute_returning_rowcount(
            "DELETE FROM image_collections WHERE id = ?", (int(collection_id),),
        )
        return rc > 0

    async def image_collection_add_jobs(
        self, collection_id: int, job_ids: list[str],
    ) -> int:
        """複数 job_id を一括でコレクションに追加。追加件数を返す。"""
        if not job_ids:
            return 0
        added = 0
        for jid in job_ids:
            rc = await self.execute_returning_rowcount(
                "INSERT OR IGNORE INTO image_collection_items "
                "(collection_id, job_id, added_at) VALUES (?, ?, ?)",
                (int(collection_id), jid, jst_now()),
            )
            added += rc
        if added:
            await self.execute(
                "UPDATE image_collections SET updated_at = ? WHERE id = ?",
                (jst_now(), int(collection_id)),
            )
        return added

    async def image_collection_remove_jobs(
        self, collection_id: int, job_ids: list[str],
    ) -> int:
        if not job_ids:
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        rc = await self.execute_returning_rowcount(
            f"DELETE FROM image_collection_items "
            f"WHERE collection_id = ? AND job_id IN ({placeholders})",
            tuple([int(collection_id), *job_ids]),
        )
        if rc:
            await self.execute(
                "UPDATE image_collections SET updated_at = ? WHERE id = ?",
                (jst_now(), int(collection_id)),
            )
        return rc

    async def image_collection_jobs_of(self, job_id: str) -> list[int]:
        rows = await self.fetchall(
            "SELECT collection_id FROM image_collection_items WHERE job_id = ?",
            (job_id,),
        )
        return [int(r["collection_id"]) for r in rows]
