# WebGUI API リファレンス

> 調査日: 2026-04-09
> 対象: `src/web/app.py` (75+ endpoints)

---

## 1. Health & Status

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/health` | ヘルスチェック（認証不要） | - | `status`, `version`, `uptime` |
| GET | `/api/status` | 総合ステータス | - | `version`, `uptime`, `ollama`, `agents[]`, `db`, `memory` |
| GET | `/api/ollama-models` | Ollamaモデル一覧 | - | `models[]` |
| POST | `/api/ollama-recheck` | Ollama接続再チェック | - | `ollama_available` |

## 2. Chat & Conversation

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| POST | `/api/chat` | メッセージ送信 | `message`, `reply_unit`(任意) | `flow_id` |
| GET | `/api/logs` | 会話ログ取得 | `limit`, `offset`, `keyword`, `channel`, `bot_only` | `logs[]` |

## 3. Maintenance & Updates

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| POST | `/api/update-code` | Git pull + サブモジュール更新 + Agent/コンテナ再起動 | - | `updated`, `message`, `restarted`, `restart_detail`, `agents[]` |
| POST | `/api/restart` | コンテナ再起動 | - | `restarted`, `detail` |
| POST | `/api/delegation-mode` | Agent委任モード変更 | `agent_id`, `mode`("allow"/"deny"/"auto") | `ok` |

## 4. Reminders CRUD

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/units/reminders` | リマインダー一覧 | `active`(任意) | `items[]` |
| PUT | `/api/units/reminders/{rid}` | リマインダー更新 | `message`, `remind_at`(ISO) | `ok` |
| POST | `/api/units/reminders/{rid}/done` | 完了にする | - | `ok` |
| DELETE | `/api/units/reminders/{rid}` | 削除 | - | `ok` |

## 5. Todos CRUD

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/units/todos` | Todo一覧 | `done`(任意) | `items[]` |
| PUT | `/api/units/todos/{tid}` | Todo更新 | `title`, `due_date`(任意) | `ok` |
| POST | `/api/units/todos/{tid}/done` | 完了にする | - | `ok` |
| DELETE | `/api/units/todos/{tid}` | 削除 | - | `ok` |

## 6. Memos CRUD

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/units/memos` | メモ一覧 | `keyword`(任意) | `items[]` |
| PUT | `/api/units/memos/{mid}` | メモ更新 | `content`, `tags`(任意) | `ok` |
| POST | `/api/units/memos/{mid}/append` | メモに追記 | `content` | `ok` |
| DELETE | `/api/units/memos/{mid}` | 削除 | - | `ok` |

## 7. Weather Subscriptions

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/units/weather` | 天気購読一覧 | `active`(任意) | `items[]` |
| PUT | `/api/units/weather/{wid}` | 購読更新 | `notify_hour`, `notify_minute`, `location`(任意) | `ok` |
| DELETE | `/api/units/weather/{wid}` | 削除 | - | `ok` |
| POST | `/api/units/weather/{wid}/toggle` | 有効/無効切替 | - | `ok`, `active` |

## 8. Timers & Units

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/units/timers` | アクティブタイマー一覧 | - | `items[]`(`id`, `message`, `minutes`, `remaining_sec`) |
| GET | `/api/units/loaded` | ロード済みユニット一覧 | - | `units[]`(`name`, `description`, `delegate_to`, `breaker_state`) |

## 9. Memory (ChromaDB)

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/memory/{collection}` | メモリアイテム取得 | `limit`, `offset` | `items[]`, `total` |
| DELETE | `/api/memory/{collection}/{doc_id}` | メモリアイテム削除 | - | `ok` |

> collection: `ai_memory`, `people_memory`, `conversation_log`

## 10. LLM Configuration

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/llm-config` | LLM設定取得 | - | `ollama_model`, `ollama_timeout`, `gemini_model`, `unit_models` |
| POST | `/api/llm-config` | LLM設定変更 | 同上 | `ok` |
| GET | `/api/debug/llm-state` | LLMデバッグ情報 | - | `ollama_available`, `gemini_config`, `units{}` |

## 11. Gemini Configuration

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/gemini-config` | Gemini設定取得 | - | `conversation`, `memory_extraction`, `unit_routing`, `monthly_token_limit` |
| POST | `/api/gemini-config` | Gemini設定変更 | 同上 | `ok` |
| GET | `/api/unit-gemini` | ユニット別Gemini許可 | - | `{unit_name: bool}` |
| POST | `/api/unit-gemini` | ユニット別Gemini許可設定 | `unit`, `allowed` | `ok` |

## 12. Logs

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/logs` | 会話ログ | `limit`, `offset`, `keyword`, `channel`, `bot_only` | `logs[]` |
| GET | `/api/logs/llm` | LLMログ(Ollama/Gemini) | `limit`, `offset`, `provider`(任意) | `logs[]` |
| GET | `/api/debug/heartbeat-logs` | ハートビートログ | - | `logs[]` |

## 13. Rakuten Search

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/rakuten-config` | 楽天検索設定 | - | `max_results`, `fetch_details` |
| POST | `/api/rakuten-config` | 楽天検索設定変更 | 同上 | `ok` |
| GET | `/api/debug/rakuten-search` | 楽天検索デバッグ | - | `available`, `data` |

## 14. Chat Unit Config

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/chat-config` | Chat設定 | - | `history_minutes` |
| POST | `/api/chat-config` | Chat設定変更 | 同上 | `ok` |

## 15. Heartbeat Config

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/heartbeat-config` | ハートビート設定 | - | `interval_with_ollama_minutes`, `interval_without_ollama_minutes`, `compact_threshold_messages` |
| POST | `/api/heartbeat-config` | ハートビート設定変更 | 同上 | `ok` |

## 16. Character & Persona

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/persona` | ペルソナ取得 | - | `persona` |
| POST | `/api/persona` | ペルソナ設定 | `persona` | `ok` |

## 17. Flow Tracking

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/flow/state` | フロー状態 | - | (tracker依存) |
| GET | `/api/flow/stream` | SSEストリーム | - | `data: {event_json}` |

## 18. Monologue & InnerMind

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/monologue` | モノローグ一覧 | `limit` | `monologues[]` |
| GET | `/api/inner-mind/status` | InnerMindステータス | - | `self_model`, `last_monologue`, `activity`, `enabled` |
| GET | `/api/inner-mind/context` | InnerMindコンテキストソース | - | `sources[]`(`name`, `text`) |
| GET | `/api/inner-mind/settings` | InnerMind設定取得 | - | `enabled`, `speak_probability`, `min_speak_interval_minutes`, `thinking_interval_ticks`, `speak_channel_id`, `target_user_id` |
| POST | `/api/inner-mind/settings` | InnerMind設定変更 | 同上 | `ok` |

## 19. Input Relay (Windows Agent Tool)

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| POST | `/api/tools/input-relay/update` | 全Agentのinput-relay更新 | - | `agents[]` |
| GET | `/api/tools/input-relay/status` | input-relayステータス | - | `agents[]` |
| GET | `/api/tools/input-relay/logs/{role}` | input-relayログ | `lines` | Agent固有 |
| POST | `/api/tools/input-relay/start/{role}` | input-relay開始 | - | Agent固有 |
| POST | `/api/tools/input-relay/stop/{role}` | input-relay停止 | - | Agent固有 |
| POST | `/api/tools/input-relay/restart/{role}` | input-relay再起動 | - | Agent固有 |

## 20. STT (Speech-to-Text)

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/stt/status` | STTステータス | - | `agents[]` |
| GET | `/api/stt/devices` | マイクデバイス一覧 | `role`(default:"sub") | Agent固有 |
| POST | `/api/stt/control` | STT制御(init/start/stop/set_device) | `role`, + 制御パラメータ | Agent固有 |
| GET | `/api/stt/model/status` | Whisperモデル状態 | - | `loaded` |
| GET | `/api/stt/transcripts` | 最新文字起こし | `role` | `transcripts[]` |

## 21. OBS

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/obs/games` | ゲームプロセス/グループ | - | `games[]`, `groups[]` |
| POST | `/api/obs/games` | ゲーム設定保存 | ゲーム設定オブジェクト | Agent固有 |
| GET | `/api/obs/status` | OBS接続状態 | - | `obs_connected` |
| GET | `/api/obs/logs` | OBSログ | `lines` | `logs[]` |

## 22. RSS Feed

| Method | Path | 説明 | パラメータ | レスポンス |
|--------|------|------|-----------|-----------|
| GET | `/api/rss/feeds` | フィード一覧 | - | `feeds[]`, `categories{}` |
| POST | `/api/rss/feeds` | フィード追加 | `url`, `title`(任意), `category`(任意) | `ok` |
| DELETE | `/api/rss/feeds/{feed_id}` | フィード削除 | - | `ok` |
| GET | `/api/rss/articles` | 記事一覧 | `category`(任意), `limit` | `articles[]` |
| POST | `/api/rss/fetch` | 手動フェッチ | - | fetch結果 |
