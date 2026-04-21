# auto-kirinuki（配信切り抜き）

Raspberry Pi の secretary-bot に統合した「配信アーカイブ → ハイライト抽出 →
EDL / MP4 出力」パイプライン。旧 `streamarchive-auto-kirinuki` を Pi 司令塔 +
Windows Agent 重処理の構成に移植し、`image_gen` と同じ Pi/Agent 分離パターンで
常駐ジョブとして運用する。

## コンポーネント

```
Discord / WebGUI
  ↓  (ジョブ登録)
src/units/clip_pipeline/        ← Pi 側ユニット
  ├── unit.py                   : ClipPipelineUnit（Cog / Discord / SSE pub-sub）
  ├── dispatcher.py             : 状態機械 + Agent 選定 + SSE 購読
  ├── agent_client.py           : /clip-pipeline/* の httpx ラッパ
  └── models.py                 : 状態定数 / DEFAULT_PARAMS / TransitionEvent
  ↓  HTTP + SSE (X-Agent-Token)
windows-agent/tools/clip_pipeline/   ← Windows Agent 側ツール
  ├── router.py                 : FastAPI `/clip-pipeline/*` ルーター
  ├── runner.py                 : ClipJob + run_pipeline スレッド駆動
  ├── whisper_cache.py          : NAS → ローカル SSD 同期（SSE 進捗）
  └── pipeline/                 : 旧 streamarchive-auto-kirinuki の移植
      ├── pipeline.py           : 7 ステップのオーケストレータ
      ├── preprocess_audio.py   : ffmpeg トラック抽出 + Demucs 分離
      ├── transcribe.py         : faster-whisper 文字起こし
      ├── analyze_audio.py      : librosa RMS/onset/pitch
      ├── emotion.py            : 感情スコアリング
      ├── highlight.py          : Ollama ハイライト判定
      ├── export_edl.py         : CMX 3600 EDL 出力
      └── export_clips.py       : ffmpeg -c copy で MP4 切り出し
```

## 関連ドキュメント

- [design.md](./design.md) — アーキテクチャ / DB スキーマ / API 概要
- [implementation_plan.md](./implementation_plan.md) — Phase 1 タスク + 進捗
- [nas_migration.md](./nas_migration.md) — NAS 共有 `ai-image` → `secretary-bot` 再編手順
- [api.md](./api.md) — Windows Agent `/clip-pipeline/*` API 仕様書
- 旧リポジトリ: `C:/Users/yamatoishida/Documents/git/streamarchive-auto-kirinuki`（参考用に残置。削除不可）

## DB スキーマ

`src/database/_base.py` migration v32 で以下を追加:

- `clip_pipeline_jobs`（status / step / video_path / output_dir / whisper_model / ollama_model / params_json / result_json / agent_id / retry_count / next_attempt_at / timeout_at ほか）
- `clip_pipeline_job_events`（遷移履歴 / from_status / to_status / agent_id / detail_json）
- インデックス: `(status, created_at)` / `(status, timeout_at)` / `(job_id, created_at)`

ヘルパは `src/database/clip_pipeline.py` の `ClipPipelineMixin` に集約。

## ジョブ状態機械

```
queued → dispatching → [warming_cache →] running → done
                                               ↘  failed  (is_retryable で再試行)
                                               ↘  cancelled
```

`warming_cache` は Agent の `/capability` で Whisper モデルが SSD にない場合のみ通る。
Agent 側は 1 ジョブ固定（GPU 単一枚前提）。複数 Agent 登録時は `preferred=sub_pc`
（priority=1）で Sub PC を優先採用。

## 設定

### Pi 側 `config.yaml.example` → `units.clip_pipeline`

```yaml
units:
  clip_pipeline:
    enabled: true
    discord_output_channel_id: 0
    default_whisper_model: "large-v3"
    default_ollama_model: "qwen3:14b"
    defaults:
      top_n: 0
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
      warming_cache_seconds: 1800
      running_default_seconds: 7200
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

### Windows Agent `agent_config.yaml.example` → `clip_pipeline`

```yaml
clip_pipeline:
  enabled: true
  root: "C:/secretary-bot/clip-pipeline"
  cache: "C:/secretary-bot-cache/whisper"
  whisper_cache:
    max_size_gb: 20
    default_model: "large-v3"
  nas:
    host: ""                 # .env の NAS_HOST を上書き優先
    share: "secretary-bot"
    subpath: "auto-kirinuki"
    mount_drive: "N:"
```

## 実機疎通手順（抜粋）

1. NAS に `secretary-bot\auto-kirinuki\models\whisper\<model_name>\` を配置（`model.bin`, `config.json`, `tokenizer.json` など一式）
2. Main PC / Sub PC で `pip install -r windows-agent/requirements.txt`
3. Agent 再起動: `start_agent.bat`（`/health` で起動確認）
4. Pi 再起動 → migration v32 が走り `clip_pipeline_jobs` が生成されること確認
5. Discord で `切り抜き この配信を` と動画パスを添えて投げる、もしくは WebGUI 側 UI（Phase E で実装）から enqueue
6. Sub PC Agent が warming_cache → running へ遷移し、NAS の outputs 配下に `timeline.edl` / `clip_XXX.mp4` / `transcript.json` / `highlights.json` が揃うこと

## Phase 進捗

| フェーズ | 状態 |
|---|---|
| A. NAS 再編 | 完了 |
| B. DB / 共通基盤 | 完了 |
| C. Pi 側ユニット | 完了 |
| D. Windows Agent | 完了（コード実装、実機疎通未検証） |
| E. WebGUI | 完了（`/api/clip-pipeline/*` + `#clip-pipeline` SPA ページ） |
| F. 設定 / ドキュメント | 完了（本書含む） |
| G. クリーンアップ / 検証 | 未着手 |

最新は [implementation_plan.md](./implementation_plan.md) 参照。
