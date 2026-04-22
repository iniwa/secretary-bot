# auto-kirinuki Phase 1 実装計画・進捗

設計は `design.md`、NAS 移行は `nas_migration.md` を参照。
進捗はこのファイルのチェックボックスを随時更新する。

## 進捗サマリ

| フェーズ | 状態 | 備考 |
|---|---|---|
| A. NAS再編（設定/ドキュメント） | 完了 | 実機適用は運用時 |
| B. DB / 共通基盤 | 完了 | migration v32 + ClipPipelineMixin + errors |
| C. Pi 側ユニット | 完了 | models / agent_client / dispatcher / unit / __init__ + UnitManager 登録 |
| D. Windows Agent 側 | 完了 | router / runner / whisper_cache + 旧コード移植。実機疎通は Main/Sub PC で |
| E. WebGUI | 完了 | `/api/clip-pipeline/*` + `#clip-pipeline` SPA ページ（capability / job CRUD / SSE） |
| F. 設定 / ドキュメント | 完了 | design / implementation_plan / nas_migration / api / README + issues.md |
| G. クリーンアップ | 静的作業完了 / 実機疎通のみ残 | G1/G2/G4 完了。D8・G3 は Main/Sub PC 起動時の実機確認 |

最終更新: 2026-04-20 / 担当: Claude Code + iniwa

## 引き継ぎメモ（2026-04-20 時点）

**直前のセッションで完了**:
- Phase A (NAS 再編): すべての設定・ドキュメントを `secretary-bot` 親共有前提に更新
- Phase B (DB / 共通基盤): `_base.py` に migration v32 追加（`clip_pipeline_jobs` + `clip_pipeline_job_events` + 3 インデックス）。`src/database/clip_pipeline.py` に `ClipPipelineMixin` を新設し `__init__.py` に登録。`src/errors.py` に `ClipPipelineError` / `WhisperError` / `TranscribeError` / `HighlightError` を追加。

**次に着手するタスク**:
- Phase C1 (`src/units/clip_pipeline/models.py`) から開始。`image_gen` ユニットの構造を参考にし、`JobStatus` / `TransitionEvent` dataclass、status/step/platform 定数を定義する。
- その後 C2 (agent_client.py) → C3 (dispatcher.py) → C4 (unit.py) の順で進める。`src/units/image_gen/` の実装をテンプレとして最大限コピーし、clip_pipeline 固有の差分（step カラム、warming_cache 遷移、ジョブ完了時の Discord 投稿内容）のみ書き換える。

**未検証項目**:
- migration v32 は実機で未起動。Pi 再起動時に `PRAGMA user_version = 32` まで進むこと、`clip_pipeline_jobs` テーブルが生成されることの確認はリモート PC からは不可能。Main/Sub PC で動作確認するか、テストコードで検証する必要がある。

---

## A. NAS再編（設計反映）

- [x] A1: `config.yaml.example` の `units.image_gen.nas.base_path` を `/mnt/ai-image` → `/mnt/secretary-bot/ai-image` へ変更
- [x] A2: `config.yaml.example` に `units.clip_pipeline` セクション新設（`nas.base_path: "/mnt/secretary-bot/auto-kirinuki"` ほか defaults / retry / timeouts / dispatcher）
- [x] A3: `windows-agent/config/agent_config.yaml.example` の `image_gen.nas.share` を `"ai-image"` → `"secretary-bot"`、`subpath` を `""` → `"ai-image"` へ変更
- [x] A4: `agent_config.yaml.example` に `clip_pipeline` セクション新設（`share: "secretary-bot"`, `subpath: "auto-kirinuki"`, `mount_drive: "N:"`）
- [x] A5: `docs/image_gen/nas_setup.md` を `secretary-bot` 共有前提に書き換え
- [x] A6: `docs/image_gen/design.md` / `docs/image_gen/README.md` / `docs/image_gen/setup/*.md` のパス参照更新（`/mnt/ai-image` → `/mnt/secretary-bot/ai-image`, `Z:\` → `N:\ai-image` など）
- [x] A7: `.env.example` の `NAS_SHARE` デフォルトを `secretary-bot` へ（Pi 側 + Windows Agent 側の両方）

## B. DB / 共通基盤

- [x] B1: `src/database/_base.py` に migration v32 追加（`clip_pipeline_jobs` + `clip_pipeline_job_events` + 3 インデックス）。`_SCHEMA_VERSION` を 32 に更新
- [x] B2: `src/database/clip_pipeline.py` 新設 + `__init__.py` に `ClipPipelineMixin` 登録。CRUD メソッド実装:
  - [x] `clip_pipeline_job_insert`
  - [x] `clip_pipeline_job_get`
  - [x] `clip_pipeline_job_list`
  - [x] `clip_pipeline_job_update_status`
  - [x] `clip_pipeline_job_update_progress`
  - [x] `clip_pipeline_job_set_result`
  - [x] `clip_pipeline_job_cancel`
  - [x] `clip_pipeline_job_claim_queued`（FIFO + next_attempt_at 考慮の楽観ロック pick）
  - [x] `clip_pipeline_job_find_timed_out`（stuck_reaper 用に追加）
  - [x] `clip_pipeline_job_events_list`
- [x] B3: `src/errors.py` に例外クラス追加
  - [x] `ClipPipelineError`（基底）
  - [x] `WhisperError`
  - [x] `TranscribeError`
  - [x] `HighlightError`
  - [x] `CacheSyncError` — image_gen のものを共通利用（追加不要と判断）

## C. Pi 側ユニット（`src/units/clip_pipeline/`）

- [x] C1: `models.py`
- [x] C2: `agent_client.py`
- [x] C3: `dispatcher.py`
- [x] C4: `unit.py`
- [x] C5: `__init__.py`（ユニット登録）
- [x] C6: `src/units/__init__.py` に `clip_pipeline` を追加（UnitManager 自動ロード対応）

## D. Windows Agent 側（`windows-agent/tools/clip_pipeline/`）

- [x] D1: ディレクトリ新設、旧 `streamarchive-auto-kirinuki/clip-pipeline/*` をコピー
  - pipeline / preprocess_audio / transcribe / analyze_audio / emotion / highlight / export_edl / export_clips / config（`pipeline/` サブパッケージに集約）
- [x] D2: 移植コードの修正
  - [x] 明示相対 import 化（`from .config import ...` 等）
  - [x] `transcribe` に `download_root` 引数、`run_pipeline` に `step_callback` / `cancel_flag` / `whisper_download_root` / 戻り値 dict を追加
  - [x] 旧 `worker.py` / `coordinator.py` / `main.py` / `monitor.py` は移植しない（Pi 側 Dispatcher に吸収）
- [x] D3: `runner.py` 新規（`ClipJob` + `run_clip_job` で SSE イベントキュー駆動）
- [x] D4: `whisper_cache.py` 新規（NAS → ローカル SSD を 4MB チャンクでコピー、途中キャンセル対応、完了時 atomic replace）
- [x] D5: `router.py` 新規
  - [x] `/clip-pipeline/capability`
  - [x] `/clip-pipeline/whisper/cache-sync` + `/events` + `/cancel`
  - [x] `/clip-pipeline/jobs/start`
  - [x] `/clip-pipeline/jobs/{id}` / `/events` / `/cancel`
  - [x] X-Agent-Token 認証
- [x] D6: `__init__.py` で `init_clip_pipeline(role, agent_config, agent_dir)` を公開
- [x] D7: `windows-agent/agent.py` に統合（import + lifespan init + include_router）
- [ ] D8: 実機で `nas_mount.py` が `secretary-bot` 共有を再利用することの確認（Main/Sub PC 再開時）
- [x] D9: `requirements.txt`（Windows Agent） — faster-whisper / librosa / demucs / funasr / requests を追加

## E. WebGUI

- [x] E1: `src/web/routes/clip_pipeline.py`
  - [x] `POST /api/clip-pipeline/jobs` — ジョブ登録
  - [x] `GET /api/clip-pipeline/jobs` — 一覧（status/limit/offset）
  - [x] `GET /api/clip-pipeline/jobs/{id}` — 詳細
  - [x] `POST /api/clip-pipeline/jobs/{id}/cancel` — 取消
  - [x] `GET /api/clip-pipeline/jobs/stream` — 共通 SSE（`unit.subscribe_events` 経由）
  - [x] `GET /api/clip-pipeline/capability` — 全 Agent の `/capability` を並列集約
- [x] E2: `src/web/routes/__init__.py` の `register_all_routes` に `clip_pipeline.register` 追加
- [x] E3: `src/web/static/js/pages/clip-pipeline.js` に SPA ページを実装（HTML は render() でインライン）
  - [x] 動画パス入力欄（単一、Agent 絶対パス）
  - [x] モード / whisper / ollama / output_dir 指定
  - [x] パラメータ入力（top_n / min_clip_sec / max_clip_sec / mic_track / use_demucs / do_export_clips）
  - [x] capability パネル（GPU / VRAM / Whisper モデル一覧 / busy 表示）
  - [x] ジョブ一覧テーブル（作成時刻 / 動画 / status badge / progress bar / step / agent / whisper / 取消）
  - [x] SSE 受信で該当ジョブ行をライブ更新、15s ポーリング fallback
- [x] E4: fetch ラッパ `api()` + `EventSource` 接続（上記 js 内）
- [x] E5: CSS は render() 内 `<style>` にインライン（既存コンポーネント変数を活用、独立ファイルは作らない）
- [x] E6: `src/web/static/index.html` の Tools グループに `#clip-pipeline` ナビ追加 / `app.js` の pages レジストリ登録

## F. 設定 / ドキュメント

- [x] F1: `docs/auto_kirinuki/design.md`
- [x] F2: `docs/auto_kirinuki/implementation_plan.md`（本書）
- [x] F3: `docs/auto_kirinuki/nas_migration.md`
- [x] F4: `docs/auto_kirinuki/api.md`（Agent API 仕様書）
- [x] F5: `docs/auto_kirinuki/README.md`（目次）
- [x] F6: `docs/issues.md` に auto-kirinuki セクション更新（Phase C/D/F 完了記録）

## G. クリーンアップ / 検証

- [x] G1: ローカル型チェック / import 整合（2026-04-23: Pi 側 + Agent 側 + agent.py + web/app.py 全 import が解決。Database のクリップ関連メソッド 12 個も揃っている）
- [x] G2: ユニットテスト（Pi 側ユニット / Dispatcher ロジック）（2026-04-23: `tests/units/clip_pipeline/` に 35 テスト追加 — 純粋関数 9 / Dispatcher 状態遷移 13 / Unit ロジック 13）
- [ ] G3: 実機疎通（Main/Sub PC 上で Agent 起動 + Pi から enqueue）
  - 注: リモート PC では実施不可。Sub PC / Main PC で再開時にチェック
- [x] G4: 旧リポジトリ `streamarchive-auto-kirinuki` の参考用コメント追加（2026-04-23: `CLAUDE.md` / `CLAUDE_ja.md` / `clip-pipeline-design.md` / `memo.md` の先頭に移行バナーを追加。未コミット — 旧リポは削除しない）

---

## 実装順序（推奨）

```
F1→F2→F3 (ドキュメント基盤)
  ↓
A1〜A7 (設定系 only, 実機適用は別途)
  ↓
B1〜B3 (DB + errors)
  ↓
C1→C2→C3→C4→C5 (Pi ユニット)
  ↓
D1→D2→D3→D4→D5→D6→D7→D8→D9 (Windows Agent)
  ↓
E1→E2→E3→E4→E5→E6 (WebGUI)
  ↓
F4→F5→F6 (残ドキュメント)
  ↓
G1→G2→G4 (静的検証、G3 は実機環境で別途)
```

## ログ欄（進捗記録）

- 2026-04-20 初回作成: 設計・実装計画・NAS 移行手順の 3 ドキュメントを作成。
- 2026-04-20 Phase A 完了: `config.yaml.example` / `agent_config.yaml.example` / `.env.example` / `windows-agent/config/.env.example` 更新。`docs/image_gen/nas_setup.md` を `secretary-bot` 親共有前提に全面書き換え。`design.md` / `api.md` / `README.md` / `comfyui_usage.md` / `preset_compat.md` / `setup/*.md` のパス参照を `/mnt/secretary-bot/ai-image` / `N:\ai-image` / `//nas/secretary-bot/ai-image` / `secretary-bot-rw` へ更新。
- 2026-04-20 Phase B 完了: `src/database/_base.py` に migration v32 追加（`clip_pipeline_jobs` + `clip_pipeline_job_events` + 3 インデックス）、`_SCHEMA_VERSION` を 32 に。`src/database/clip_pipeline.py` を新設し `ClipPipelineMixin` に CRUD 10 メソッドを実装、`__init__.py` に登録。`src/errors.py` に `ClipPipelineError` / `WhisperError` / `TranscribeError` / `HighlightError` を追加。`CacheSyncError` は image_gen のものを再利用。
- 2026-04-20 Phase C 完了: `src/units/clip_pipeline/` に `models.py` / `agent_client.py` / `dispatcher.py` / `unit.py` / `__init__.py` を新設し、`src/units/__init__.py` の `_UNIT_MODULES` に `clip_pipeline` を登録。image_gen テンプレを基にエラー階層を `BotError + is_retryable` 判定に揃え、NAS 出力パスを `outputs/<stem>` 直下に正規化、`warming_cache` 遷移で `AgentClient.capability()` を呼んで Whisper モデル欠落を判定するフローに統一。
- 2026-04-20 Phase D 完了: `windows-agent/tools/clip_pipeline/` を新設。旧 `streamarchive-auto-kirinuki/clip-pipeline/` の pipeline / preprocess_audio / transcribe / analyze_audio / emotion / highlight / export_edl / export_clips / config を `pipeline/` サブパッケージへ移植（明示相対 import 化、`transcribe` に `download_root`、`run_pipeline` に `step_callback` / `cancel_flag` / 戻り値追加）。`runner.py` で asyncio.to_thread 駆動 + SSE イベントキュー、`whisper_cache.py` で NAS → ローカル SSD への chunked copy（atomic replace）、`router.py` で `/clip-pipeline/capability` / `/whisper/cache-sync` / `/jobs/start` の HTTP + SSE を実装。`windows-agent/agent.py` に init + include_router を追加、`windows-agent/requirements.txt` に faster-whisper / librosa / demucs / funasr / requests を追加。実機疎通は Main/Sub PC で再開時に実施。
- 2026-04-20 Phase F 完了: `docs/auto_kirinuki/api.md`（Agent API 仕様書）と `docs/auto_kirinuki/README.md`（目次 + 全体構成）を新設。`implementation_plan.md` / `docs/issues.md` を Phase C/D/F 完了状態に更新。
- 2026-04-20 Phase E 完了: `src/web/routes/clip_pipeline.py` に 6 エンドポイント（jobs POST/GET/detail/cancel、jobs/stream SSE、capability）を実装し、`src/web/routes/__init__.py` に登録。`AgentClient` は `discord.py` 依存経路を避けるため capability ハンドラ内で遅延 import。WebGUI は `src/web/static/js/pages/clip-pipeline.js` に SPA ページを新設（SSE ライブ更新 + 15s ポーリング fallback + 取消ボタン + capability パネル）、`index.html` の Tools グループにナビ追加、`app.js` の pages レジストリに登録。残タスクは Phase G（ローカル型チェック / 実機疎通）。
- 2026-04-23 Phase G 静的作業完了: G1（全 import 解決確認 — Pi 側 `src/units/clip_pipeline/` + Agent 側 `tools/clip_pipeline/` + `windows-agent/agent.py` + `src/web/app.py`。`Database` に `clip_pipeline_job_*` 系 12 メソッドが揃っていることも確認）、G2（`tests/units/clip_pipeline/` に 35 テスト追加 — 純粋関数 9 / Dispatcher 状態遷移 13 / Unit ロジック 13、全体 pytest 75 passed）、G4（旧リポジトリ `streamarchive-auto-kirinuki` の `CLAUDE.md` / `CLAUDE_ja.md` / `clip-pipeline-design.md` / `memo.md` の先頭に移行バナーを追加）。残るは G3（実機疎通 — Main/Sub PC 上で Agent 起動 + Pi から enqueue）と D8（`nas_mount.py` が `secretary-bot` 共有を再利用する確認）のみ。どちらも実機動作が必要なので、Main/Sub PC セッション再開時に検証する。
