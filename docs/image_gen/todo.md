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
| **C** | Agent `POST /lora/dataset/tag` (WD14) + Pi 連携 | [ ] |
| **D** | WebGUI タグ/キャプション編集 UI（grid + reviewed_at 管理） | [ ] |
| **E** | TOML テンプレ生成 + `sample_prompts.txt` 書き出し + Agent `POST /lora/dataset/sync` | [ ] |
| **F** | Agent `kohya_manager.py` + `/lora/train/{start,status,stream,cancel}` SSE | [ ] |
| **G** | WebGUI 学習監視ページ（進捗・loss グラフ・sample 画像・cancel・ログ tail） | [ ] |
| **H** | 学習結果の手動昇格 API + WebGUI ボタン | [ ] |

## ドキュメント側の未確定事項（実運用で詰める）

- `api.md` §12（残り）
  - Preview イベント送出頻度
  - `/system/logs follow=true` バッファ上限
  - NAS 並行読み出し本数
  - `/image/generate` タイムアウト既定値
