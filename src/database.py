"""SQLite操作（aiosqlite・WALモード）。"""

import aiosqlite
from datetime import datetime, timezone, timedelta

from src.logger import get_logger

JST = timezone(timedelta(hours=9))


def jst_now() -> str:
    """現在の日本時間をISO形式文字列で返す（DB保存用）。"""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

log = get_logger(__name__)

_SCHEMA_VERSION = 27

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
            22: [
                # 重複除去してから UNIQUE INDEX を張る（既存データ保護）
                "DELETE FROM stt_transcripts WHERE id NOT IN (SELECT MIN(id) FROM stt_transcripts GROUP BY started_at)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_stt_transcripts_started_at ON stt_transcripts(started_at)",
            ],
            23: [
                # Main PC アクティビティ蓄積テーブル群
                """CREATE TABLE IF NOT EXISTS activity_samples (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts                 TEXT NOT NULL,
                    game               TEXT,
                    foreground_process TEXT,
                    is_fullscreen      INTEGER DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_activity_samples_ts ON activity_samples(ts)",
                """CREATE TABLE IF NOT EXISTS game_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_name    TEXT NOT NULL,
                    start_at     TEXT NOT NULL,
                    end_at       TEXT,
                    duration_sec INTEGER
                )""",
                "CREATE INDEX IF NOT EXISTS idx_game_sessions_start ON game_sessions(start_at)",
                """CREATE TABLE IF NOT EXISTS foreground_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    process_name TEXT NOT NULL,
                    start_at     TEXT NOT NULL,
                    end_at       TEXT,
                    duration_sec INTEGER,
                    during_game  INTEGER DEFAULT 0,
                    game_name    TEXT
                )""",
                "CREATE INDEX IF NOT EXISTS idx_foreground_sessions_start ON foreground_sessions(start_at)",
            ],
            24: [
                # Googleカレンダー読み取り元の登録
                """CREATE TABLE IF NOT EXISTS calendar_read_sources (
                    calendar_id   TEXT PRIMARY KEY,
                    display_name  TEXT,
                    is_private    INTEGER NOT NULL DEFAULT 0,
                    enabled       INTEGER NOT NULL DEFAULT 1,
                    last_synced_at TEXT,
                    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                # 読み取ったカレンダーイベントのキャッシュ
                # is_private=1 の場合 title/location/description は NULL 保存
                """CREATE TABLE IF NOT EXISTS calendar_events (
                    event_id      TEXT PRIMARY KEY,
                    calendar_id   TEXT NOT NULL,
                    title         TEXT,
                    start_at      TEXT NOT NULL,
                    end_at        TEXT NOT NULL,
                    is_all_day    INTEGER NOT NULL DEFAULT 0,
                    is_private    INTEGER NOT NULL DEFAULT 0,
                    fetched_at    TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_at)",
                "CREATE INDEX IF NOT EXISTS idx_calendar_events_cal ON calendar_events(calendar_id)",
            ],
            25: [
                # InnerMind 自律アクション基盤
                "ALTER TABLE mimi_monologue ADD COLUMN action TEXT",
                "ALTER TABLE mimi_monologue ADD COLUMN reasoning TEXT",
                "ALTER TABLE mimi_monologue ADD COLUMN action_params TEXT",
                "ALTER TABLE mimi_monologue ADD COLUMN action_result TEXT",
                "ALTER TABLE mimi_monologue ADD COLUMN pending_id INTEGER",
                """CREATE TABLE IF NOT EXISTS pending_actions (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    monologue_id       INTEGER,
                    tier               INTEGER NOT NULL,
                    unit_name          TEXT,
                    method             TEXT,
                    params             TEXT NOT NULL,
                    reasoning          TEXT NOT NULL,
                    summary            TEXT NOT NULL,
                    status             TEXT NOT NULL DEFAULT 'pending',
                    discord_message_id TEXT,
                    channel_id         TEXT,
                    user_id            TEXT NOT NULL,
                    result             TEXT,
                    error              TEXT,
                    created_at         DATETIME NOT NULL,
                    resolved_at        DATETIME,
                    expires_at         DATETIME NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_pending_actions_status ON pending_actions(status)",
                "CREATE INDEX IF NOT EXISTS idx_pending_actions_user   ON pending_actions(user_id, status)",
            ],
            26: [
                # === AI 画像生成基盤 ===
                # ComfyUI ワークフロー（プリセット）
                """CREATE TABLE IF NOT EXISTS workflows (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                TEXT NOT NULL UNIQUE,
                    description         TEXT,
                    category            TEXT NOT NULL,
                    workflow_json       TEXT NOT NULL,
                    required_nodes      TEXT,
                    required_models     TEXT,
                    required_loras      TEXT,
                    main_pc_only        INTEGER NOT NULL DEFAULT 0,
                    starred             INTEGER NOT NULL DEFAULT 0,
                    default_timeout_sec INTEGER NOT NULL DEFAULT 300,
                    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                # プロンプト断片テンプレート
                """CREATE TABLE IF NOT EXISTS prompt_templates (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL,
                    positive   TEXT,
                    negative   TEXT,
                    notes      TEXT,
                    tags       TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                # 会話的プロンプト編集セッション
                """CREATE TABLE IF NOT EXISTS prompt_sessions (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id           TEXT NOT NULL,
                    platform          TEXT NOT NULL,
                    positive          TEXT,
                    negative          TEXT,
                    history_json      TEXT,
                    base_workflow_id  INTEGER,
                    params_json       TEXT,
                    updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at        DATETIME
                )""",
                "CREATE INDEX IF NOT EXISTS idx_prompt_sessions_user ON prompt_sessions(user_id, updated_at)",
                # 生成ジョブキュー（Dispatcher 状態機械のフィールドを最初から含める）
                """CREATE TABLE IF NOT EXISTS image_jobs (
                    id                 TEXT PRIMARY KEY,
                    user_id            TEXT NOT NULL,
                    platform           TEXT NOT NULL,
                    workflow_id        INTEGER,
                    positive           TEXT,
                    negative           TEXT,
                    params_json        TEXT,
                    status             TEXT NOT NULL DEFAULT 'queued',
                    assigned_agent     TEXT,
                    priority           INTEGER NOT NULL DEFAULT 0,
                    progress           INTEGER NOT NULL DEFAULT 0,
                    error_message      TEXT,
                    result_paths       TEXT,
                    retry_count        INTEGER NOT NULL DEFAULT 0,
                    max_retries        INTEGER NOT NULL DEFAULT 2,
                    last_error         TEXT,
                    cache_sync_id      TEXT,
                    next_attempt_at    DATETIME,
                    dispatcher_lock_at DATETIME,
                    timeout_at         DATETIME,
                    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at         DATETIME,
                    finished_at        DATETIME
                )""",
                "CREATE INDEX IF NOT EXISTS idx_image_jobs_status_next ON image_jobs(status, next_attempt_at)",
                "CREATE INDEX IF NOT EXISTS idx_image_jobs_user_created ON image_jobs(user_id, created_at)",
                # ジョブ遷移イベントログ
                """CREATE TABLE IF NOT EXISTS image_job_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id       TEXT NOT NULL,
                    from_status  TEXT,
                    to_status    TEXT NOT NULL,
                    agent_id     TEXT,
                    detail_json  TEXT,
                    occurred_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                "CREATE INDEX IF NOT EXISTS idx_image_job_events_job ON image_job_events(job_id, occurred_at)",
                # LoRA プロジェクト
                """CREATE TABLE IF NOT EXISTS lora_projects (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT NOT NULL UNIQUE,
                    description  TEXT,
                    dataset_path TEXT,
                    base_model   TEXT,
                    config_json  TEXT,
                    status       TEXT NOT NULL DEFAULT 'draft',
                    output_path  TEXT,
                    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                # LoRA データセット項目
                """CREATE TABLE IF NOT EXISTS lora_dataset_items (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id  INTEGER NOT NULL,
                    image_path  TEXT NOT NULL,
                    caption     TEXT,
                    tags        TEXT,
                    reviewed_at DATETIME
                )""",
                # LoRA 学習ジョブ
                """CREATE TABLE IF NOT EXISTS lora_train_jobs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id    INTEGER NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'queued',
                    progress      INTEGER NOT NULL DEFAULT 0,
                    tb_logdir     TEXT,
                    sample_images TEXT,
                    started_at    DATETIME,
                    finished_at   DATETIME,
                    error_message TEXT
                )""",
                # 各 PC のキャッシュ状況
                """CREATE TABLE IF NOT EXISTS model_cache_manifest (
                    agent_id     TEXT NOT NULL,
                    file_type    TEXT NOT NULL,
                    filename     TEXT NOT NULL,
                    sha256       TEXT,
                    size         INTEGER,
                    last_used_at DATETIME,
                    starred      INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (agent_id, file_type, filename)
                )""",
                # LoRA 学習推奨値テンプレート
                """CREATE TABLE IF NOT EXISTS lora_config_templates (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    category   TEXT NOT NULL,
                    size_class TEXT NOT NULL,
                    rank       INTEGER NOT NULL,
                    alpha      INTEGER NOT NULL,
                    lr_unet    REAL NOT NULL,
                    lr_text    REAL NOT NULL,
                    batch_size INTEGER NOT NULL,
                    epochs     INTEGER NOT NULL,
                    scheduler  TEXT NOT NULL,
                    extra_json TEXT,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                # 初期 9 テンプレート（character/outfit/style × small/medium/large）
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('character', 'small',  16,  8, 1e-4,   5e-5,   2, 10, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('character', 'medium', 16,  8, 1e-4,   5e-5,   2,  8, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('character', 'large',  32, 16, 1e-4,   5e-5,   2,  6, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('outfit',    'small',  16,  8, 1e-4,   5e-5,   2, 12, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('outfit',    'medium', 16,  8, 1e-4,   5e-5,   2, 10, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('outfit',    'large',  32, 16, 1e-4,   5e-5,   2,  8, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('style',     'small',  32, 16, 5e-5,   2.5e-5, 2, 15, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('style',     'medium', 32, 16, 5e-5,   2.5e-5, 2, 12, 'cosine', 1)",
                "INSERT OR IGNORE INTO lora_config_templates (category, size_class, rank, alpha, lr_unet, lr_text, batch_size, epochs, scheduler, is_default) VALUES ('style',     'large',  64, 32, 5e-5,   2.5e-5, 2, 10, 'cosine', 1)",
            ],
            27: [
                # Multi-PC activity detection: Sub PC foreground 記録 + active_pcs
                # `pc` カラム: サンプル/フォアグラウンドセッションがどの PC で発生したか
                # `active_pcs` カラム: Main サンプル側に CSV で保存（例: "main,sub"）。一次情報源は Main sender の Input-Relay /api/status
                "ALTER TABLE activity_samples   ADD COLUMN pc TEXT NOT NULL DEFAULT 'main'",
                "ALTER TABLE foreground_sessions ADD COLUMN pc TEXT NOT NULL DEFAULT 'main'",
                "ALTER TABLE activity_samples   ADD COLUMN active_pcs TEXT",
                "CREATE INDEX IF NOT EXISTS idx_foreground_sessions_pc_start ON foreground_sessions(pc, start_at)",
                "CREATE INDEX IF NOT EXISTS idx_activity_samples_pc_ts ON activity_samples(pc, ts)",
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
                        log.warning("Migration stmt skipped (v%d): %s — %s", version, e, stmt[:80])
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
        action: str | None = None, reasoning: str | None = None,
        action_params: str | None = None, action_result: str | None = None,
        pending_id: int | None = None,
    ) -> int:
        """モノローグを保存し、挿入されたIDを返す。
        action != None の行は自律アクションの decision ログ。"""
        cursor = await self.execute(
            "INSERT INTO mimi_monologue "
            "(monologue, mood, did_notify, notified_message, created_at, context_json, "
            " action, reasoning, action_params, action_result, pending_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (monologue, mood, 1 if did_notify else 0, notified_message, jst_now(), context_json,
             action, reasoning, action_params, action_result, pending_id),
        )
        return cursor.lastrowid

    async def set_monologue_action_result(
        self, monologue_id: int, action_result: str,
    ) -> None:
        """decision 実行後に結果 JSON を書き戻す。"""
        await self.execute(
            "UPDATE mimi_monologue SET action_result = ? WHERE id = ?",
            (action_result, monologue_id),
        )

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

    # --- InnerMind 自律アクション: pending_actions ---

    async def create_pending_action(
        self, *, monologue_id: int | None, tier: int,
        unit_name: str | None, method: str | None, params: str,
        reasoning: str, summary: str, user_id: str,
        channel_id: str | None, expires_at: str,
    ) -> int:
        cursor = await self.execute(
            "INSERT INTO pending_actions "
            "(monologue_id, tier, unit_name, method, params, reasoning, summary, "
            " status, user_id, channel_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (monologue_id, tier, unit_name, method, params, reasoning, summary,
             user_id, channel_id, jst_now(), expires_at),
        )
        return cursor.lastrowid

    async def get_pending_action(self, pending_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM pending_actions WHERE id = ?", (pending_id,)
        )

    async def list_pending_actions(
        self, *, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        if status:
            return await self.fetchall(
                "SELECT * FROM pending_actions WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        return await self.fetchall(
            "SELECT * FROM pending_actions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def count_pending_today(self, tier: int, user_id: str) -> int:
        """今日作成された同 tier の pending 数（上限制御用）。"""
        today = datetime.now(JST).strftime("%Y-%m-%d")
        row = await self.fetchone(
            "SELECT COUNT(*) AS c FROM pending_actions "
            "WHERE tier = ? AND user_id = ? AND date(created_at) = ?",
            (tier, user_id, today),
        )
        return int(row["c"]) if row else 0

    async def set_pending_discord_message(
        self, pending_id: int, message_id: str,
    ) -> None:
        await self.execute(
            "UPDATE pending_actions SET discord_message_id = ? WHERE id = ?",
            (message_id, pending_id),
        )

    async def resolve_pending_action(
        self, pending_id: int, status: str,
        result: str | None = None, error: str | None = None,
    ) -> None:
        """pending_action を approved/rejected/expired/executed/failed/cancelled のいずれかに確定。"""
        await self.execute(
            "UPDATE pending_actions "
            "SET status = ?, result = ?, error = ?, resolved_at = ? WHERE id = ?",
            (status, result, error, jst_now(), pending_id),
        )

    async def count_pending_unread(self, user_id: str | None = None) -> int:
        """承認待ちの pending 件数（通知バッジ用）。"""
        if user_id:
            row = await self.fetchone(
                "SELECT COUNT(*) AS c FROM pending_actions "
                "WHERE status = 'pending' AND user_id = ?", (user_id,),
            )
        else:
            row = await self.fetchone(
                "SELECT COUNT(*) AS c FROM pending_actions WHERE status = 'pending'"
            )
        return int(row["c"]) if row else 0

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

    # === 画像生成: image_jobs ===

    async def image_job_insert(
        self, *, user_id: str, platform: str,
        workflow_id: int | None, positive: str | None,
        negative: str | None, params_json: str,
        priority: int = 0, max_retries: int = 2,
    ) -> str:
        """ジョブを queued で登録し、UUID を返す。"""
        import uuid
        job_id = uuid.uuid4().hex
        await self.execute(
            "INSERT INTO image_jobs "
            "(id, user_id, platform, workflow_id, positive, negative, "
            " params_json, status, priority, max_retries, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, user_id, platform, workflow_id, positive, negative,
             params_json, priority, max_retries, jst_now()),
        )
        await self._image_job_event(
            job_id=job_id, from_status=None, to_status="queued",
            agent_id=None, detail_json=None,
        )
        return job_id

    async def image_job_get(self, job_id: str) -> dict | None:
        return await self.fetchone("SELECT * FROM image_jobs WHERE id = ?", (job_id,))

    async def image_job_list(
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
            f"SELECT * FROM image_jobs{where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    async def image_job_claim_queued(self) -> dict | None:
        """楽観ロックで 1 件 queued → dispatching へ遷移させ、該当行を返す。

        設計書の UPDATE 文に準拠:
          WHERE status='queued'
            AND (next_attempt_at IS NULL OR next_attempt_at <= now)
        """
        # まず候補 ID を 1 件選ぶ（優先度・作成順）
        row = await self.fetchone(
            "SELECT id FROM image_jobs "
            "WHERE status = 'queued' "
            "  AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now')) "
            "ORDER BY priority DESC, created_at ASC LIMIT 1"
        )
        if not row:
            return None
        job_id = row["id"]
        rowcount = await self.execute_returning_rowcount(
            "UPDATE image_jobs "
            "SET status = 'dispatching', "
            "    dispatcher_lock_at = ?, "
            "    timeout_at = datetime('now', '+30 seconds') "
            "WHERE id = ? AND status = 'queued' "
            "  AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now'))",
            (jst_now(), job_id),
        )
        if rowcount != 1:
            return None  # 他 worker に取られた
        await self._image_job_event(
            job_id=job_id, from_status="queued", to_status="dispatching",
            agent_id=None, detail_json=None,
        )
        return await self.image_job_get(job_id)

    async def image_job_update_status(
        self, job_id: str, to_status: str,
        expected_from: str | None = None,
        **fields,
    ) -> bool:
        """status を UPDATE し image_job_events に記録する。
        expected_from 指定時は from チェック付きで更新される（race 回避）。
        """
        allowed = {
            "assigned_agent", "progress", "error_message", "result_paths",
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
            f"UPDATE image_jobs SET {', '.join(sets)} WHERE {where_sql}",
            tuple(params),
        )
        if rowcount == 1:
            detail = {k: v for k, v in fields.items() if k in allowed}
            import json as _json
            await self._image_job_event(
                job_id=job_id, from_status=expected_from, to_status=to_status,
                agent_id=fields.get("assigned_agent"),
                detail_json=_json.dumps(detail, ensure_ascii=False) if detail else None,
            )
            return True
        return False

    async def image_job_update_progress(self, job_id: str, progress: int) -> None:
        """progress のみ更新（デバウンスは呼び出し側で制御）。"""
        await self.execute(
            "UPDATE image_jobs SET progress = ? WHERE id = ?",
            (int(progress), job_id),
        )

    async def image_job_set_result(self, job_id: str, result_paths_json: str) -> None:
        await self.execute(
            "UPDATE image_jobs SET result_paths = ? WHERE id = ?",
            (result_paths_json, job_id),
        )

    async def image_job_find_timed_out(self) -> list[dict]:
        """timeout_at < now の非終端ジョブを返す。"""
        return await self.fetchall(
            "SELECT * FROM image_jobs "
            "WHERE status NOT IN ('done', 'failed', 'cancelled') "
            "  AND timeout_at IS NOT NULL "
            "  AND timeout_at < datetime('now') "
            "ORDER BY created_at ASC"
        )

    async def image_job_cancel(self, job_id: str) -> bool:
        """非終端状態のジョブを cancelled に遷移させる。"""
        row = await self.image_job_get(job_id)
        if not row:
            return False
        if row["status"] in ("done", "failed", "cancelled"):
            return False
        ok = await self.image_job_update_status(
            job_id, "cancelled",
            expected_from=row["status"],
            finished_at=jst_now(),
        )
        return ok

    async def _image_job_event(
        self, *, job_id: str, from_status: str | None, to_status: str,
        agent_id: str | None, detail_json: str | None,
    ) -> None:
        await self.execute(
            "INSERT INTO image_job_events "
            "(job_id, from_status, to_status, agent_id, detail_json, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, from_status, to_status, agent_id, detail_json, jst_now()),
        )

    async def image_job_events_list(self, job_id: str) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM image_job_events "
            "WHERE job_id = ? ORDER BY occurred_at ASC, id ASC",
            (job_id,),
        )

    # === 画像生成: prompt_sessions ===

    async def prompt_session_get_active(
        self, user_id: str, platform: str,
    ) -> dict | None:
        """未失効（expires_at > now）で最も新しいセッションを返す。"""
        return await self.fetchone(
            "SELECT * FROM prompt_sessions "
            "WHERE user_id = ? AND platform = ? "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY updated_at DESC LIMIT 1",
            (user_id, platform, jst_now()),
        )

    async def prompt_session_get(self, session_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM prompt_sessions WHERE id = ?", (session_id,),
        )

    async def prompt_session_insert(
        self, *, user_id: str, platform: str,
        positive: str | None, negative: str | None,
        history_json: str | None = None,
        base_workflow_id: int | None = None,
        params_json: str | None = None,
        ttl_days: int = 7,
    ) -> int:
        """新規プロンプトセッションを作成し id を返す。"""
        now = datetime.now(JST)
        expires = (now + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = await self.execute(
            "INSERT INTO prompt_sessions "
            "(user_id, platform, positive, negative, history_json, "
            " base_workflow_id, params_json, updated_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, platform, positive, negative, history_json,
             base_workflow_id, params_json, jst_now(), expires),
        )
        return int(cur.lastrowid or 0)

    async def prompt_session_update(
        self, session_id: int, *,
        positive: str | None = None, negative: str | None = None,
        history_json: str | None = None,
        base_workflow_id: int | None = None,
        params_json: str | None = None,
        ttl_days: int | None = 7,
    ) -> bool:
        """部分更新。None のフィールドは更新対象外（空文字にしたい場合は明示的に ""）。"""
        sets: list[str] = []
        params: list = []
        if positive is not None:
            sets.append("positive = ?")
            params.append(positive)
        if negative is not None:
            sets.append("negative = ?")
            params.append(negative)
        if history_json is not None:
            sets.append("history_json = ?")
            params.append(history_json)
        if base_workflow_id is not None:
            sets.append("base_workflow_id = ?")
            params.append(base_workflow_id)
        if params_json is not None:
            sets.append("params_json = ?")
            params.append(params_json)
        sets.append("updated_at = ?")
        params.append(jst_now())
        if ttl_days is not None:
            expires = (datetime.now(JST) + timedelta(days=ttl_days)).strftime(
                "%Y-%m-%d %H:%M:%S",
            )
            sets.append("expires_at = ?")
            params.append(expires)
        params.append(session_id)
        await self.execute(
            f"UPDATE prompt_sessions SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return True

    async def prompt_session_list(
        self, user_id: str | None = None, limit: int = 20,
    ) -> list[dict]:
        if user_id:
            return await self.fetchall(
                "SELECT * FROM prompt_sessions "
                "WHERE user_id = ? "
                "AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, jst_now(), limit),
            )
        return await self.fetchall(
            "SELECT * FROM prompt_sessions "
            "WHERE expires_at IS NULL OR expires_at > ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (jst_now(), limit),
        )

    async def prompt_session_delete(self, session_id: int) -> None:
        await self.execute(
            "DELETE FROM prompt_sessions WHERE id = ?", (session_id,),
        )

    async def prompt_session_cleanup_expired(self) -> int:
        """TTL 切れのセッションを削除し、削除件数を返す。"""
        rows = await self.fetchall(
            "SELECT id FROM prompt_sessions "
            "WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (jst_now(),),
        )
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        await self.execute(
            f"DELETE FROM prompt_sessions WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return len(ids)
