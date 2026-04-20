"""スキーマ定義・初期化・fingerprint ヘルパ（zzz_disc models 共通基盤）。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS zzz_characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name_ja TEXT NOT NULL,
  element TEXT,
  faction TEXT,
  icon_url TEXT,
  display_order INTEGER DEFAULT 0,
  hoyolab_agent_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_set_masters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name_ja TEXT NOT NULL,
  aliases_json TEXT,
  two_pc_effect TEXT,
  four_pc_effect TEXT
);

CREATE TABLE IF NOT EXISTS zzz_discs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 6),
  set_id INTEGER REFERENCES zzz_set_masters(id),
  main_stat_name TEXT NOT NULL,
  main_stat_value REAL NOT NULL,
  sub_stats_json TEXT NOT NULL,
  level INTEGER DEFAULT 0,
  rarity TEXT,
  fingerprint TEXT,
  hoyolab_disc_id TEXT,
  source_image_path TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_builds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  character_id INTEGER NOT NULL REFERENCES zzz_characters(id),
  name TEXT NOT NULL,
  tag TEXT,
  rank TEXT,
  notes TEXT,
  is_current INTEGER NOT NULL DEFAULT 0,
  stats_json TEXT,
  synced_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zzz_builds_char ON zzz_builds(character_id, is_current);
CREATE UNIQUE INDEX IF NOT EXISTS idx_zzz_builds_current ON zzz_builds(character_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS zzz_build_slots (
  build_id INTEGER NOT NULL REFERENCES zzz_builds(id) ON DELETE CASCADE,
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 6),
  disc_id INTEGER REFERENCES zzz_discs(id),
  PRIMARY KEY(build_id, slot)
);
CREATE INDEX IF NOT EXISTS idx_zzz_build_slots_disc ON zzz_build_slots(disc_id);

CREATE TABLE IF NOT EXISTS zzz_hoyolab_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE NOT NULL,
  region TEXT NOT NULL,
  ltuid_v2 TEXT NOT NULL,
  ltoken_v2 TEXT NOT NULL,
  nickname TEXT,
  last_synced_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_team_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT,
  display_order INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zzz_teams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER REFERENCES zzz_team_groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  display_order INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zzz_teams_group ON zzz_teams(group_id, display_order);

CREATE TABLE IF NOT EXISTS zzz_team_slots (
  team_id INTEGER NOT NULL REFERENCES zzz_teams(id) ON DELETE CASCADE,
  position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 2),
  character_id INTEGER REFERENCES zzz_characters(id),
  build_id INTEGER REFERENCES zzz_builds(id),
  PRIMARY KEY(team_id, position)
);
CREATE INDEX IF NOT EXISTS idx_zzz_team_slots_build ON zzz_team_slots(build_id);

CREATE TABLE IF NOT EXISTS zzz_extraction_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  image_path TEXT,
  extracted_json TEXT,
  normalized_json TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zzz_jobs_status ON zzz_extraction_jobs(status, created_at);
"""


async def _maybe_add_column(db, table: str, column: str, coldef: str) -> None:
    info = await db.fetchall(f"PRAGMA table_info({table})")
    if column not in {r["name"] for r in info}:
        await db.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")
        await db.db.commit()


async def init_schema(db) -> None:
    """CREATE TABLE IF NOT EXISTS + 既存 DB へのカラム追加 + インデックス。

    インデックス作成は fingerprint 列が確実に存在した後に行う。
    """
    await db.db.executescript(_SCHEMA_SQL)
    await db.db.commit()
    # 既存環境への追加カラム（fingerprint等は Phase 1 時点では無かった）
    await _maybe_add_column(db, "zzz_discs", "level", "INTEGER DEFAULT 0")
    await _maybe_add_column(db, "zzz_discs", "rarity", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "fingerprint", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "hoyolab_disc_id", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "icon_url", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "name", "TEXT")
    await _maybe_add_column(db, "zzz_discs", "is_pinned", "INTEGER DEFAULT 0")
    await _maybe_add_column(db, "zzz_characters", "hoyolab_agent_id", "TEXT")
    await _maybe_add_column(db, "zzz_characters", "recommended_substats_json", "TEXT")
    await _maybe_add_column(db, "zzz_characters", "recommended_disc_sets_json", "TEXT")
    # スキル情報（手動入力）
    await _maybe_add_column(db, "zzz_characters", "skills_json", "TEXT")
    await _maybe_add_column(db, "zzz_characters", "skill_summary", "TEXT")
    # ネットから取得したオススメステータス・ディスクのフリーテキスト
    await _maybe_add_column(db, "zzz_characters", "recommended_notes", "TEXT")
    # オススメ編成（メモ）: 編成例・シナジーをフリーテキストで残す
    await _maybe_add_column(db, "zzz_characters", "recommended_team_notes", "TEXT")
    # 推奨メインステ（slot 5/6/7）フィルタ用: {"5": ["攻撃力%"], "6": [...], "7": [...]}
    await _maybe_add_column(db, "zzz_characters", "recommended_main_stats_json", "TEXT")
    # オススメ編成（構造化・複数可）: [{"members": [...], "note": ""}]
    await _maybe_add_column(db, "zzz_characters", "recommended_teams_json", "TEXT")
    # 音動機（W-Engine）情報を build に保存
    await _maybe_add_column(db, "zzz_builds", "w_engine_json", "TEXT")
    # HoYoLAB 自動ログイン用（平文・自宅 Pi 前提）
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "email", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "password", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "auto_login_enabled", "INTEGER DEFAULT 0")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "last_auto_login_at", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "last_auto_login_error", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "account_mid_v2", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "account_id_v2", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "cookie_token_v2", "TEXT")
    await _maybe_add_column(db, "zzz_hoyolab_accounts", "ltmid_v2", "TEXT")
    # fingerprint 用 UNIQUE インデックスは列追加後に作成
    await db.db.executescript(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_zzz_discs_fingerprint "
        "ON zzz_discs(fingerprint) WHERE fingerprint IS NOT NULL;"
    )
    await db.db.commit()
    # 既存ディスクに fingerprint を埋める
    await _backfill_fingerprints(db)


def compute_fingerprint(slot: int, set_id: int | None,
                        main_stat_name: str, main_stat_value: float,
                        sub_stats: list[dict]) -> str:
    """物理的に同じディスクを識別するための決定論的ハッシュ。

    (slot, set_id, main_stat, 各サブステ{name,value,upgrades}) から計算。
    sub_stats は name の辞書順に正規化。
    """
    # NOTE: upgrades (強化回数) は value から導出される派生値のため
    # fingerprint には含めない（再同期の冪等性を保つ）
    subs_norm = sorted(
        [
            [s.get("name") or "",
             round(float(s.get("value", 0) or 0), 3)]
            for s in (sub_stats or [])
        ],
        key=lambda x: x[0],
    )
    payload = json.dumps(
        [slot, set_id, main_stat_name,
         round(float(main_stat_value or 0), 3), subs_norm],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def _backfill_fingerprints(db) -> None:
    rows = await db.fetchall(
        "SELECT id, slot, set_id, main_stat_name, main_stat_value, sub_stats_json "
        "FROM zzz_discs WHERE fingerprint IS NULL OR fingerprint = ''"
    )
    for r in rows:
        subs = json.loads(r["sub_stats_json"] or "[]")
        fp = compute_fingerprint(
            r["slot"], r["set_id"], r["main_stat_name"],
            r["main_stat_value"], subs,
        )
        try:
            await db.execute(
                "UPDATE zzz_discs SET fingerprint = ? WHERE id = ?",
                (fp, r["id"]),
            )
        except Exception:
            # 既存ディスクに重複がある場合は先に見つけたものを残し、後続はスキップ
            pass
