# WebGUI DB & Config リファレンス

> 調査日: 2026-04-09

---

## Database Schema (v14)

### コアテーブル

#### memos
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| content | TEXT | NOT NULL |
| tags | TEXT | |
| user_id | TEXT | DEFAULT '' |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP |

#### todos
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| title | TEXT | NOT NULL |
| done | BOOLEAN | DEFAULT 0 |
| user_id | TEXT | DEFAULT '' |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP |
| done_at | DATETIME | |
| due_date | DATETIME | |

#### reminders
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| message | TEXT | NOT NULL |
| remind_at | DATETIME | NOT NULL |
| repeat_type | TEXT | |
| repeat_interval | INTEGER | |
| active | BOOLEAN | DEFAULT 1 |
| notified | BOOLEAN | DEFAULT 0 |
| user_id | TEXT | DEFAULT '' |
| done_at | DATETIME | |
| snooze_count | INTEGER | DEFAULT 0 |
| last_snoozed_at | TEXT | |
| snoozed_until | TEXT | |

#### weather_subscriptions
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| user_id | TEXT | NOT NULL |
| location | TEXT | NOT NULL |
| latitude | REAL | NOT NULL |
| longitude | REAL | NOT NULL |
| notify_hour | INTEGER | DEFAULT 7 |
| notify_minute | INTEGER | DEFAULT 0 |
| active | BOOLEAN | DEFAULT 1 |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP |

### ログテーブル

#### conversation_log
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| timestamp | DATETIME | DEFAULT CURRENT_TIMESTAMP |
| channel | TEXT | NOT NULL |
| channel_name | TEXT | DEFAULT '' |
| role | TEXT | NOT NULL |
| content | TEXT | NOT NULL |
| user_id | TEXT | DEFAULT '' |
| mode | TEXT | |
| unit | TEXT | |

#### llm_log
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| timestamp | DATETIME | NOT NULL |
| provider | TEXT | NOT NULL |
| model | TEXT | NOT NULL |
| purpose | TEXT | NOT NULL |
| prompt_text | TEXT | |
| system_text | TEXT | |
| response_text | TEXT | |
| prompt_len | INTEGER | DEFAULT 0 |
| response_len | INTEGER | DEFAULT 0 |
| duration_ms | INTEGER | DEFAULT 0 |
| success | BOOLEAN | DEFAULT 1 |
| error | TEXT | |
| tokens_per_sec | REAL | |
| eval_count | INTEGER | |
| prompt_eval_count | INTEGER | |

#### conversation_summary
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| summary | TEXT | NOT NULL |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP |

### InnerMindテーブル

#### mimi_monologue
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| monologue | TEXT | NOT NULL |
| mood | TEXT | |
| did_notify | BOOLEAN | DEFAULT 0 |
| notified_message | TEXT | |
| created_at | DATETIME | NOT NULL |

#### mimi_self_model
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| key | TEXT | NOT NULL |
| value | TEXT | NOT NULL |
| updated_at | DATETIME | NOT NULL |

### RSSテーブル

#### rss_feeds
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| url | TEXT | UNIQUE NOT NULL |
| title | TEXT | NOT NULL |
| category | TEXT | NOT NULL |
| is_preset | INTEGER | DEFAULT 0 |
| added_by | TEXT | |
| created_at | TEXT | DEFAULT (datetime('now')) |

#### rss_articles
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| feed_id | INTEGER | REFERENCES rss_feeds(id) |
| title | TEXT | NOT NULL |
| url | TEXT | UNIQUE NOT NULL |
| summary | TEXT | |
| published_at | TEXT | |
| fetched_at | TEXT | DEFAULT (datetime('now')) |

#### rss_user_prefs / rss_feedback
- ユーザー別フィード設定・記事評価

### STTテーブル

#### stt_transcripts
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| raw_text | TEXT | NOT NULL |
| started_at | TEXT | NOT NULL |
| ended_at | TEXT | NOT NULL |
| duration_seconds | REAL | |
| collected_at | TEXT | DEFAULT (datetime('now')) |
| summarized | INTEGER | DEFAULT 0 |

#### stt_summaries
| Column | Type | 制約 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| summary | TEXT | NOT NULL |
| transcript_ids | TEXT | NOT NULL |
| created_at | TEXT | DEFAULT (datetime('now')) |

### 設定テーブル

#### settings (Key-Valueストア)
| Column | Type | 制約 |
|--------|------|------|
| key | TEXT | PRIMARY KEY |
| value | TEXT | NOT NULL |

ランタイム設定の保存に使用。WebGUIからの設定変更はここに保存される。

---

## config.yaml 主要セクション

### LLM設定 (`llm`)
- `ollama_model`: デフォルトモデル名
- `ollama_timeout`: 生成タイムアウト（秒）
- `ollama_url`: Ollama URL（省略時は自動構築）

### ハートビート (`heartbeat`)
- `interval_with_ollama_minutes`: Ollama有効時の間隔（15分）
- `interval_without_ollama_minutes`: Ollama無効時の間隔（180分）
- `compact_threshold_messages`: 圧縮閾値（20メッセージ）

### Windows Agents (`windows_agents`)
- `id`, `name`, `role`, `host`, `port`, `priority`
- `metrics_instance`: VictoriaMetrics用
- `wol_device_id`: Wake-on-LAN用

### ユニット (`units`)
各ユニットの `enabled` フラグ + 個別設定

### InnerMind (`inner_mind`)
- `enabled`, `thinking_interval_ticks`, `speak_probability`
- `min_speak_interval_minutes`, `speak_channel_id`, `target_user_id`

### Gemini (`gemini`)
- `conversation`, `memory_extraction`, `unit_routing`: 各トグル
- `monthly_token_limit`: 月間トークン制限

### キャラクター (`character`)
- `name`: "ミミ"
- `persona`: ペルソナ定義テキスト

### その他
- `rss`: フェッチ間隔, ダイジェスト時刻, プリセット
- `stt`: ポーリング間隔, キャプチャ設定, Whisperモデル
- `weather`: API URL, デフォルト地域
- `searxng`: SearXNG URL, 結果数
- `rakuten_search`: 最大結果数, 詳細取得

---

## 環境変数 (.env)

| 変数名 | 用途 |
|--------|------|
| GEMINI_API_KEY | Google Gemini APIキー |
| DISCORD_BOT_TOKEN | Discordボットトークン |
| DISCORD_ADMIN_CHANNEL_ID | 管理通知チャンネル |
| WEBGUI_USERNAME | WebGUI認証ユーザー名 |
| WEBGUI_PASSWORD | WebGUI認証パスワード |
| WEBGUI_PORT | WebGUIポート（8100） |
| WEBGUI_USER_ID | WebGUI用DiscordユーザーID |
| PORTAINER_URL / API_TOKEN / ENV_ID | Portainer連携 |
| CONTAINER_NAME | コンテナ名 |
| GITEA_URL / USER / TOKEN | Gitea連携 |
| GOOGLE_SERVICE_ACCOUNT_FILE | Googleカレンダー認証 |
| AGENT_SECRET_TOKEN | Windows Agent認証トークン |
