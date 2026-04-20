# image_gen 残タスク管理

> 調査日: 2026-04-16
> 参照: `docs/image_gen/design.md`, `docs/image_gen/api.md`

実装を終えた項目は `[x]` にし、該当節の末尾に「実装日: YYYY-MM-DD」と実装ファイル/コミットメモを残す。

## Phase 1（Walking Skeleton）仕上げ

- [x] **Agent ウォームアップ** — 起動時に定常ベースモデル（`units.image_gen.default_base_model`）を NAS→ローカルにキャッシュしておき、最初のジョブでコールドスタートしないようにする
  - 実装日: 2026-04-16
  - `Dispatcher._warmup_agents()` / `_warmup_one_agent()` / `_sync_manifest()` を追加
  - 起動 2 秒後に各 Agent の `/capability` を取得 → `model_cache_manifest` を同期 → `default_base_model` が checkpoints に無ければ `/cache/sync` を発射（best-effort）

## Phase 2（複数 PC 分散）

- [x] **`src/units/model_sync.py` 新規ユニット** — capability 定期ポーリング / ウォームアップ指示
  - 実装日: 2026-04-16
  - `ModelSyncUnit` を追加（30分毎 `warmup_all_agents` を呼び出し）
  - `units.model_sync.{enabled, interval_seconds, trigger_sync}` を config に追加
  - `execute` 経由で手動トリガも可能
- [x] **Windows Agent セットアップ系 API**
  - 実装日: 2026-04-16
  - `windows-agent/tools/image_gen/setup_manager.py` を新規作成（task_id で追跡）
  - [x] `POST /comfyui/setup` — git clone → venv 作成 → torch+requirements インストール
  - [x] `POST /comfyui/update` — git pull + requirements 再インストール（稼働中は事前に stop）
  - [x] `POST /kohya/setup` — kohya_ss インストール（Phase 4 準備）
  - [x] `GET /setup/{task_id}` で進捗・ログ tail を取得
  - [x] `GET /setup` で task 一覧
  - config の `image_gen.setup.{comfyui_repo, comfyui_ref, kohya_repo, kohya_ref, cuda_index_url}` で既定値上書き可
- [x] **SubPC 動作検証の手順整備** — 実機作業のためドキュメントのみ整備（2026-04-16）
  - `docs/image_gen/setup/verify.md` 付録 C にセットアップ系 API の手順を追記
  - 実機疎通は §1「E2E 正常系チェックリスト」をユーザー側で実施

## Phase 3（プロンプト / Discord 連携）

- [x] **`ImageGenUnit.execute()` 実装** — Discord スラッシュコマンド・メンション経由の enqueue
  - 実装日: 2026-04-16
  - `src/units/image_gen/unit.py` に LLM プロンプト抽出（`_EXTRACT_PROMPT`）、`_discord_generate` / `_discord_status` / `_discord_cancel` / `_discord_list` を追加
  - `_discord_notifier_loop` が `subscribe_events` 経由でジョブの DONE/FAILED/CANCELLED を監視し、Discord チャンネルへ画像（最大4件）やステータスを投稿
  - 出力先は `units.image_gen.discord_output_channel_id` → コマンド発信チャンネル → 管理者チャンネルの順にフォールバック
- [x] **`src/units/prompt_crafter.py` 新規** — LLM 補助のプロンプト会話編集
  - 実装日: 2026-04-16
  - `PromptCrafterUnit` を追加（`DELEGATE_TO=None`, TTL 7日, 定期 cleanup タスク付き）
  - `database.py` に `prompt_session_get_active/insert/update/list/delete/cleanup_expired` を追加
  - `execute` で LLM による action 抽出 (`craft/show/clear`) + SDXL 向け positive/negative 生成（差分編集対応）
  - 他ユニット参照 API: `get_active_prompt(user_id, platform)` / `craft(...)` / `clear_active(...)`
  - `image_gen._discord_generate` が positive 抽出失敗時にアクティブセッションを自動参照するよう連携
  - config: `units.prompt_crafter.{session_ttl_days, cleanup_interval_seconds}` を追加
- [x] **WebGUI `/api/image/prompts` 一式 + 専用ページ**
  - 実装日: 2026-04-16
  - `src/web/app.py` に `/api/image/prompts` (list/active GET, craft POST, active/session DELETE) を追加
  - `src/web/static/js/pages/prompts.js` を新規作成（指示入力・アクティブセッション表示・履歴一覧・削除）
  - `index.html` / `app.js` のナビゲーションに `Prompts` ページを登録

## Phase 4（LoRA 学習）

> 設計確定日: 2026-04-20
> - **タグ付け**: kohya 同梱 `tag_images_by_wd14_tagger.py` を Agent から直接呼ぶ（ComfyUI ノード化はしない）
> - **TOML テンプレ**: SDXL LoRA の固定テンプレ 1 種（`network_dim=8` / `network_alpha=4` / `lr=1e-4` / 8 epochs / batch=1）。WebGUI から手動編集可
> - **データセット投入**: WebGUI の drag-drop 複数ファイル upload → Pi → NAS → Agent が学習開始時に NAS → ローカル SSD コピー
> - **`sample_prompts`**: Pi が `<NAS>/ai-image/lora_work/<project>/sample_prompts.txt` を生成、TOML の `sample_prompts` でそのパスを参照
> - **トリガーワード**: プロジェクト名 = トリガーワード。各 caption 先頭に必ずトリガーを自動付与（kohya 学習の定石）
> - **昇格**: 学習完了後、ユーザーが WebGUI で承認したチェックポイントのみ `lora_work/<project>/checkpoints/` → `models/loras/<project>/` へ移動

### 実装ブレークダウン

| # | スコープ | 状態 |
|---|---|---|
| **A** | DB schema (`lora_projects` / `lora_dataset_items` / `lora_train_jobs`) + `LoRAMixin` | [x] 既存 (`src/database/_base.py`, `src/database/lora.py`) |
| **B** | `src/units/lora_train` ユニット骨格 + project CRUD API + WebGUI 一覧/作成画面 | [x] 2026-04-20 |
| **B+** | WebGUI dataset drag-drop upload → Pi multipart → NAS 配置 → `lora_dataset_items` 登録 | [x] 2026-04-20 |
| **C** | Agent `POST /lora/dataset/tag` (WD14) + Pi 連携 | [x] 2026-04-20 |
| **D** | WebGUI タグ/キャプション編集 UI（grid + reviewed_at 管理） | [x] 2026-04-20 |
| **E** | TOML テンプレ生成 + `sample_prompts.txt` 書き出し + Agent `POST /lora/dataset/sync` | [x] 2026-04-20 |
| **F** | Agent `kohya_manager.py` + `/lora/train/{start,status,stream,cancel}` SSE | [x] 2026-04-20 |
| **G** | WebGUI 学習監視ページ（進捗・loss グラフ・sample 画像・cancel・ログ tail） | [x] 2026-04-20 |
| **H** | 学習結果の手動昇格 API + WebGUI ボタン | [x] 2026-04-20 |

### 直近の進捗 (2026-04-20)

#### 実装済みファイル

- `src/units/lora_train/__init__.py` — `setup(bot)` で `LoRATrainUnit` をロード
- `src/units/lora_train/unit.py` — `LoRATrainUnit(BaseUnit)`、`DELEGATE_TO=None`
  - project: `create_project` / `list_projects` / `get_project` / `update_project` / `delete_project`
  - dataset: `open_dataset_dir` / `add_dataset_item` / `list_dataset_items` / `get_dataset_item` / `delete_dataset_item` / `is_dataset_path_safe`
  - status は `_ALLOWED_STATUSES = ("draft", "ready", "training", "done", "failed")` で検証
  - NAS 設定は `units.image_gen.nas.{base_path, lora_datasets_subdir, lora_work_subdir}` を共用
- `src/units/lora_train/nas_io.py`
  - 定数: `ALLOWED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")`、`MAX_IMAGE_BYTES = 16 MiB`、`_NAME_RE = ^[a-z0-9][a-z0-9_]{1,31}$`
  - `validate_project_name` / `dataset_dir` / `work_dir` / `ensure_dataset_dir` / `ensure_work_dirs` / `remove_project_dirs`
  - `normalize_image_ext` / `write_dataset_image` / `remove_dataset_file` / `is_inside_dataset_dir`（path traversal 防御）
- `src/web/routes/lora_train.py` — `register(app, ctx)` で REST 登録、Basic Auth は `Depends(ctx.verify)`
  - `GET /api/lora/projects?status=...`
  - `GET /api/lora/projects/{id}`
  - `POST /api/lora/projects` body=`{name, description?, base_model?}`
  - `PATCH /api/lora/projects/{id}` body=`{description?, base_model?, status?}`
  - `DELETE /api/lora/projects/{id}?purge_files=true`
  - `GET /api/lora/projects/{id}/dataset?reviewed_only=...`
  - `POST /api/lora/projects/{id}/dataset` multipart `files[]` — 1 枚ずつ stream → `asyncio.to_thread` で書き込み
  - `DELETE /api/lora/projects/{id}/dataset/{item_id}`
  - `GET /api/lora/projects/{id}/dataset/{item_id}/image` — `FileResponse` + dataset root 配下チェック
- `src/database/lora.py` — `lora_dataset_item_get(item_id)` を追加（既存 `LoRAMixin` の他メソッドはそのまま利用）
- `src/units/__init__.py` — `_UNIT_MODULES["lora_train"] = "src.units.lora_train"`
- `src/web/routes/__init__.py` — `lora_train.register(app, ctx)` を `register_all_routes` 末尾で呼び出し
- `src/web/routes/image_gen.py` — `_IMG_ALLOWED_EXTS` を `nas_io.ALLOWED_IMAGE_EXTS` から再利用するよう変更
- `src/tools/image_gen_console/static/index.html` — サイドバーに `🎯 LoRA` タブ追加
- `src/tools/image_gen_console/static/js/app.js` — `routes` に `lora` ページを追加
- `src/tools/image_gen_console/static/js/pages/lora.js` — プロジェクト一覧/編集 + drag-drop dataset カード（`lora-ds-*` クラスを `image_gen.css` に追加済み）
- `src/tools/image_gen_console/static/css/image_gen.css` — `.lora-ds-drop` / `.lora-ds-grid` / `.lora-ds-cell` / `.lora-ds-meta` / `.lora-ds-del`

実機での疎通確認は未実施（Remote PC 環境のため）。Pi で再起動 → schema migration が走り、WebGUI から `🎯 LoRA` タブを開いて以下を確認:
1. 新規プロジェクト作成 → NAS 上に `lora_datasets/<name>/` と `lora_work/<name>/{checkpoints,samples,logs}/` が出来る
2. drag-drop で画像投入 → サムネイル表示、削除ボタンで本体＋同名 `.txt` が消える
3. プロジェクト削除（`purge_files=true`）→ NAS dir が消える

#### 未着手スコープの再開時メモ

- **C: WD14 タグ付け**
  - Agent 側に `windows-agent/tools/image_gen/wd14_tagger.py` を新設し、kohya `sd-scripts/finetune/tag_images_by_wd14_tagger.py` を `subprocess` で呼ぶ。`setup_manager.py` の `SetupTask` パターンを流用して `task_id + 進捗 + ログ tail` を返す
  - Agent endpoint: `POST /lora/dataset/tag {nas_path, threshold, repo_id?}` → 202 + `task_id`、`GET /lora/tag/{task_id}` で進捗
  - Pi 側 `LoRATrainUnit.start_tagging(project_id, agent_id?)` で AgentPool から kohya 有効 Agent を選定、POST → ポーリング、完了後に NAS 上の `<image>.txt` を読み `lora_dataset_items.tags` を `UPDATE`
  - Pi route: `POST /api/lora/projects/{id}/dataset/tag`
  - WebGUI: dataset カードに「🏷 WD14 タグ付け」ボタン + 進捗バー
- **D: タグ/キャプション編集 UI**
  - 各画像クリックで lightbox 風の編集モーダル（タグ multi-select chip + キャプション textarea）
  - 「review 済み」トグル → `lora_dataset_item_update(mark_reviewed=True)`
  - dataset list で `reviewed_only` トグル
- **E: TOML テンプレ + sample_prompts**
  - Pi に `src/units/lora_train/toml_builder.py` を新設、SDXL 固定テンプレ（`network_dim=8 / alpha=4 / lr=1e-4 / 8 epochs / batch=1`）を生成し `<NAS>/lora_work/<project>/dataset.toml` と `sample_prompts.txt` を書き出す
  - Agent 側に `POST /lora/dataset/sync` を追加（NAS → ローカル SSD `<root>/lora_data/<project>/` へコピー、cache_manager の `copy_with_progress` を流用）
  - Pi route: `POST /api/lora/projects/{id}/prepare` で TOML 生成 → Agent sync を起動
- **F: kohya 学習**
  - Agent 側に `windows-agent/tools/image_gen/kohya_manager.py` を新設、`sdxl_train_network.py` を `subprocess` で起動。`SetupTask` パターン + SSE で stdout/stderr の tail を配信
  - Pi 側 `LoRATrainUnit.start_training(project_id)` で `lora_train_jobs` 行を作って Agent POST、SSE 受信を別 task でぶら下げて DB 進捗を更新
  - Pi route: `POST /api/lora/projects/{id}/train/start` / `GET /train/{job_id}/status` / `GET /train/{job_id}/stream` / `POST /train/{job_id}/cancel`
- **G: 学習監視 UI**
  - lora.js に「📈 学習」セクションを追加。SSE 接続 → 進捗バー / loss 折れ線 / sample 画像サムネ / ログ tail
- **H: 手動昇格**
  - Pi route: `POST /api/lora/projects/{id}/promote` body=`{checkpoint_filename}` で `<NAS>/lora_work/<project>/checkpoints/<file>` → `<NAS>/models/loras/<project>/<file>` へ移動
  - WebGUI: 学習完了後、checkpoint 一覧から「✅ 昇格」ボタン

#### 設計上の注意点

- Agent 側の `kohya_manager` / `wd14_tagger` は `setup_manager.py` の `SetupTask`（`asyncio.create_subprocess_exec` + ログ tail）を流用する想定。subprocess の cwd は `<root>/kohya_ss`、Python は `<root>/venv-kohya/Scripts/python.exe`
- WD14 と学習はどちらも長時間（数分～数十分）かかるため、Pi → Agent は 202 + SSE/poll の非同期返却。SSE は `cache/sync` のパターン (router.py:592-631) をそのまま流用可能
- `lora_train_jobs.tb_logdir` を学習開始時に `<NAS>/lora_work/<project>/logs/<timestamp>/` に設定すると、後で TensorBoard を別 PC から指せる

## ドキュメント側の未確定事項（実運用で詰める）

- `api.md` §12（残り）
  - Preview イベント送出頻度
  - `/system/logs follow=true` バッファ上限
  - NAS 並行読み出し本数
  - `/image/generate` タイムアウト既定値
