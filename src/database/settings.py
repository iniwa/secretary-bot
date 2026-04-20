"""設定・LLMログ関連のDBメソッド。"""

from src.database._base import jst_now


class SettingsMixin:
    # --- 設定永続化 ---

    async def get_setting(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def delete_setting(self, key: str) -> None:
        await self.execute("DELETE FROM settings WHERE key = ?", (key,))

    # --- LLMログ ---

    async def log_llm_call(
        self, provider: str, model: str, purpose: str,
        prompt_len: int, response_len: int, duration_ms: int,
        success: bool = True, error: str | None = None,
        prompt_text: str | None = None, system_text: str | None = None,
        response_text: str | None = None,
        tokens_per_sec: float | None = None,
        eval_count: int | None = None,
        prompt_eval_count: int | None = None,
        instance: str | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO llm_log (timestamp, provider, model, purpose, prompt_text, system_text, response_text, prompt_len, response_len, duration_ms, success, error, tokens_per_sec, eval_count, prompt_eval_count, instance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (jst_now(), provider, model, purpose, prompt_text, system_text, response_text, prompt_len, response_len, duration_ms, success, error, tokens_per_sec, eval_count, prompt_eval_count, instance),
        )

    async def get_llm_logs(
        self, limit: int = 50, offset: int = 0,
        provider: str | None = None,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if provider:
            conditions.append("provider = ?")
            params.append(provider)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM llm_log{where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    async def get_all_settings(self, prefix: str = "") -> dict[str, str]:
        if prefix:
            rows = await self.fetchall(
                "SELECT key, value FROM settings WHERE key LIKE ?", (f"{prefix}%",)
            )
        else:
            rows = await self.fetchall("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}
