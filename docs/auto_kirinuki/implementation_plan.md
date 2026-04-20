# auto-kirinuki Phase 1 実装計画・進捗

設計は `design.md`、NAS 移行は `nas_migration.md` を参照。
進捗はこのファイルのチェックボックスを随時更新する。

## 進捗サマリ

| フェーズ | 状態 | 備考 |
|---|---|---|
| A. NAS再編（設定/ドキュメント） | 完了 | 実機適用は運用時 |
| B. DB / 共通基盤 | 完了 | migration v32 + ClipPipelineMixin + errors |
| C. Pi 側ユニット | 未着手 | **次に着手する Phase** |
| D. Windows Agent 側 | 未着手 | 実機疎通は Main/Sub PC で |
| E. WebGUI | 未着手 | |
| F. 設定 / ドキュメント | 部分着手 | design.md / implementation_plan.md / nas_migration.md 作成済み。api.md / README.md 未 |
| G. クリーンアップ | 未着手 | |

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

- [ ] C1: `models.py`
  - [ ] `JobStatus` dataclass（to_dict 付き）
  - [ ] `TransitionEvent` dataclass
  - [ ] ステータス定数（STATUS_QUEUED, DISPATCHING, WARMING_CACHE, RUNNING, DONE, FAILED, CANCELLED）
  - [ ] ステップ定数（STEP_PREPROCESS, TRANSCRIBE, ANALYZE, EMOTION, HIGHLIGHT, EDL, CLIPS）
  - [ ] プラットフォーム定数（PLATFORM_DISCORD, PLATFORM_WEBGUI）
- [ ] C2: `agent_client.py`
  - [ ] `AgentClient` クラス（httpx wrapper）
  - [ ] `capability()` / `whisper_cache_sync()` / `whisper_cache_sync_stream()`
  - [ ] `job_start()` / `job_get()` / `job_stream()` / `job_cancel()`
  - [ ] エラーマッピング（Agent レスポンス → 例外）
- [ ] C3: `dispatcher.py`
  - [ ] `Dispatcher` クラス（image_gen パターン）
  - [ ] `_job_dispatcher_worker`（queued→dispatching→running 駆動）
  - [ ] `_warming_cache_monitor`（SSE 購読）
  - [ ] `_running_monitor`（SSE 購読、step/progress/log 転送）
  - [ ] `_stuck_reaper_worker`
  - [ ] キャンセル / リトライ / バックオフ
- [ ] C4: `unit.py`
  - [ ] `ClipPipelineUnit(BaseUnit)` 基本構造
  - [ ] `execute()`（Discord: 切り抜き / status / cancel / list）
  - [ ] `enqueue()` / `get_job()` / `list_jobs()` / `cancel_job()`
  - [ ] イベント pub/sub（WebGUI SSE 用）
  - [ ] Discord notifier loop（完了時に結果投稿）
  - [ ] LLM 意図抽出プロンプト（`_extract_params`）
- [ ] C5: `__init__.py`（ユニット登録）
- [ ] C6: `src/unit_manager.py`（または自動ロード機構）での有効化確認

## D. Windows Agent 側（`windows-agent/tools/clip_pipeline/`）

- [ ] D1: ディレクトリ新設、旧 `streamarchive-auto-kirinuki/clip-pipeline/*` をコピー
  - [ ] `pipeline.py`
  - [ ] `preprocess_audio.py`
  - [ ] `transcribe.py`
  - [ ] `analyze_audio.py`
  - [ ] `emotion.py`
  - [ ] `highlight.py`
  - [ ] `export_edl.py`
  - [ ] `export_clips.py`
  - [ ] `config.py` は吸収して不要に
- [ ] D2: 移植コードの修正
  - [ ] import パス（`sys.path.insert` を削除、明示相対 import へ）
  - [ ] `config.py` 依存を agent_config.yaml 経由に差し替え
  - [ ] 旧 `worker.py` / `coordinator.py` / `main.py` / `monitor.py` は移植しない（Pi 側 Dispatcher に吸収）
- [ ] D3: `runner.py` 新規
  - [ ] ジョブ辞書（`dict[job_id, JobContext]`）
  - [ ] `start_job(params) -> job_id`
  - [ ] `cancel_job(job_id)`
  - [ ] `get_job(job_id)` snapshot
  - [ ] SSE 用イベントキュー（step/progress/log/result/error）
  - [ ] ログコールバック / progress コールバックのアダプタ
- [ ] D4: `whisper_cache.py` 新規
  - [ ] NAS `models/whisper/` 列挙
  - [ ] ローカル SSD `<cache>/whisper/` への sha256 検証付きコピー
  - [ ] 進捗 SSE
  - [ ] Whisper ライブラリのモデルパス解決
- [ ] D5: `router.py` 新規
  - [ ] `/capability`
  - [ ] `/whisper/cache-sync` + `/events`
  - [ ] `/jobs/start`
  - [ ] `/jobs/{id}` / `/events` / `/cancel`
  - [ ] X-Agent-Token 認証
- [ ] D6: `__init__.py` で `init_clip_pipeline(role, agent_config, agent_dir)` を公開
- [ ] D7: `windows-agent/agent.py` に統合
  - [ ] `from tools.clip_pipeline import router as clip_pipeline_router, init_clip_pipeline`
  - [ ] lifespan で `init_clip_pipeline(...)` 呼び出し
  - [ ] `app.include_router(clip_pipeline_router, prefix="/clip-pipeline")`
- [ ] D8: `windows-agent/tools/image_gen/nas_mount.py` で `secretary-bot` 共有の再利用動作確認（既存同UNC検出で `N:` が共用されるはず）
- [ ] D9: `requirements.txt`（Windows Agent）
  - [ ] `openai-whisper`
  - [ ] `demucs`
  - [ ] `librosa`
  - [ ] `torch`（CUDA 対応、既存設定流用）
  - [ ] `ffmpeg-python`（既存確認）

## E. WebGUI

- [ ] E1: `src/web/routes/clip_pipeline.py`
  - [ ] `/api/clip-pipeline/jobs` POST / GET
  - [ ] `/api/clip-pipeline/jobs/{id}` GET / DELETE
  - [ ] `/api/clip-pipeline/jobs/{id}/events` SSE
  - [ ] `/api/clip-pipeline/capability` GET（Agent一覧 + 各 capability）
- [ ] E2: `src/web/app.py` ルーター登録
- [ ] E3: `src/web/static/clip_pipeline.html`
  - [ ] 動画パス入力欄（複数行、フォルダ展開）
  - [ ] モード/モデル選択
  - [ ] パラメータ入力（top_n / min_sec / max_sec / do_clips / mic_track / use_demucs）
  - [ ] 進捗バー + ログエリア（SSE）
  - [ ] 履歴テーブル + キャンセル/再実行
- [ ] E4: `src/web/static/js/pages/clip_pipeline.js`（フェッチ + SSE 接続）
- [ ] E5: `src/web/static/css/clip_pipeline.css`（任意）
- [ ] E6: WebGUI ナビに `/clip-pipeline` 追加

## F. 設定 / ドキュメント

- [x] F1: `docs/auto_kirinuki/design.md`
- [x] F2: `docs/auto_kirinuki/implementation_plan.md`（本書）
- [ ] F3: `docs/auto_kirinuki/nas_migration.md`
- [ ] F4: `docs/auto_kirinuki/api.md`（Agent API 仕様書）
- [ ] F5: `docs/auto_kirinuki/README.md`（目次）
- [ ] F6: `docs/issues.md` に auto-kirinuki セクション追加

## G. クリーンアップ / 検証

- [ ] G1: ローカル型チェック / import 整合
- [ ] G2: ユニットテスト（Pi 側ユニット / Dispatcher ロジック）
- [ ] G3: 実機疎通（Main/Sub PC 上で Agent 起動 + Pi から enqueue）
  - 注: リモート PC では実施不可。Sub PC / Main PC で再開時にチェック
- [ ] G4: 旧リポジトリ `streamarchive-auto-kirinuki` の参考用コメント追加（削除しない）

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
