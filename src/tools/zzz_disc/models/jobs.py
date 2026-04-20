"""VLM 抽出ジョブキュー。"""

import json
from typing import Any

from ._base import _now

__all__ = [
    "_job_row_to_dict",
    "create_job",
    "get_job",
    "list_jobs",
    "update_job",
    "delete_job",
    "list_jobs_to_resume",
    "prune_finished_jobs",
]


# ---------- Jobs（既存維持） ----------

def _job_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "source": row["source"],
        "image_path": row["image_path"],
        "extracted_json": json.loads(row["extracted_json"]) if row["extracted_json"] else None,
        "normalized_json": json.loads(row["normalized_json"]) if row["normalized_json"] else None,
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def create_job(db, *, source: str, image_path: str | None = None) -> int:
    now = _now()
    cursor = await db.execute(
        "INSERT INTO zzz_extraction_jobs (status, source, image_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("queued", source, image_path, now, now),
    )
    return cursor.lastrowid


async def get_job(db, job_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM zzz_extraction_jobs WHERE id = ?", (job_id,))
    return _job_row_to_dict(row) if row else None


async def list_jobs(db, *, statuses: list[str] | None = None,
                    limit: int = 100) -> list[dict]:
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        rows = await db.fetchall(
            f"SELECT * FROM zzz_extraction_jobs WHERE status IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT ?",
            tuple(statuses) + (limit,),
        )
    else:
        rows = await db.fetchall(
            "SELECT * FROM zzz_extraction_jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return [_job_row_to_dict(r) for r in rows]


async def update_job(db, job_id: int, *, status: str | None = None,
                     image_path: str | None = None,
                     extracted_json: Any | None = None,
                     normalized_json: Any | None = None,
                     error_message: str | None = None) -> int:
    fields = []
    params: list = []
    if status is not None:
        fields.append("status = ?"); params.append(status)
    if image_path is not None:
        fields.append("image_path = ?"); params.append(image_path)
    if extracted_json is not None:
        fields.append("extracted_json = ?"); params.append(json.dumps(extracted_json, ensure_ascii=False))
    if normalized_json is not None:
        fields.append("normalized_json = ?"); params.append(json.dumps(normalized_json, ensure_ascii=False))
    if error_message is not None:
        fields.append("error_message = ?"); params.append(error_message)
    fields.append("updated_at = ?"); params.append(_now())
    params.append(job_id)
    return await db.execute_returning_rowcount(
        f"UPDATE zzz_extraction_jobs SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


async def delete_job(db, job_id: int) -> int:
    return await db.execute_returning_rowcount(
        "DELETE FROM zzz_extraction_jobs WHERE id = ?", (job_id,),
    )


async def list_jobs_to_resume(db) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM zzz_extraction_jobs "
        "WHERE status IN ('queued', 'capturing', 'extracting') ORDER BY id"
    )
    return [_job_row_to_dict(r) for r in rows]


async def prune_finished_jobs(db, retention: int = 200) -> int:
    rows = await db.fetchall(
        "SELECT id FROM zzz_extraction_jobs WHERE status IN ('saved', 'failed') "
        "ORDER BY id DESC"
    )
    stale = [r["id"] for r in rows[retention:]]
    if not stale:
        return 0
    placeholders = ",".join(["?"] * len(stale))
    return await db.execute_returning_rowcount(
        f"DELETE FROM zzz_extraction_jobs WHERE id IN ({placeholders})",
        tuple(stale),
    )
