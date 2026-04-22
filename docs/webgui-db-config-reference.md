# WebGUI DB & Config リファレンス

> 最終更新: 2026-04-23
> 実装: `src/database/`（スキーマ定義＋マイグレーション）/ `config.yaml.example`

## Database Schema（現行 v35）

`_SCHEMA_VERSION` は `src/database/_base.py` で管理されている。`DatabaseBase._maybe_migrate()` が
`PRAGMA user_version` を見て逐次マイグレーションを流す方式。テーブルごとのアクセサは
`src/database/<topic>.py` に分割されている（`conversation.py` / `generation.py` / `clip_pipeline.py` /
`lora.py` / `monologue.py` / `pending.py` / `section.py` / `settings.py` / `wildcard.py`）。

### ドメイン別テーブル一覧

#### 基本ユニット
- `memos` / `todos` / `reminders` / `weather_subscriptions` / `calendar_settings`

#### 会話・ログ
- `conversation_log`（`channel / channel_name / role / content / user_id / mode / unit`）
- `conversation_summary`（Heartbeat のコンテキスト圧縮結果）
- `llm_log`（provider/model/purpose/prompt/response/duration/tokens_per_sec/eval_count 等）

#### InnerMind
- `mimi_monologue`（monologue/mood/did_notify/notified_message）
- `mimi_self_model`（Key-Value 型）
- `pending_actions`（Actuator の承認待ちキュー）

#### RSS
- `rss_feeds` / `rss_articles` / `rss_user_prefs` / `rss_feedback`

#### STT
- `stt_transcripts` / `stt_summaries`

#### Docker Log Monitor
- `docker_log_exclusions` / `docker_error_log`

#### Activity
- `activity_samples`（Agent /activity の生サンプル、短期保持）
- `game_sessions` / `foreground_sessions`（連続 FG の集計、永続）
- `obs_sessions`（OBS の recording/streaming session）
- `daily_diaries`（1日の活動ダイジェスト）

#### Calendar（読み取り同期）
- `calendar_read_sources` / `calendar_events`

#### 画像生成（prompt_crafter / image_gen）
- `prompt_templates` / `prompt_sessions`
- `image_jobs` / `image_job_events`（旧系統）
- `generation_jobs` / `generation_job_events`（新系統）
- `prompt_section_categories` / `prompt_sections` / `prompt_section_presets`
- `wildcard_files`（v31 以降。Wildcard / Dynamic Prompts の辞書 `name` PK）
- `image_collections` / `image_collection_items`
- `workflows`（ComfyUI ワークフロー JSON 保管）
- `model_cache_manifest`（Agent の /capability 結果）

#### LoRA 学習
- `lora_projects` / `lora_dataset_items` / `lora_train_jobs` / `lora_config_templates`

#### Clip Pipeline（自動切り抜き）
- `clip_pipeline_jobs` / `clip_pipeline_job_events`

#### 設定
- `settings` (Key-Value) — ランタイム設定や UI 設定を格納。WebGUI 経由で書き換わる値は
  基本的にこのテーブルに入る（例: LLM 設定、InnerMind 設定、RSS トグル、Gemini トグル …）。

### マイグレーションの流れ
- 新規カラム / 新規テーブルは `_INIT_SQL` にも同梱しつつ、`_MIGRATIONS` 辞書に
  `old_version → new_version` のステップを登録する。
- 既存 DB は `_maybe_migrate()` が再生して追加する。
- 新規 DB は `_INIT_SQL` 一括 + `user_version = _SCHEMA_VERSION` をセットして初期化する。

> テーブル一覧は増えやすいため、常に `src/database/_base.py` を第一ソースとする。

---

## `config.yaml` 主要セクション

実体は `config.yaml.example` を参照。ここでは役割の概観のみ示す。

### LLM ― `llm`
- `ollama_model` / `ollama_timeout` / `ollama_cooldown_sec` / `ollama_url`
- `gpu_memory_skip_bytes` — GPU 占有中と見なして Ollama インスタンスを除外する閾値
  （`llm.gpu_monitor` + VictoriaMetrics で判定）

### Heartbeat ― `heartbeat`
- `interval_with_ollama_minutes` / `interval_without_ollama_minutes` / `compact_threshold_messages`

### Memory ― `memory`
- `dedup_skip_threshold` / `dedup_merge_threshold` / `sweep_stale_days` / `sweep_enabled`

### Activity ― `activity`
- `poll_interval_seconds` / `sample_retention_days` / `idle_timeout_seconds`
- `game_end_close_delay_seconds`（ゲーム再起動の吸収）
- `stt_flush_on_fg_change_min_duration_seconds`
- `input_relay.sender_url` / `block_rules` / `habit`（習慣ゲームプロフィール・離脱判定）

### Metrics ― `metrics.victoria_metrics_url`
Grafana スタック流用。GPU / CPU / Agent 可用性に使用。

### Windows Agents ― `windows_agents`
- `id` / `name` / `role`（"main" / "sub" / etc.）/ `host` / `port` / `priority`
- `metrics_instance`（VictoriaMetrics 用）
- `wol_device_id`（WoL ツールのデバイス ID）
- `comfyui_public_url`（Cloudflare Tunnel 経由公開 URL、任意）

### 委託閾値 ― `delegation.thresholds`
`cpu_percent` / `memory_percent` / `gpu_percent` を超えると委託を拒否する。

### ユニット ― `units`
`reminder / memo / timer / status / chat / web_search / rakuten_search / weather / calendar /
rss / power / docker_log_monitor / image_gen / lora_train / prompt_crafter / model_sync / clip_pipeline`
の enabled + 個別パラメータ。`chat.history_minutes`, `calendar.timezone`, `power.shutdown_delay`
など。ユニットごとに `llm:` を上書き可能（`ollama_only`, `ollama_model`, `gemini_model`）。

### Tools（同居サブツール）― `tools`
- `zzz_disc.delegate_to` / `capture.backend=mss|obs` / `queue.max_concurrent`
  — ZZZ Disc Manager（HoYoLAB 連携）

### RSS ― `rss`
`fetch_interval_minutes` / `digest_hour` / `article_retention_days` / `max_articles_per_category` /
`presets`（gaming / tech / pc / vr / news など既定フィード群）。

### Calendar 読み取り同期 ― `calendar.read_sync`
`enabled` / `sync_interval_minutes` / `lookahead_days` / `inject_hours`。
書き込みは `units.calendar` 側。

### Docker Log Monitor ― `docker_monitor`
`check_interval_seconds` / `cooldown_minutes` / `containers` / `max_lines_per_check`。

### STT ― `stt`
- `polling_interval_minutes`
- `capture`（device / sample_rate / VAD / 閾値類）
- `batch.interval_minutes`
- `processing`（要約閾値 / silence_trigger / gap_split / retention）
- `model`（`kotoba-tech/kotoba-whisper-v2.0`, device=cuda, unload_after_minutes）

### WoL ツール ― `wol.url`

### InnerMind ― `inner_mind`
- `thinking_interval_ticks` / `min_speak_interval_minutes` / `speak_channel_id` / `target_user_id`
- `active_threshold_minutes`
- `salience.top_n` / `salience.threshold`（注意フィルタ）
- `github`（GITHUB_TOKEN + username が必要）
- `tavily_news`（TAVILY_API_KEY + queries 必要）

### Gemini トグル ― `gemini`
`conversation` / `memory_extraction` / `unit_routing` / `monthly_token_limit`（全デフォルト OFF）

### キャラクター ― `character`
`name` / `persona`（ミミの人格定義テキスト）

---

## 環境変数（`.env`）

| 変数名 | 用途 |
|--------|------|
| `GEMINI_API_KEY` | Google Gemini APIキー（Geminiフォールバック用） |
| `DISCORD_BOT_TOKEN` | Discord Bot トークン |
| `DISCORD_ADMIN_CHANNEL_ID` | 管理通知チャンネル |
| `WEBGUI_USERNAME` / `WEBGUI_PASSWORD` | Basic 認証 |
| `WEBGUI_PORT` | WebGUI 公開ポート（既定 8100） |
| `WEBGUI_USER_ID` | WebGUI 用の仮想 Discord ユーザー ID |
| `PORTAINER_URL` / `PORTAINER_API_TOKEN` / `PORTAINER_ENV_ID` | Portainer 経由の再起動 |
| `CONTAINER_NAME` | 再起動対象コンテナ名 |
| `GITEA_URL` / `GITEA_USER` / `GITEA_TOKEN` | Gitea サブモジュール連携（input-relay 等） |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Google Calendar 認証 |
| `AGENT_SECRET_TOKEN` | Windows Agent 認証（`X-Agent-Token`） |
| `GITHUB_TOKEN` | InnerMind の GitHub 活動コンテキスト用 |
| `TAVILY_API_KEY` | InnerMind の Web ニュースコンテキスト用 |
| `OBS_WEBSOCKET_PASSWORD` | ZZZ Disc Manager の OBS キャプチャ用（`tools.zzz_disc.capture.obs`） |

> 値は `.env.example` のテンプレを参照。`config.yaml` には機密情報を書かないこと。
