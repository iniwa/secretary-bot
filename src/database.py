"""SQLite操作（aiosqlite・WALモード）。"""

import aiosqlite
from datetime import datetime, timezone, timedelta

from src.logger import get_logger

JST = timezone(timedelta(hours=9))


def jst_now() -> str:
    """現在の日本時間をISO形式文字列で返す（DB保存用）。"""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

log = get_logger(__name__)

_SCHEMA_VERSION = 21

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
    done_at         DATETIME,
    snooze_count    INTEGER NOT NULL DEFAULT 0,
    last_snoozed_at TEXT,
    snoozed_until   TEXT
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
    prompt_eval_count INTEGER,
    instance TEXT
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

CREATE TABLE IF NOT EXISTS mimi_monologue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    monologue        TEXT NOT NULL,
    mood             TEXT,
    did_notify       BOOLEAN DEFAULT 0,
    notified_message TEXT,
    created_at       DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS mimi_self_model (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS docker_log_exclusions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    container_name TEXT NOT NULL DEFAULT '',
    pattern        TEXT NOT NULL,
    reason         TEXT DEFAULT '',
    added_by       TEXT DEFAULT '',
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(container_name, pattern)
);

CREATE TABLE IF NOT EXISTS docker_error_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    container_name TEXT NOT NULL,
    message        TEXT NOT NULL,
    first_seen     DATETIME NOT NULL,
    last_seen      DATETIME NOT NULL,
    count          INTEGER NOT NULL DEFAULT 1,
    dismissed      BOOLEAN NOT NULL DEFAULT 0,
    level          TEXT NOT NULL DEFAULT 'error'
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
            10: [
                """CREATE TABLE IF NOT EXISTS mimi_monologue (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    monologue        TEXT NOT NULL,
                    mood             TEXT,
                    did_notify       BOOLEAN DEFAULT 0,
                    notified_message TEXT,
                    created_at       DATETIME NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS mimi_self_model (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    updated_at DATETIME NOT NULL
                )""",
            ],
            11: [
                "ALTER TABLE conversation_log ADD COLUMN channel_name TEXT DEFAULT ''",
            ],
            12: [
                "ALTER TABLE reminders ADD COLUMN snooze_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE reminders ADD COLUMN last_snoozed_at TEXT",
                "ALTER TABLE reminders ADD COLUMN snoozed_until TEXT",
            ],
            13: [
                """CREATE TABLE IF NOT EXISTS rss_feeds (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    url        TEXT UNIQUE NOT NULL,
                    title      TEXT NOT NULL,
                    category   TEXT NOT NULL,
                    is_preset  INTEGER DEFAULT 0,
                    added_by   TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )""",
                """CREATE TABLE IF NOT EXISTS rss_articles (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    feed_id      INTEGER NOT NULL REFERENCES rss_feeds(id),
                    title        TEXT NOT NULL,
                    url          TEXT UNIQUE NOT NULL,
                    summary      TEXT,
                    published_at TEXT,
                    fetched_at   TEXT DEFAULT (datetime('now'))
                )""",
                """CREATE TABLE IF NOT EXISTS rss_user_prefs (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id  TEXT NOT NULL,
                    feed_id  INTEGER REFERENCES rss_feeds(id),
                    category TEXT,
                    enabled  INTEGER DEFAULT 1,
                    UNIQUE(user_id, feed_id),
                    UNIQUE(user_id, category)
                )""",
                """CREATE TABLE IF NOT EXISTS rss_feedback (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL,
                    article_id INTEGER NOT NULL REFERENCES rss_articles(id),
                    rating     INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, article_id)
                )""",
            ],
            14: [
                """CREATE TABLE IF NOT EXISTS stt_transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_text TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_seconds REAL,
                    collected_at TEXT DEFAULT (datetime('now')),
                    summarized INTEGER DEFAULT 0
                )""",
                """CREATE TABLE IF NOT EXISTS stt_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    transcript_ids TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )""",
            ],
            15: [
                "ALTER TABLE mimi_monologue ADD COLUMN context_json TEXT DEFAULT ''",
            ],
            16: [
                """CREATE TABLE IF NOT EXISTS docker_log_exclusions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern    TEXT NOT NULL UNIQUE,
                    reason     TEXT DEFAULT '',
                    added_by   TEXT DEFAULT '',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
            ],
            17: [
                """CREATE TABLE IF NOT EXISTS docker_error_log (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    container_name TEXT NOT NULL,
                    message        TEXT NOT NULL,
                    first_seen     DATETIME NOT NULL,
                    last_seen      DATETIME NOT NULL,
                    count          INTEGER NOT NULL DEFAULT 1,
                    dismissed      BOOLEAN NOT NULL DEFAULT 0
                )""",
            ],
            18: [
                "ALTER TABLE rss_articles ADD COLUMN description TEXT",
            ],
            19: [
                "ALTER TABLE docker_error_log ADD COLUMN level TEXT NOT NULL DEFAULT 'error'",
            ],
            20: [
                "ALTER TABLE llm_log ADD COLUMN instance TEXT",
            ],
            21: [
                "ALTER TABLE docker_log_exclusions ADD COLUMN container_name TEXT NOT NULL DEFAULT ''",
                # UNIQUE(pattern) → UNIQUE(container_name, pattern) への変更
                # SQLite は DROP CONSTRAINT 不可。新テーブルへの移行は複雑なので、
                # 旧 UNIQUE(pattern) 制約は残したまま運用する。
                # container_name 付きの重複チェックはアプリケーション側で行う。
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

    async def execute_returning_rowcount(self, sql: str, params: tuple = ()) -> int:
        cursor = await self.db.execute(sql, params)
        await self.db.commit()
        return cursor.rowcount

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
        user_id: str = "", channel_name: str = "",
    ) -> None:
        await self.execute(
            "INSERT INTO conversation_log (timestamp, channel, role, content, user_id, mode, unit, channel_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (jst_now(), channel, role, content, user_id, mode, unit, channel_name),
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
            f"SELECT role, content, channel_name FROM conversation_log "
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

    # --- InnerMind モノローグ ---

    async def save_monologue(
        self, monologue: str, mood: str | None = None,
        did_notify: bool = False, notified_message: str | None = None,
        context_json: str = "",
    ) -> int:
        """モノローグを保存し、挿入されたIDを返す。"""
        cursor = await self.execute(
            "INSERT INTO mimi_monologue (monologue, mood, did_notify, notified_message, created_at, context_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (monologue, mood, 1 if did_notify else 0, notified_message, jst_now(), context_json),
        )
        return cursor.lastrowid

    async def update_monologue_notify(
        self, monologue_id: int, notified_message: str,
    ) -> None:
        """モノローグの発言情報を更新する。"""
        await self.execute(
            "UPDATE mimi_monologue SET did_notify = 1, notified_message = ? WHERE id = ?",
            (notified_message, monologue_id),
        )

    async def get_monologues(
        self, limit: int = 50, did_notify_only: bool = False,
    ) -> list[dict]:
        """モノローグ履歴を取得する。"""
        where = " WHERE did_notify = 1" if did_notify_only else ""
        return await self.fetchall(
            f"SELECT * FROM mimi_monologue{where} ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_last_monologue(self) -> dict | None:
        """最新のモノローグを1件取得する。"""
        return await self.fetchone(
            "SELECT * FROM mimi_monologue ORDER BY created_at DESC LIMIT 1"
        )

    # --- InnerMind 自己モデル ---

    async def upsert_self_model(self, key: str, value: str) -> None:
        """自己モデルのkey-valueを更新（存在すればUPDATE、なければINSERT）。"""
        existing = await self.fetchone(
            "SELECT id FROM mimi_self_model WHERE key = ?", (key,)
        )
        if existing:
            await self.execute(
                "UPDATE mimi_self_model SET value = ?, updated_at = ? WHERE key = ?",
                (value, jst_now(), key),
            )
        else:
            await self.execute(
                "INSERT INTO mimi_self_model (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, jst_now()),
            )

    async def get_self_model(self) -> dict[str, str]:
        """自己モデル全体をdict形式で取得する。"""
        rows = await self.fetchall("SELECT key, value FROM mimi_self_model")
        return {r["key"]: r["value"] for r in rows}
