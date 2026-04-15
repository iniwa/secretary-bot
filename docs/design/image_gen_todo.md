# image_gen 残タスク管理

> 調査日: 2026-04-16
> 参照: `docs/design/image_gen_design.md`, `docs/design/image_gen_api.md`

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
- [ ] **SubPC 動作検証** — Main/Sub 両方で `/capability` → enqueue → 生成成功まで通す

## Phase 3（プロンプト / Discord 連携）

- [ ] **`ImageGenUnit.execute()` 実装** — Discord スラッシュコマンド・メンション経由の enqueue
- [ ] **`src/units/prompt_crafter.py` 新規** — LLM 補助のプロンプト会話編集
- [ ] **WebGUI `/api/image/prompts` 一式 + 専用ページ**

## Phase 4（LoRA 学習）

- [ ] **`src/units/lora_train.py` 新規** — LoRA プロジェクト管理・タグ付け・学習オーケストレーション
- [ ] **`windows-agent/tools/image_gen/kohya_manager.py` 新規** — kohya_ss プロセス管理
- [ ] **WebGUI `/api/lora/projects/*` + 専用ページ**

## ドキュメント側の未確定事項（実運用で詰める）

- `image_gen_api.md` §12
  - Preview イベント送出頻度
  - `/system/logs follow=true` バッファ上限
  - kohya sample_prompts 埋め込み方法
  - NAS 並行読み出し本数
  - `/image/generate` タイムアウト既定値
