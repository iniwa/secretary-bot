"""SQLite操作の基盤（aiosqlite・WALモード・マイグレーション）。"""

from datetime import datetime, timedelta, timezone

import aiosqlite

from src.logger import get_logger

JST = timezone(timedelta(hours=9))


def jst_now() -> str:
    """現在の日本時間をISO形式文字列で返す（DB保存用）。"""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

log = get_logger(__name__)

_SCHEMA_VERSION = 32

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


class DatabaseBase:
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
            28: [
                # === 画像生成基盤のモダリティ汎用化 + セクション合成プリセット ===
                # image_jobs → generation_jobs にリネームし、動画/音声も乗せられるようにする。
                # 互換 View で `SELECT * FROM image_jobs` は引き続き動く。
                # セクション断片プリセット（quality/style/...）を DB で管理する。
                #
                # 新カラム:
                #   generation_jobs.modality       -- 'image' / 'video' / 'audio'
                #   generation_jobs.sections_json  -- 合成時に積んだ section_id リスト（再現用）
                #   generation_jobs.result_kinds   -- 出力メディア種別の JSON 配列
                """CREATE TABLE IF NOT EXISTS generation_jobs (
                    id                 TEXT PRIMARY KEY,
                    user_id            TEXT NOT NULL,
                    platform           TEXT NOT NULL,
                    workflow_id        INTEGER,
                    modality           TEXT NOT NULL DEFAULT 'image',
                    sections_json      TEXT,
                    result_kinds       TEXT,
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
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_status_next ON generation_jobs(status, next_attempt_at)",
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_user_created ON generation_jobs(user_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_modality ON generation_jobs(modality, status)",
                """CREATE TABLE IF NOT EXISTS generation_job_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id       TEXT NOT NULL,
                    from_status  TEXT,
                    to_status    TEXT NOT NULL,
                    agent_id     TEXT,
                    detail_json  TEXT,
                    occurred_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                "CREATE INDEX IF NOT EXISTS idx_generation_job_events_job ON generation_job_events(job_id, occurred_at)",
                # 既存 image_jobs → generation_jobs へ全行コピー（modality='image'）
                """INSERT INTO generation_jobs
                      (id, user_id, platform, workflow_id, modality, sections_json, result_kinds,
                       positive, negative, params_json, status, assigned_agent, priority, progress,
                       error_message, result_paths, retry_count, max_retries, last_error,
                       cache_sync_id, next_attempt_at, dispatcher_lock_at, timeout_at,
                       created_at, started_at, finished_at)
                   SELECT id, user_id, platform, workflow_id, 'image', NULL, NULL,
                          positive, negative, params_json, status, assigned_agent, priority, progress,
                          error_message, result_paths, retry_count, max_retries, last_error,
                          cache_sync_id, next_attempt_at, dispatcher_lock_at, timeout_at,
                          created_at, started_at, finished_at
                     FROM image_jobs""",
                """INSERT INTO generation_job_events
                      (id, job_id, from_status, to_status, agent_id, detail_json, occurred_at)
                   SELECT id, job_id, from_status, to_status, agent_id, detail_json, occurred_at
                     FROM image_job_events""",
                # 旧テーブルは DROP し、同名 View で後方互換を残す（sqlite3 CLI 等からの SELECT 用）
                "DROP INDEX IF EXISTS idx_image_jobs_status_next",
                "DROP INDEX IF EXISTS idx_image_jobs_user_created",
                "DROP INDEX IF EXISTS idx_image_job_events_job",
                "DROP TABLE IF EXISTS image_jobs",
                "DROP TABLE IF EXISTS image_job_events",
                """CREATE VIEW IF NOT EXISTS image_jobs AS
                     SELECT id, user_id, platform, workflow_id, positive, negative, params_json,
                            status, assigned_agent, priority, progress, error_message, result_paths,
                            retry_count, max_retries, last_error, cache_sync_id, next_attempt_at,
                            dispatcher_lock_at, timeout_at, created_at, started_at, finished_at
                       FROM generation_jobs
                      WHERE modality = 'image'""",
                """CREATE VIEW IF NOT EXISTS image_job_events AS
                     SELECT id, job_id, from_status, to_status, agent_id, detail_json, occurred_at
                       FROM generation_job_events""",
                # === セクション合成プリセット ===
                # カテゴリ: quality / style / character / composition / scene / lora_trigger / negative
                # is_builtin=1 はユーザー削除不可（API で 400 を返す）
                """CREATE TABLE IF NOT EXISTS prompt_section_categories (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    key           TEXT NOT NULL UNIQUE,
                    label         TEXT NOT NULL,
                    description   TEXT,
                    display_order INTEGER NOT NULL DEFAULT 100,
                    is_builtin    INTEGER NOT NULL DEFAULT 0,
                    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                # セクション本体（1 カテゴリ内に複数定義）
                # positive/negative は作品タグ列。weight 記法 `(tag:1.2)` もそのまま入れる。
                """CREATE TABLE IF NOT EXISTS prompt_sections (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_key  TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    description   TEXT,
                    positive      TEXT,
                    negative      TEXT,
                    tags          TEXT,
                    is_builtin    INTEGER NOT NULL DEFAULT 0,
                    starred       INTEGER NOT NULL DEFAULT 0,
                    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (category_key, name)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_prompt_sections_category ON prompt_sections(category_key, starred DESC, name)",
                # ビルトイン 7 カテゴリ（display_order に空き番号を残してユーザー追加カテゴリが差し込めるようにする）
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('quality',      '品質',           '全体クオリティ・美麗系', 10,  1)",
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('style',        'スタイル',       '作風・画風',             20,  1)",
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('character',    'キャラクター',   'キャラ指定・外見',       30,  1)",
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('composition',  '構図',           'ポーズ・アングル',       40,  1)",
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('scene',        'シーン',         '背景・場面・小物',       50,  1)",
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('lora_trigger', 'LoRA トリガー',  'LoRA 呼び出しトークン',  80,  1)",
                "INSERT OR IGNORE INTO prompt_section_categories (key, label, description, display_order, is_builtin) VALUES ('negative',     'ネガティブ',     '抑制したい要素',         100, 1)",
            ],
            29: [
                # Gallery 強化: お気に入り / タグ
                # favorite=1 のジョブはギャラリーで ⭐ 表示・絞り込み可
                # tags は JSON 配列文字列（["風景","夜景"] 等）
                "ALTER TABLE generation_jobs ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE generation_jobs ADD COLUMN tags TEXT",
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_favorite ON generation_jobs(favorite, created_at DESC)",
            ],
            30: [
                # セクション選択 + ユーザー追記プロンプト + 挿入位置を一つのプリセットとして保存
                # payload_json には {section_ids, user_positive, user_negative, user_position} を格納
                """CREATE TABLE IF NOT EXISTS prompt_section_presets (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT NOT NULL UNIQUE,
                    description  TEXT,
                    payload_json TEXT NOT NULL,
                    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
            ],
            31: [
                # Wildcard / Dynamic Prompts のファイル辞書
                # `__hair__` のようなファイル参照で引かれる。name が主キーで upsert 運用。
                # content は改行区切りの候補行（`#` 始まり・空行はコメント扱い）
                """CREATE TABLE IF NOT EXISTS wildcard_files (
                    name        TEXT PRIMARY KEY,
                    content     TEXT NOT NULL DEFAULT '',
                    description TEXT,
                    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
            ],
            32: [
                # === auto-kirinuki（配信アーカイブ切り抜き）ジョブキュー ===
                # Dispatcher 状態機械: queued → dispatching → warming_cache → running → done/failed/cancelled
                # step カラムは Agent 側の実行ステップ（preprocess/transcribe/analyze/emotion/highlight/edl/clips）
                """CREATE TABLE IF NOT EXISTS clip_pipeline_jobs (
                    id             TEXT PRIMARY KEY,
                    user_id        TEXT NOT NULL,
                    platform       TEXT NOT NULL,
                    status         TEXT NOT NULL DEFAULT 'queued',
                    assigned_agent TEXT,
                    video_path     TEXT NOT NULL,
                    output_dir     TEXT NOT NULL,
                    mode           TEXT NOT NULL DEFAULT 'normal',
                    whisper_model  TEXT NOT NULL,
                    ollama_model   TEXT NOT NULL,
                    params_json    TEXT,
                    step           TEXT,
                    progress       INTEGER NOT NULL DEFAULT 0,
                    result_json    TEXT,
                    last_error     TEXT,
                    retry_count    INTEGER NOT NULL DEFAULT 0,
                    max_retries    INTEGER NOT NULL DEFAULT 2,
                    cache_sync_id  TEXT,
                    next_attempt_at    DATETIME,
                    dispatcher_lock_at DATETIME,
                    timeout_at         DATETIME,
                    created_at     TEXT NOT NULL,
                    started_at     TEXT,
                    finished_at    TEXT
                )""",
                "CREATE INDEX IF NOT EXISTS idx_clip_jobs_status ON clip_pipeline_jobs(status, next_attempt_at)",
                "CREATE INDEX IF NOT EXISTS idx_clip_jobs_user ON clip_pipeline_jobs(user_id, created_at DESC)",
                # ジョブ遷移イベントログ
                """CREATE TABLE IF NOT EXISTS clip_pipeline_job_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id      TEXT NOT NULL,
                    from_status TEXT,
                    to_status   TEXT NOT NULL,
                    agent_id    TEXT,
                    detail_json TEXT,
                    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                "CREATE INDEX IF NOT EXISTS idx_clip_job_events_job ON clip_pipeline_job_events(job_id, occurred_at)",
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
