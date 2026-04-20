# auto-kirinuki Agent API 仕様

Pi 側 `src/units/clip_pipeline/` Dispatcher から Windows Agent
（`windows-agent/tools/clip_pipeline/router.py`）を叩く HTTP / SSE API の仕様。

- ベース URL: `http://<agent host>:7777`
- 全エンドポイントは `/clip-pipeline/` プレフィックス配下
- 認証: リクエストヘッダ `X-Agent-Token: <AGENT_SECRET_TOKEN>`（未設定時はスキップ）
- トレース: `X-Trace-Id` リクエスト/レスポンス共通ヘッダ（未指定時は Agent 側で生成）
- 文字コード: UTF-8 / JSON

## エラーレスポンス形式

すべての 4xx / 5xx は以下の JSON を返す:

```json
{
  "error_class": "ValidationError",
  "message": "video_path not found on agent: N:\\auto-kirinuki\\inputs\\foo.mp4",
  "retryable": false,
  "detail": {"trace_id": "agent_abcd1234"}
}
```

Pi 側 `src/errors.py` の例外階層にマップされる。`ClipPipelineError` が基底、
`WhisperError` / `TranscribeError` / `HighlightError` / `CacheSyncError` /
`ValidationError` / `ResourceUnavailableError` / `TransientError` /
`AgentCommunicationError` が派生。

| HTTP | error_class 既定 | 意味 |
|---|---|---|
| 400 | `ValidationError` | payload 不備・非リトライ |
| 401 | `AuthError` | トークン不一致・非リトライ |
| 404 | `ValidationError` | job_id / sync_id 不明 |
| 409 | `ValidationError` | 既に終端状態 |
| 423 | `ResourceUnavailableError` | Agent ビジー / Whisper 未キャッシュ（リトライ可） |
| 500 | `ClipPipelineError` | 内部エラー |
| 503 | `ResourceUnavailableError` | 機能無効 / ComfyUI 的リソース未用意 |

## GET `/clip-pipeline/capability`

Agent の能力スナップショット。Dispatcher が Whisper モデルキャッシュ欠落判定に使う。

**レスポンス 200**:

```json
{
  "agent_id": "sub-pc",
  "role": "sub",
  "enabled": true,
  "busy": false,
  "gpu_info": {
    "name": "NVIDIA GeForce RTX 5060 Ti",
    "vram_total_mb": 16383,
    "vram_free_mb": 15000,
    "cuda_compute": "8.9"
  },
  "ffmpeg_version": "n6.1.1-full_build-www.gyan.dev",
  "whisper_models_local": ["large-v3", "base"],
  "whisper_models_nas": ["large-v3", "large-v3-turbo", "base"],
  "cache_root": "C:\\secretary-bot-cache\\whisper",
  "nas_whisper_base": "N:\\auto-kirinuki\\models\\whisper",
  "nas_inputs_base": "N:\\auto-kirinuki\\inputs",
  "nas_outputs_base": "N:\\auto-kirinuki\\outputs",
  "api_version": 2
}
```

`whisper_models_local[]` は `<cache_root>/<name>/model.bin` が存在するモデルのみ。
`whisper_models_nas[]` は NAS 上に同ディレクトリ構造で置かれているモデル。

`nas_inputs_base` / `nas_outputs_base` は Agent 視点の Windows 絶対パス（UNC もしくはマウントドライブ）。
解決不能な場合は `""`。`api_version` は今回 `2`。旧 Agent で未定義の場合は `1` として扱う。

## GET `/clip-pipeline/inputs`

NAS `inputs/` 直下の動画ファイル一覧を返す。WebGUI の動画選択 UI から呼ばれる。

- 対象拡張子（大小無視）: `.mp4 .mkv .mov .avi .ts .m2ts .webm .flv`
- **トップレベルのみ列挙**（再帰なし）
- ファイル名の昇順でソート
- base が存在しない／読めない場合でも 200 を返し、`error` に理由を載せる（409 等にはしない）

**レスポンス 200**:

```json
{
  "base": "N:\\auto-kirinuki\\inputs",
  "files": [
    {
      "name": "stream_20260419.mkv",
      "full_path": "N:\\auto-kirinuki\\inputs\\stream_20260419.mkv",
      "size": 12345678,
      "mtime": 1713654321
    }
  ]
}
```

**レスポンス 200（base 未マウント等）**:

```json
{
  "base": "N:\\auto-kirinuki\\inputs",
  "files": [],
  "error": "inputs base not found or not a directory: N:\\auto-kirinuki\\inputs"
}
```

- `size`: バイト数（int）
- `mtime`: epoch 秒（int）
- `full_path`: Agent 視点の絶対パス。そのまま `/jobs/start` の `video_path` に渡せる。

**503**: `clip_pipeline` が無効な Agent で呼ぶと `ResourceUnavailableError` を返す。

## POST `/clip-pipeline/whisper/cache-sync`

NAS の Whisper モデルをローカル SSD へ同期。Dispatcher が warming_cache 遷移時に呼ぶ。

**リクエスト**:

```json
{
  "model": "large-v3",
  "sha256": null
}
```

- `model` (string, required): モデルディレクトリ名（NAS 側の `models/whisper/` 直下のフォルダ名）
- `sha256` (string, optional): 予約フィールド（現状未使用。将来 `model.bin` の検証に使う）

**レスポンス 202**:

```json
{
  "sync_id": "wsync_abc1234567890def",
  "status": "queued",
  "model": "large-v3",
  "progress_url": "/clip-pipeline/whisper/cache-sync/wsync_abc1234567890def/events"
}
```

### GET `/clip-pipeline/whisper/cache-sync/{sync_id}/events`

Server-Sent Events。進行中は連続してイベントを配信、完了/失敗/キャンセル時は `done` で終端。

**イベント種別**:
- `status` `{status: "queued"|"running"|"done"|"failed"|"cancelled", skipped?: bool}`
- `progress` `{current_file: string, bytes_done: int, total_bytes: int}`
- `error` `{error_class, message, retryable}`
- `done` `{}`（終端。このあと接続を閉じる）

キープアライブ: 15 秒操作無しで `: keepalive\n\n` を吐く。

### POST `/clip-pipeline/whisper/cache-sync/{sync_id}/cancel`

実行中キャッシュ同期のキャンセル要求。

**レスポンス 200**: `{ok: true, status: "cancelled"}`

## POST `/clip-pipeline/jobs/start`

切り抜きジョブを開始。1 Agent あたり同時 1 ジョブまで（`busy` 時は 423）。

**リクエスト**:

```json
{
  "job_id": "cpj_abcd1234",
  "video_path": "N:\\auto-kirinuki\\inputs\\stream_20260419.mkv",
  "output_dir": "N:\\auto-kirinuki\\outputs\\stream_20260419",
  "mode": "normal",
  "whisper_model": "large-v3",
  "ollama_model": "qwen3:14b",
  "params": {
    "top_n": 0,
    "min_clip_sec": 30,
    "max_clip_sec": 180,
    "do_export_clips": false,
    "mic_track": 1,
    "use_demucs": true,
    "sleep_sec": 2
  },
  "timeout_sec": 7200
}
```

- `video_path` / `output_dir` は Agent から見える **絶対パス**（NAS UNC or 既マウントドライブ経由）
- `mode`: `"normal"` | `"test"`。テストモードは短縮パイプライン想定（現実装はフル実行）
- `params` の各キーは省略可能（`run_pipeline` の既定値にフォールバック）

**レスポンス 202**:

```json
{
  "job_id": "cpj_abcd1234",
  "status": "running",
  "progress_url": "/clip-pipeline/jobs/cpj_abcd1234/events"
}
```

**冪等性**: 既に同じ `job_id` で登録されたジョブがあれば、同じ `progress_url` を再掲。

### GET `/clip-pipeline/jobs/{job_id}`

ジョブスナップショットの取得。Dispatcher のフォールバック用。

**レスポンス 200**:

```json
{
  "job_id": "cpj_abcd1234",
  "status": "running",
  "progress": 0.42,
  "step": "emotion",
  "started_at": 1714100000.0,
  "finished_at": null,
  "result": {},
  "last_error": null
}
```

### GET `/clip-pipeline/jobs/{job_id}/events`

SSE 進捗ストリーム。Dispatcher の `_monitor_running` はこれを購読する。

**イベント種別**:
- `status` `{status: "running"|"done"|"failed"|"cancelled"}`
- `step` `{step: "preprocess"|"transcribe"|"analyze"|"emotion"|"highlight"|"edl"|"clips"}`
- `progress` `{percent: 0-100, desc: string}`
- `log` `{message: string}`
- `result` `{highlights_count: int, edl_path: string, clip_paths: string[], transcript_path: string}`
- `error` `{error_class, message, retryable, traceback?}`
- `done` `{}`（終端）

### POST `/clip-pipeline/jobs/{job_id}/cancel`

実行中ジョブのキャンセル要求。各ステップ境界でチェックされ速やかに `cancelled` へ遷移する。

**レスポンス 200**: `{ok: true, status: "cancelled"}`

## Pi 側 Dispatcher の状態機械との対応

```
queued
  ↓ (dispatcher picks)
dispatching                          # Agent 選定中
  ↓ (capability() で Whisper 欠落時のみ)
warming_cache                        # POST /whisper/cache-sync → SSE 購読
  ↓ (done)
running                              # POST /jobs/start → SSE 購読
  ↓ (result event)
done                                 # result_json をDBへ書き込み
```

- `failed` / `cancelled` は上記任意の遷移で発生しうる
- `failed` の `is_retryable()` が True なら `max_retries` の範囲でリトライ（バックオフ: `base_backoff_seconds` × 2^attempt、上限 `max_backoff_seconds`）
- `timeout_sec` を超過したジョブは `_stuck_reaper_worker` が `failed` に遷移（リトライ可）

## タイムアウト既定値

`config.yaml.example` の `units.clip_pipeline.timeouts`:
- `dispatching_seconds`: 30（dispatching に留まる上限）
- `warming_cache_seconds`: 1800（Whisper 同期：large-v3 NAS→SSD で 3–10 分、余裕持たせて 30 分）
- `running_default_seconds`: 7200（1 時間動画 × large-v3 の保守値。ジョブ単位で上書き可）
- `queued_seconds`: 86400（1 日滞留したらスタック扱い）
