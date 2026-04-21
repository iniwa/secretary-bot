"""auto-kirinuki（配信アーカイブ切り抜き）ジョブ関連のDBメソッド。

Dispatcher 状態機械:
    queued → dispatching → warming_cache → running → done / failed / cancelled

終端でないジョブが timeout_at を超えたら stuck_reaper が retry / failed 判定を行う。
generation_jobs と方式を揃えているが、テーブル・メソッドは分離（モダリティが大きく異なる）。
"""

import json as _json
import uuid

from src.database._base import jst_now


class ClipPipelineMixin:
    # === 登録 ===

    async def clip_pipeline_job_insert(
        self, *, user_id: str, platform: str,
        video_path: str, output_dir: str,
        whisper_model: str, ollama_model: str,
        params_json: str | None = None,
        max_retries: int = 2,
    ) -> str:
        """ジョブを queued で登録し、UUID を返す。"""
        job_id = uuid.uuid4().hex
        await self.execute(
            "INSERT INTO clip_pipeline_jobs "
            "(id, user_id, platform, status, video_path, output_dir, "
            " whisper_model, ollama_model, params_json, "
            " max_retries, created_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
            (job_id, user_id, platform, video_path, output_dir,
             whisper_model, ollama_model, params_json,
             max_retries, jst_now()),
        )
        await self._clip_pipeline_job_event(
            job_id=job_id, from_status=None, to_status="queued",
            agent_id=None, detail_json=None,
        )
        return job_id

    # === 取得 ===

    async def clip_pipeline_job_get(self, job_id: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM clip_pipeline_jobs WHERE id = ?", (job_id,)
        )

    async def clip_pipeline_job_list(
        self, user_id: str | None = None, status: str | None = None,
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
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM clip_pipeline_jobs{where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    # === Dispatcher 操作 ===

    async def clip_pipeline_job_claim_queued(self) -> dict | None:
        """queued 先頭 1 件を dispatching に遷移させて返す。楽観ロック。

        時刻列は全て JST 文字列で保存されるため（jst_now / _future）、
        SQL の比較も `datetime('now', '+9 hours')` で JST に揃える。
        """
        row = await self.fetchone(
            "SELECT id FROM clip_pipeline_jobs "
            "WHERE status = 'queued' "
            "  AND (next_attempt_at IS NULL "
            "       OR next_attempt_at <= datetime('now', '+9 hours')) "
            "ORDER BY created_at ASC LIMIT 1"
        )
        if not row:
            return None
        job_id = row["id"]
        rowcount = await self.execute_returning_rowcount(
            "UPDATE clip_pipeline_jobs "
            "SET status = 'dispatching', "
            "    dispatcher_lock_at = ?, "
            "    timeout_at = datetime('now', '+9 hours', '+30 seconds') "
            "WHERE id = ? AND status = 'queued' "
            "  AND (next_attempt_at IS NULL "
            "       OR next_attempt_at <= datetime('now', '+9 hours'))",
            (jst_now(), job_id),
        )
        if rowcount != 1:
            return None
        await self._clip_pipeline_job_event(
            job_id=job_id, from_status="queued", to_status="dispatching",
            agent_id=None, detail_json=None,
        )
        return await self.clip_pipeline_job_get(job_id)

    async def clip_pipeline_job_update_status(
        self, job_id: str, to_status: str,
        expected_from: str | None = None,
        **fields,
    ) -> bool:
        """status を UPDATE し clip_pipeline_job_events に記録する。"""
        allowed = {
            "assigned_agent", "step", "progress", "result_json",
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
            f"UPDATE clip_pipeline_jobs SET {', '.join(sets)} WHERE {where_sql}",
            tuple(params),
        )
        if rowcount == 1:
            detail = {k: v for k, v in fields.items() if k in allowed}
            await self._clip_pipeline_job_event(
                job_id=job_id, from_status=expected_from, to_status=to_status,
                agent_id=fields.get("assigned_agent"),
                detail_json=_json.dumps(detail, ensure_ascii=False) if detail else None,
            )
            return True
        return False

    async def clip_pipeline_job_update_progress(
        self, job_id: str, progress: int, step: str | None = None,
    ) -> None:
        """progress / step のみ更新（デバウンスは呼び出し側で制御）。"""
        if step is None:
            await self.execute(
                "UPDATE clip_pipeline_jobs SET progress = ? WHERE id = ?",
                (int(progress), job_id),
            )
        else:
            await self.execute(
                "UPDATE clip_pipeline_jobs SET progress = ?, step = ? WHERE id = ?",
                (int(progress), step, job_id),
            )

    async def clip_pipeline_job_update_step(self, job_id: str, step: str) -> None:
        """step のみ更新。step イベントで progress を上書きしたくないときに使う。"""
        await self.execute(
            "UPDATE clip_pipeline_jobs SET step = ? WHERE id = ?",
            (step, job_id),
        )

    async def clip_pipeline_job_set_result(
        self, job_id: str, result_json: str,
    ) -> None:
        await self.execute(
            "UPDATE clip_pipeline_jobs SET result_json = ? WHERE id = ?",
            (result_json, job_id),
        )

    async def clip_pipeline_job_cancel(self, job_id: str) -> bool:
        """非終端状態のジョブを cancelled に遷移させる。"""
        row = await self.clip_pipeline_job_get(job_id)
        if not row:
            return False
        if row["status"] in ("done", "failed", "cancelled"):
            return False
        return await self.clip_pipeline_job_update_status(
            job_id, "cancelled",
            expected_from=row["status"],
            finished_at=jst_now(),
        )

    async def clip_pipeline_job_count_active_on_agent(
        self, agent_id: str, exclude_job_id: str | None = None,
    ) -> int:
        """指定 agent で dispatching / warming_cache / running 状態のジョブ数を返す。
        Dispatcher が先行ジョブの完了を待つかの判定に使う（agent 側は同時実行 1 本制限）。
        """
        sql = (
            "SELECT COUNT(*) AS n FROM clip_pipeline_jobs "
            "WHERE assigned_agent = ? "
            "  AND status IN ('dispatching', 'warming_cache', 'running')"
        )
        params: list = [agent_id]
        if exclude_job_id:
            sql += " AND id != ?"
            params.append(exclude_job_id)
        row = await self.fetchone(sql, tuple(params))
        return int(row["n"]) if row else 0

    async def clip_pipeline_job_find_timed_out(self) -> list[dict]:
        """timeout_at < now の非終端ジョブを返す。stuck_reaper が使う。
        timeout_at は JST 文字列で書かれるので比較も JST に揃える。
        """
        return await self.fetchall(
            "SELECT * FROM clip_pipeline_jobs "
            "WHERE status NOT IN ('done', 'failed', 'cancelled') "
            "  AND timeout_at IS NOT NULL "
            "  AND timeout_at < datetime('now', '+9 hours') "
            "ORDER BY created_at ASC"
        )

    # === イベントログ ===

    async def _clip_pipeline_job_event(
        self, *, job_id: str, from_status: str | None, to_status: str,
        agent_id: str | None, detail_json: str | None,
    ) -> None:
        await self.execute(
            "INSERT INTO clip_pipeline_job_events "
            "(job_id, from_status, to_status, agent_id, detail_json, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, from_status, to_status, agent_id, detail_json, jst_now()),
        )

    async def clip_pipeline_job_events_list(self, job_id: str) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM clip_pipeline_job_events "
            "WHERE job_id = ? ORDER BY occurred_at ASC, id ASC",
            (job_id,),
        )
