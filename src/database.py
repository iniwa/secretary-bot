"""SQLite操作（aiosqlite・WALモード）。"""

import aiosqlite
from datetime import datetime, timezone, timedelta

from src.logger import get_logger

JST = timezone(timedelta(hours=9))


def jst_now() -> str:
    """現在の日本時間をISO形式文字列で返す（DB保存用）。"""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

log = get_logger(__name__)

_SCHEMA_VERSION = 8

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS memos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    tags       TEXT,
    user_id    TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    done       BOOLEAN NOT NULL DEFAULT 0,
    user_id    TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    done_at    DATETIME,
    due_date   DATETIME
);

CREATE TABLE IF NOT EXISTS reminders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message         TEXT NOT NULL,
    remind_at       DATETIME NOT NULL,
    repeat_type     TEXT,
    repeat_interval INTEGER,
    active          BOOLEAN NOT NULL DEFAULT 1,
    notified        BOOLEAN NOT NULL DEFAULT 0,
    user_id         TEXT NOT NULL DEFAULT '',
    done_at         DATETIME
);

CREATE TABLE IF NOT EXISTS conversation_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel   TEXT NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    user_id   TEXT NOT NULL DEFAULT '',
    mode      TEXT,
    unit      TEXT
);

CREATE TABLE IF NOT EXISTS conversation_summary (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    summary    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    provider  TEXT NOT NULL,
    model     TEXT NOT NULL,
    purpose   TEXT NOT NULL,
    prompt_text TEXT,
    system_text TEXT,
    response_text TEXT,
    prompt_len INTEGER NOT NULL DEFAULT 0,
    response_len INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    success   BOOLEAN NOT NULL DEFAULT 1,
    error     TEXT,
    tokens_per_sec REAL,
    eval_count INTEGER,
    prompt_eval_count INTEGER
);

CREATE TABLE IF NOT EXISTS weather_subscriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    location      TEXT NOT NULL,
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    notify_hour   INTEGER NOT NULL DEFAULT 7,
    notify_minute INTEGER NOT NULL DEFAULT 0,
    active        BOOLEAN NOT NULL DEFAULT 1,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS calendar_settings (
    user_id     TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: str = "/app/data/bot.db"):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_INIT_SQL)
        await self._migrate()
        log.info("Database connected: %s", self._path)

    async def _migrate(self) -> None:
        """PRAGMA user_version による簡易マイグレーション。"""
        _migrations: dict[int, list[str]] = {
            2: [
                "ALTER TABLE reminders ADD COLUMN notified BOOLEAN NOT NULL DEFAULT 0",
                "ALTER TABLE reminders ADD COLUMN done_at DATETIME",
            ],
            3: [
                "ALTER TABLE reminders ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE todos ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE memos ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE conversation_log ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
            ],
            4: [
                "ALTER TABLE todos ADD COLUMN due_date DATETIME",
            ],
            5: [
                """CREATE TABLE IF NOT EXISTS llm_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    provider  TEXT NOT NULL,
                    model     TEXT NOT NULL,
                    purpose   TEXT NOT NULL,
                    prompt_len INTEGER NOT NULL DEFAULT 0,
                    response_len INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    success   BOOLEAN NOT NULL DEFAULT 1,
                    error     TEXT
                )""",
            ],
            6: [
                "ALTER TABLE llm_log ADD COLUMN prompt_text TEXT",
                "ALTER TABLE llm_log ADD COLUMN system_text TEXT",
                "ALTER TABLE llm_log ADD COLUMN response_text TEXT",
            ],
            7: [
                """CREATE TABLE IF NOT EXISTS weather_subscriptions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    location      TEXT NOT NULL,
                    latitude      REAL NOT NULL,
                    longitude     REAL NOT NULL,
                    notify_hour   INTEGER NOT NULL DEFAULT 7,
                    notify_minute INTEGER NOT NULL DEFAULT 0,
                    active        BOOLEAN NOT NULL DEFAULT 1,
                    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
            ],
            8: [
                """CREATE TABLE IF NOT EXISTS calendar_settings (
                    user_id     TEXT PRIMARY KEY,
                    calendar_id TEXT NOT NULL,
                    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
            ],
            9: [
                "ALTER TABLE llm_log ADD COLUMN tokens_per_sec REAL",
                "ALTER TABLE llm_log ADD COLUMN eval_count INTEGER",
                "ALTER TABLE llm_log ADD COLUMN prompt_eval_count INTEGER",
            ],
        }
        cursor = await self._db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current = row[0] if row else 0
        for version in sorted(_migrations.keys()):
            if current < version:
                for stmt in _migrations[version]:
                    try:
                        await self._db.execute(stmt)
                    except Exception as e:
                        log.debug("Migration stmt skipped (%s): %s", e, stmt)
                await self._db.execute(f"PRAGMA user_version = {version}")
                await self._db.commit()
                log.info("Database migrated to version %d", version)

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cursor = await self.db.execute(sql, params)
        await self.db.commit()
        return cursor

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            log.info("Database closed")

    # --- 会話ログ ---

    async def log_conversation(
        self, channel: str, role: str, content: str,
        mode: str | None = None, unit: str | None = None,
        user_id: str = "",
    ) -> None:
        await self.execute(
            "INSERT INTO conversation_log (timestamp, channel, role, content, user_id, mode, unit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (jst_now(), channel, role, content, user_id, mode, unit),
        )

    async def get_conversation_logs(
        self, limit: int = 50, offset: int = 0,
        keyword: str | None = None,
        channel: str | None = None,
        bot_only: bool = False,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if keyword:
            conditions.append("content LIKE ?")
            params.append(f"%{keyword}%")
        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        if bot_only:
            # webguiは全表示、discord系はボットが応答した会話のみ
            conditions.append(
                "(channel = 'webgui'"
                " OR role = 'assistant'"
                " OR (role = 'user' AND EXISTS ("
                "   SELECT 1 FROM conversation_log c2"
                "   WHERE c2.role = 'assistant'"
                "   AND c2.channel = conversation_log.channel"
                "   AND c2.id > conversation_log.id"
                "   AND c2.id <= conversation_log.id + 5"
                ")))"
            )
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM conversation_log{where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    async def get_recent_messages(self, limit: int = 20) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM conversation_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    async def get_recent_channel_messages(
        self, channel: str, limit: int = 20, user_id: str = "",
        minutes: int = 0,
    ) -> list[dict]:
        """チャネル・ユーザー単位の直近会話履歴を古い順で返す。

        minutes: 0以外を指定すると、現在時刻から指定分以内のメッセージのみ返す。
        """
        conditions = ["channel = ?"]
        params: list = [channel]
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if minutes > 0:
            cutoff = (datetime.now(JST) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        where = " AND ".join(conditions)
        params.append(limit)
        rows = await self.fetchall(
            f"SELECT role, content FROM conversation_log "
            f"WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            tuple(params),
        )
        return list(reversed(rows))

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
    ) -> None:
        await self.execute(
            "INSERT INTO llm_log (timestamp, provider, model, purpose, prompt_text, system_text, response_text, prompt_len, response_len, duration_ms, success, error, tokens_per_sec, eval_count, prompt_eval_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (jst_now(), provider, model, purpose, prompt_text, system_text, response_text, prompt_len, response_len, duration_ms, success, error, tokens_per_sec, eval_count, prompt_eval_count),
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
