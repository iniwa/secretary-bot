# auto-kirinuki 設計書

配信アーカイブ動画から Whisper 文字起こし + 音声/感情分析 + Ollama 判定で切り抜き候補を自動生成し、DaVinci Resolve で読み込める EDL とオプションの MP4 を出力するユニット。旧リポジトリ `streamarchive-auto-kirinuki` を secretary-bot に統合したもの。

## 1. 基本方針

- `image_gen` と同じ Pi/Agent 分離パターンを踏襲する
- Pi（Raspberry Pi）は司令塔：ジョブ登録・状態機械・AgentPool 選択・SSE 配信・Discord/WebGUI 連携
- Windows Agent は重処理：Whisper / Demucs / librosa / 感情推定 / Ollama 判定 / FFmpeg 切り出し
- GPU は 1 エージェントあたり 1 枚のため 1 エージェント 1 ジョブ固定
- 結果物は NAS 経由で共有（Pi から履歴表示・Discord 添付が可能）

## 2. NAS ディレクトリ再編

### 共有構成
既存 `ai-image` 共有から `secretary-bot` 共有へ移行。`secretary-bot` 配下に `ai-image/` と `auto-kirinuki/` を並置する。

```
\\NAS\secretary-bot\
├── ai-image\             （既存 ai-image 共有の中身を移動）
│   ├── models\
│   ├── outputs\
│   ├── lora_datasets\
│   ├── lora_work\
│   ├── workflows\
│   └── snapshots\
└── auto-kirinuki\        （新規）
    ├── inputs\           ← 任意、動画置き場
    ├── outputs\
    │   └── <video_name>\
    │       ├── transcript.json
    │       ├── audio_features.json
    │       ├── emotions.json
    │       ├── highlights.json
    │       ├── timeline.edl
    │       └── clips\
    └── models\
        └── whisper\      ← Whisper モデルの正本（Agent ローカル SSD に同期）
```

### パスマッピング
| 実行元 | マウントベース | image_gen base | auto-kirinuki base |
|---|---|---|---|
| Pi (Linux) | `/mnt/secretary-bot/` | `/mnt/secretary-bot/ai-image` | `/mnt/secretary-bot/auto-kirinuki` |
| Main/Sub PC | `N:\` | `N:\ai-image` | `N:\auto-kirinuki` |

### 移行方針
既存 `ai-image` 共有を**残したまま**新規 `secretary-bot` 共有を作成し、中身をコピー後に旧共有を停止する方式。詳細は `nas_migration.md` を参照。

## 3. アーキテクチャ

```
[Discord] [WebGUI]
    ↓         ↓
[ClipPipelineUnit (Pi)]                    ← enqueue/list/cancel + SSE配信
    ↓
[Dispatcher (Pi)]                          ← 状態機械 queued→dispatching→running→done
    ↓
[AgentPool.select_agent(preferred="sub")]  ← Sub PC 優先、Main PC フォールバック
    ↓
[AgentClient (HTTP + SSE)]
    ↓
[Windows Agent: tools/clip_pipeline/router.py]
    ↓
[runner.py → pipeline.run_pipeline()]
    ├── preprocess_audio (FFmpeg + Demucs)
    ├── transcribe (Whisper, CUDA)
    ├── analyze_audio (librosa)
    ├── emotion (LLM/音声特徴ベース)
    ├── highlight (Ollama)
    ├── export_edl (CMX 3600)
    └── export_clips (FFmpeg -c copy)
         ↓
     [NAS: \\secretary-bot\auto-kirinuki\outputs\<video_name>\]
```

## 4. ディレクトリ構成（新規追加のみ）

```
secretary-bot/
├── src/units/clip_pipeline/
│   ├── __init__.py
│   ├── unit.py              # ClipPipelineUnit(BaseUnit)
│   ├── dispatcher.py        # ジョブ状態機械
│   ├── agent_client.py      # Agent HTTP/SSE ラッパー
│   └── models.py            # JobStatus / TransitionEvent / STEP 定数
├── src/web/routes/clip_pipeline.py
├── src/web/static/clip_pipeline.html
├── windows-agent/tools/clip_pipeline/
│   ├── __init__.py
│   ├── router.py            # FastAPI router
│   ├── runner.py            # ジョブ実行・キャンセル・SSE emit
│   ├── pipeline.py          # 7ステップ制御（旧 pipeline.py 移植）
│   ├── preprocess_audio.py
│   ├── transcribe.py
│   ├── analyze_audio.py
│   ├── emotion.py
│   ├── highlight.py
│   ├── export_edl.py
│   ├── export_clips.py
│   └── whisper_cache.py     # NAS→ローカル SSD 同期
└── docs/auto_kirinuki/
    ├── design.md            # 本書
    ├── implementation_plan.md
    ├── nas_migration.md
    └── api.md               # Agent API 仕様
```

## 5. DB スキーマ

```sql
CREATE TABLE clip_pipeline_jobs (
  id TEXT PRIMARY KEY,                    -- UUID hex
  user_id TEXT NOT NULL,
  platform TEXT NOT NULL,                 -- 'discord' | 'webgui'
  status TEXT NOT NULL,                   -- queued/dispatching/warming_cache/running/done/failed/cancelled
  assigned_agent TEXT,
  video_path TEXT NOT NULL,
  output_dir TEXT NOT NULL,
  mode TEXT NOT NULL,                     -- 'test' | 'normal'
  whisper_model TEXT NOT NULL,
  ollama_model TEXT NOT NULL,
  params_json TEXT,                       -- top_n/min_clip_sec/max_clip_sec/do_export_clips/mic_track/use_demucs/sleep_sec
  step TEXT,                              -- preprocess/transcribe/analyze/emotion/highlight/edl/clips
  progress INTEGER DEFAULT 0,             -- 0-100
  result_json TEXT,                       -- {transcript_path, highlights_count, edl_path, clip_paths[]}
  last_error TEXT,
  retry_count INTEGER DEFAULT 0,
  max_retries INTEGER DEFAULT 2,
  cache_sync_id TEXT,                     -- warming_cache 中の同期 ID
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX idx_clip_jobs_status ON clip_pipeline_jobs(status);
CREATE INDEX idx_clip_jobs_user ON clip_pipeline_jobs(user_id, created_at DESC);
```

## 6. ジョブ状態機械

```
queued ──► dispatching ──► warming_cache* ──► running ──► done
  │             │                │                │
  │             ▼                ▼                ▼
  └─◄─ retry (backoff) ── failed ◄─────────── failed
                │
                ▼
           cancelled （任意の非終端から）
```

`warming_cache` は Whisper モデル未キャッシュ時のみ挟む（後述のキャッシュ同期）。

ステップ重み（pipeline 内 progress）:
| step | weight |
|---|---|
| preprocess | 0.10 |
| transcribe | 0.25 |
| analyze_audio | 0.15 |
| emotion | 0.25 |
| highlight | 0.20 |
| edl | 0.02 |
| clips | 0.03 |

## 7. Agent API (`/clip-pipeline/*`)

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/clip-pipeline/capability` | CUDA可否 / Whisper / Demucs / Ollamaモデル / キャッシュ済みWhisper一覧 / busy状態 |
| POST | `/clip-pipeline/whisper/cache-sync` | Whisperモデル NAS→ローカル SSD 同期を開始（body: `{model_name}`） |
| GET | `/clip-pipeline/whisper/cache-sync/{sync_id}/events` | SSE（進捗0-100、完了） |
| POST | `/clip-pipeline/jobs/start` | ジョブ開始（body: video_path, output_dir, whisper_model, ollama_model, params） |
| GET | `/clip-pipeline/jobs/{id}` | 現在状態（snapshot） |
| GET | `/clip-pipeline/jobs/{id}/events` | SSE（step/progress/log/result） |
| POST | `/clip-pipeline/jobs/{id}/cancel` | キャンセル |

認証: 全エンドポイント `X-Agent-Token` 必須（既存の `AGENT_SECRET_TOKEN`）。

## 8. Whisper モデル自動キャッシュ

`image_gen` の cache_manager 方式を踏襲。

- NAS の `auto-kirinuki/models/whisper/` にモデル正本を配置
- Agent ローカル SSD（`<agent_root>/cache/whisper/`）にコピーして使用
- `sha256` サイドカー検証で再コピー判定
- ジョブ投入時に `whisper_model` が未キャッシュなら `warming_cache` ステータスへ遷移し `cache-sync` を自走

**自動同期ポリシー（決定事項）**: `model_sync` ユニット相当の定期ポーリングは作らず、**ジョブ開始時に必要なモデルを自動同期**する。初回は必然的に `warming_cache` を通る。

## 9. Discord 連携

### コマンド例
- `切り抜き D:\videos\stream_01.mp4` → enqueue、受付メッセージを返す
- `切り抜き status <job_id>` → 進捗確認
- `切り抜き list` → 直近ジョブ一覧
- `切り抜き cancel <job_id>` → キャンセル

`_discord_notifier_loop()` で完了時に EDL パス・highlights 件数・mentions ユーザーへ通知。チャンネルは `config.yaml` の `units.clip_pipeline.discord_output_channel_id` または起動元チャンネル。

## 10. WebGUI (`/clip-pipeline`)

- 動画パス複数行入力（フォルダ展開、NAS UNC / Agent ローカル両対応）
- モード切替（test / normal）・Whisper/Ollama モデル・クリップ長・MP4切出・mic_track・Demucsトグル
- 進捗: SSE で全体プログレスバー + ステップ別進捗 + ログ
- 履歴: ジョブ一覧・結果ファイルリンク・キャンセル・再実行

## 11. 負荷制御（既存仕組みを流用）

- `AgentPool.priority`: Sub PC=1（配信編集用優先）、Main PC=2
- `ActivityDetector` の `block_rules.gaming_on_main=true` でゲーム中の Main PC を自動除外
- WebGUI の agent mode: allow/deny/auto
- `/capability` の `busy: bool` で同時実行を回避

## 12. 設定項目（config.yaml 追加分）

```yaml
units:
  clip_pipeline:
    enabled: true
    discord_output_channel_id: 0
    default_mode: "normal"             # test | normal
    default_whisper_model: "large-v3"
    default_ollama_model: "qwen3:14b"
    defaults:
      top_n: 0                          # 0=無制限
      min_clip_sec: 30
      max_clip_sec: 180
      do_export_clips: false
      mic_track: 1
      use_demucs: true
      sleep_sec: 2
    retry:
      max_retries: 2
      base_backoff_seconds: 30
      max_backoff_seconds: 300
    timeouts:
      dispatching_seconds: 30
      warming_cache_seconds: 1800       # 大モデルで長め
      running_default_seconds: 7200     # 1h 動画 × large-v3 の余裕値
      queued_seconds: 86400
    dispatcher:
      poll_interval_seconds: 2
      stuck_reaper_interval_seconds: 30
      progress_debounce_seconds: 2
    nas:
      base_path: "/mnt/secretary-bot/auto-kirinuki"
      outputs_subdir: "outputs"
      whisper_models_subdir: "models/whisper"
      inputs_subdir: "inputs"
```

Agent 側 `agent_config.yaml`:
```yaml
clip_pipeline:
  enabled: true
  root: "C:/secretary-bot/clip-pipeline"
  cache: "C:/secretary-bot-cache/whisper"
  nas:
    share: "secretary-bot"
    subpath: "auto-kirinuki"
    mount_drive: "N:"                   # image_gen と共用
```

## 13. 旧リポジトリの扱い

`C:/Users/yamatoishida/Documents/git/streamarchive-auto-kirinuki` は参考用として残置。統合完了後も削除しない（移植元比較用）。

## 14. 将来拡張（Phase 2 以降）

- 複数動画のキューイング（Dispatcher が順次処理）
- 完了通知のデスクトップ通知・Webhook
- DaVinci Resolve API 直接連携（EDL ではなく直接タイムライン投入）
- 音量・波形ベースの補助ハイライト検出
