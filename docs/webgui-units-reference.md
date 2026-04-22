# WebGUI Units リファレンス

> 最終更新: 2026-04-23
> 実装: `src/units/`（Discord / WebGUI 両面の機能 = ユニット）
>       + `src/web/routes/*.py`（対応する WebGUI API）

## ユニット一覧

ユニットの有効/無効・個別パラメータは `config.yaml` の `units.<name>` で制御。
ロード対象は `src/units/__init__.py::_UNIT_MODULES`。

| Unit | 目的 | 委任先 | データ保存先 | WebGUI 操作 |
|------|------|--------|-------------|-------------|
| reminder | リマインダー / Todo 管理 | - | SQLite（reminders/todos） | CRUD・スヌーズ・完了・通知済みルーティング |
| memo | メモ保存・検索 | - | SQLite（memos） | CRUD・検索・追記 |
| timer | カウントダウンタイマー | - | メモリ（非永続） | 残り時間のリアルタイム表示 |
| weather | 天気予報・定期通知 | - | SQLite + Open-Meteo API | 購読管理・有効/無効 |
| rss | RSS フィード集約 | - | SQLite（rss_feeds/articles/prefs/feedback） | フィード管理・記事・手動 fetch |
| chat | 自由会話 | - | ChromaDB（ai_memory/people_memory） | チャット UI |
| status | システムステータス | - | `StatusCollector` | ダッシュボード応答 |
| power | PC 電源管理（WoL / shutdown / restart） | - | WoL API + Windows Agent | 管理者限定操作 |
| calendar | Google カレンダー連携 | - | Google API + SQLite（calendar_*） | イベント作成・読み取り同期 |
| web_search | SearXNG 検索 | - | SearXNG API | 検索 UI |
| rakuten_search | 楽天商品検索 | - | Rakuten API / スクレイピング | 検索 UI・設定 |
| docker_log_monitor | コンテナログ監視 | - | SQLite（docker_error_log / docker_log_exclusions） | エラー一覧・除外管理 |
| image_gen | 画像生成（ComfyUI） | windows | SQLite（generation_jobs / image_collections / prompt_sections）+ NAS | 生成・ギャラリー・ワークフロー・プリセット・Wildcard |
| prompt_crafter | LLM と対話で SDXL プロンプトを育成 | - | SQLite（prompt_sessions / prompt_templates） | セッション管理・active プロンプト取得 |
| model_sync | Agent のモデルキャッシュ同期 | - | SQLite（model_cache_manifest）| 手動 sync トリガ |
| lora_train | kohya_ss LoRA 学習 | windows | SQLite（lora_projects / dataset_items / train_jobs / config_templates）+ NAS | プロジェクト・データセット・ジョブ |
| clip_pipeline | 配信アーカイブの自動切り抜き | windows | SQLite（clip_pipeline_jobs/events）+ NAS | ジョブ投入・EDL 取得・capability 確認 |

## 補助システム

| System | 目的 | 説明 |
|--------|------|------|
| `remote_proxy` | 委任ラッパー | `DELEGATE_TO="windows"` のユニットを透過的に Agent へ転送 |
| `agent_pool` | Agent 管理 | priority 順 + アクティビティ判定で空き Agent を選択 |
| `heartbeat` | 定期バックグラウンド処理 | InnerMind / RSS / STT / Docker 監視 / スヌーズ再通知 / カレンダー読み取り同期 |
| `inner_mind` | 自律思考システム | 思考レンズ + 12 種コンテキストソース + Actuator（承認待ち振り分け） |
| `activity_detector` / `collector` | アクティビティ統合 | ゲーム / OBS / VC / FG セッションを収集し、処理抑制判定に使う |
| `habit_detector` | 習慣ゲーム検出 | プレイ頻度プロフィール・離脱判定を InnerMind に渡す |
| `daily_diary` | 1 日の活動ダイジェスト | Activity データを集約して日記を LLM で生成 |

---

## 各ユニット詳細

### 1. Reminder Unit
- チャット意図: `add / list / edit / delete / done / contextual_done / contextual_snooze /
  todo_add / todo_list / todo_edit / todo_done / todo_delete`
- テーブル: `reminders`（message/remind_at/repeat_type/active/notified/snooze_count/
  snoozed_until 等）、`todos`（title/done/due_date/done_at）
- WebGUI: 一覧 / 編集 / 完了 / 削除、スヌーズ UI（通知済み未完了を優先的に回す）

### 2. Memo Unit
- チャット意図: `save / list / search / edit / append / delete`
- テーブル: `memos`（content/tags/user_id）
- WebGUI: 一覧 / 検索（keyword）/ インライン編集 / 追記（`/append`）

### 3. Timer Unit
- チャット意図: `set_timer`（分数＋メッセージ）
- 保存: メモリ内 `_active_timers` / `_timer_info` のみ（永続化しない）
- WebGUI: アクティブタイマー一覧＋残り秒数のリアルタイム表示

### 4. Weather Unit
- チャット意図: `get_weather / weekly / subscribe / unsubscribe / list`
- テーブル: `weather_subscriptions`（location/lat/lon/notify_hour/notify_minute/active）
- WebGUI: 購読一覧 / トグル / 通知時刻変更 / 削除

### 5. RSS Unit
- チャット意図: `digest / list / add / remove / enable_category / disable_category`
- テーブル: `rss_feeds` / `rss_articles` / `rss_user_prefs` / `rss_feedback`
- WebGUI: フィード CRUD / 記事一覧 / 手動 fetch / カテゴリフィルタ / 記事フィードバック

### 6. Chat Unit
- 設定: `chat.history_minutes`（0 で時間制限なし）
- メモリ: `AIMemory`（Ollama 必須）+ `PeopleMemory`（Geminiフォールバック可）
- WebGUI: チャット UI（`/api/chat` + `/api/flow/stream` SSE）

### 7. Image Gen Unit（`src/units/image_gen/`）
- 委任: `DELEGATE_TO="windows"`（実行は Windows Agent + ComfyUI）
- 主要コンポーネント:
  - `unit.py`（ジョブ受付 / 状態参照 / pub/sub）
  - `dispatcher.py`（Agent 振り分け・キュー制御）
  - `agent_client.py`（Windows Agent HTTP クライアント）
  - `workflow_mgr.py`（ComfyUI ワークフロー JSON 管理）
  - `wildcard_expander.py`（`{a|b|c}` / `{1-5}` / `__name__` 辞書展開）
  - `section_composer.py` / `section_mgr.py`（セクションベースのプロンプト構築）
  - `warmup.py` / `modality.py` / `models.py`
- テーブル: `generation_jobs` / `generation_job_events` / `prompt_section_categories` /
  `prompt_sections` / `prompt_section_presets` / `wildcard_files` / `image_collections` /
  `image_collection_items` / `workflows`
- WebGUI: `/api/generation/*` + 旧 `/api/image/*`、同居 SPA `/tools/image-gen/`

### 8. PromptCrafter Unit
- 役割: LLM と対話で SDXL positive/negative プロンプトを洗練
- テーブル: `prompt_sessions`（TTL あり、`prompt_crafter.session_ttl_days`）/ `prompt_templates`
- WebGUI: `/api/image/prompts[/active]`, `POST /api/image/prompts/craft`, DELETE 系

### 9. ModelSync Unit
- 役割: `image_gen` Agent の `/capability` を定期ポーリング、未キャッシュなら `/cache/sync` 発火
- テーブル: `model_cache_manifest`
- 設定: `units.model_sync.interval_seconds` / `trigger_sync`

### 10. LoRA Train Unit（`src/units/lora_train/`）
- 委任: `DELEGATE_TO="windows"`（kohya_ss）
- 主要コンポーネント: `unit.py` / `agent_client.py` / `toml_builder.py`（sd-scripts 向け TOML 生成）/ `nas_io.py`
- テーブル: `lora_projects` / `lora_dataset_items` / `lora_train_jobs` / `lora_config_templates`
- WebGUI: `/api/lora/*`（プロジェクト / データセット / ジョブ / テンプレート）

### 11. Clip Pipeline Unit（`src/units/clip_pipeline/`）
- 委任: `DELEGATE_TO="windows"`（Whisper + Ollama + ffmpeg）
- 主要コンポーネント: `unit.py` / `dispatcher.py` / `agent_client.py` / `models.py`
- テーブル: `clip_pipeline_jobs` / `clip_pipeline_job_events`
- WebGUI: `/api/clip-pipeline/*`（ジョブ投入 / EDL / capability / inputs）

### 12. Docker Log Monitor
- Heartbeat から毎分 docker ログを巡回し、error/warning を DB に保存
- 設定: `docker_monitor.*`（check_interval / cooldown / containers / max_lines_per_check）
- WebGUI: `/api/docker-monitor/*`（一覧 / dismiss / 除外パターン管理）

### 13. Power Unit
- Windows Agent 経由で shutdown / restart、WoL ツール経由で起動
- WoL URL は `wol.url` で設定（Docker bridge から到達可能な LAN IP を指定）
- WebGUI: `/api/agents/*`（restart 系）は `routes/system.py`、shutdown は Discord 側経由

### 14. Calendar Unit
- 書き込み: `GoogleCalendarService` で直接カレンダーに追加
- 読み取り同期（`calendar.read_sync.enabled`）: `calendar_read_sources` から events.list し
  `calendar_events` にキャッシュ、InnerMind の `CalendarSource` が文脈注入

### 15. InnerMind
- 思考レンズ（`src/inner_mind/core.py::THINKING_LENSES`, 6 種）:
  1. concrete — 具体的観察（感想）
  2. empathy — ユーザーへの想像
  3. time_space — 時間・季節・天気からの連想
  4. curiosity — 一つだけ深掘り
  5. reflection — 最近の出来事を振り返り
  6. rest — 休息モード（「特になし」で可）
  - ローテーションは `_select_thinking_lens` が DB 復元した履歴を参照。
- コンテキストソース（12 種、`src/inner_mind/context_sources/`）:
  `conversation / memo / reminder / memory / weather / rss / stt / activity /
   habit / calendar / github / tavily_news`
  - 注意フィルタ `salience.top_n` / `salience.threshold` で絞る。
- 設定: `enabled / thinking_interval_ticks / min_speak_interval_minutes /
  speak_channel_id / target_user_id / active_threshold_minutes`
- Actuator（`src/inner_mind/actuator.py`）: decision の Tier 判定 → `pending_actions` へ蓄積、
  Discord 承認 UI（`approval_view.py`）または WebGUI `/api/pending/*` で処理
- WebGUI: `/api/monologue`, `/api/inner-mind/status|context|settings|dispatches|autonomy`,
  `/api/pending/*`

---

## 注意事項

- **Todo は `reminder` ユニットに統合**（独立した `todo.py` は無い）
- **モノローグ保存は InnerMind**（`mimi_monologue` テーブル）
- **OBS 制御は Windows Agent 経由**（`windows-agent/activity/obs_manager.py`, WebGUI `/api/obs/*`）
- **STT は heartbeat + Windows Agent**（Main: マイクキャプチャ・Sub: Whisper 推論、
  WebGUI `/api/stt/*`）
- **input-relay は git submodule**（`windows-agent/tools/input-relay/`）。
  メイン PC の sender と Sub PC の receiver が HTTP で通信する。
  Gitea + GitHub のデュアルプッシュで管理（詳細はメモリ参照）。
- **ZZZ Disc Manager は同居ツール**（`src/tools/zzz_disc/`、`/tools/zzz-disc/*` で配信）。
  ユニットではなく `config.yaml` の `tools.zzz_disc` で管理。
