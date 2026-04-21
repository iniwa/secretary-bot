# 画像生成 Windows Agent API 仕様書

> 対象: `windows-agent/agent.py`（FastAPI, `:7777`）の画像生成関連エンドポイント
> 関連: [`design.md`](design.md)
> 版: 2026-04-15（初版）

---

## 1. 共通仕様

### 1.1 ベース URL

```
http://<agent_host>:7777
```

Agent は Main PC / Sub PC それぞれで起動。Pi 側の `AgentPool` が priority 順に振り分ける。

### 1.2 認証

全エンドポイントで下記ヘッダを必須（`/health` を含む既存方針を継承）。

| ヘッダ | 値 | 備考 |
|---|---|---|
| `X-Agent-Token` | `.env` の `AGENT_SECRET_TOKEN` と一致する文字列 | 不一致は `401 Unauthorized` |

### 1.3 トレース伝搬

| ヘッダ | 値 | 備考 |
|---|---|---|
| `X-Trace-Id` | Pi 側で採番した trace ID | Agent ログ・ComfyUI リクエスト・SSE 応答に載せて返す。省略時は Agent が採番 |

### 1.4 エラーレスポンス共通フォーマット

失敗時はステータスコード問わず以下の JSON を返す:

```json
{
  "error_class": "ComfyUIError.OOMError",
  "message": "CUDA out of memory at KSampler",
  "retryable": true,
  "detail": {
    "node_id": "6",
    "vram_free_mb": 120
  }
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `error_class` | string | `BotError` 継承階層のクラス名（ドット区切りでネスト） |
| `message` | string | 1 行の要約、ユーザー向け表示にも流用可 |
| `retryable` | boolean | Pi Dispatcher がリトライ判定に用いる |
| `detail` | object | 追加情報（任意キー。`trace_id` / `job_id` / `node_id` / ログ抜粋など） |

ステータスコードの割当:

| コード | 用途 |
|---|---|
| `400` | `ValidationError` / 入力不正 |
| `401` | 認証失敗 |
| `404` | `job_id` / `sync_id` 不明 |
| `409` | 重複投入・排他違反（例: 学習中の別学習投入） |
| `423` | `capability.busy=true` で受け付け不可 |
| `500` | `ComfyUIError` / 予期せぬ内部例外 |
| `503` | `ResourceUnavailableError` / ComfyUI プロセス未起動・NAS 切断 |

### 1.5 進捗ストリーム（SSE 共通フォーマット）

`text/event-stream`。各イベントは `event:` + `data:`（JSON）で構成。接続保持のため 15 秒ごとに `: keepalive` コメントを送る。

共通イベント:

| event | data スキーマ | 意味 |
|---|---|---|
| `progress` | `{ "percent": 0-100, "step": int, "total": int, "note": string }` | 実行進捗 |
| `log` | `{ "level": "info"\|"warn"\|"error", "message": string, "ts": ISO8601 }` | 標準出力・標準エラーのダイジェスト |
| `status` | `{ "status": string, "detail": object }` | 状態遷移（`queued` / `running` / `done` など） |
| `result` | `{ "result_paths": [string], "artifacts": object }` | 完了時の成果物 |
| `error` | 1.4 のエラーレスポンスと同一 | 失敗時 |
| `done` | `{}` | ストリーム終端。クライアントは接続を閉じてよい |

ヘッダ:

- `X-Trace-Id`: リクエストと同値を応答ヘッダにも反映
- `Cache-Control: no-store`

---

## 2. Capability / Health

### 2.1 GET `/capability`

Agent の構成・導入済みモデル・稼働状態を返す。Pi の `model_sync` が定期ポーリングし、ジョブ投入時のルーティング判断に使う。

**リクエスト**: パラメータなし。

**レスポンス 200**:

```json
{
  "agent_id": "main-pc",
  "comfyui_version": "0.3.10",
  "comfyui_available": true,
  "has_kohya": true,
  "kohya_version": "2025-03-15",
  "busy": false,
  "updates_available": {
    "comfyui": false,
    "kohya": true,
    "custom_nodes": ["rgthree-comfy"]
  },
  "custom_nodes": [
    { "name": "ComfyUI-Manager", "commit": "abc1234" }
  ],
  "models": [
    { "type": "checkpoints", "filename": "chenkinNoobXL_v05.safetensors" }
  ],
  "loras": [
    { "type": "loras", "filename": "project_a/epoch10.safetensors", "starred": true }
  ],
  "vaes": [{ "filename": "sdxl_vae.safetensors" }],
  "embeddings": [],
  "upscale_models": [],
  "gpu_info": {
    "name": "NVIDIA GeForce RTX 4080",
    "vram_total_mb": 16384,
    "vram_free_mb": 15200,
    "cuda_compute": "8.9"
  },
  "cache_usage": { "used_gb": 38.4, "limit_gb": 100 }
}
```

**サイドエフェクト**: なし（読み取りのみ）。ComfyUI 未起動時は `comfyui_available=false` を返し、他フィールドは可能な範囲で埋める。

### 2.2 GET `/health`（既存）

**リクエスト**: パラメータなし。

**レスポンス 200**: `{ "status": "ok", "version": "<agent_version>", "uptime_sec": int }`

**備考**: 認証のみ要求。Pi `AgentPool` のヘルスチェックで使用。

---

## 3. キャッシュ同期

### 3.1 GET `/cache/manifest`

Agent ローカル SSD（`${SECRETARY_BOT_CACHE}/models/`）の現在のキャッシュ状況を返す。

**レスポンス 200**:

```json
{
  "agent_id": "main-pc",
  "generated_at": "2026-04-15T12:34:56+09:00",
  "entries": [
    {
      "type": "checkpoints",
      "filename": "chenkinNoobXL_v05.safetensors",
      "sha256": "abc...",
      "size": 6738902345,
      "mtime": "2026-04-12T10:00:00+09:00",
      "last_used_at": "2026-04-14T22:10:11+09:00",
      "starred": false
    }
  ],
  "cache_usage": { "used_gb": 38.4, "limit_gb": 100 }
}
```

**サイドエフェクト**: なし。

### 3.2 POST `/cache/sync`

NAS → ローカル SSD への同期ジョブを開始する。即時 `202 Accepted` を返し、進捗は SSE または polling で取得。

**リクエストボディ**:

```json
{
  "files": [
    {
      "type": "checkpoints",
      "filename": "chenkinNoobXL_v05.safetensors",
      "nas_path": "//nas/secretary-bot/ai-image/models/checkpoints/chenkinNoobXL_v05.safetensors",
      "sha256": "abc...",
      "size": 6738902345
    }
  ],
  "priority": "high",
  "reason": "job_id=...",
  "verify_sha256": true
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `files[]` | array | ✓ | 同期対象 |
| `files[].type` | enum(`checkpoints`/`loras`/`vae`/`embeddings`/`upscale_models`/`clip`/`controlnet`) | ✓ | サブディレクトリ判定 |
| `files[].filename` | string | ✓ | サブディレクトリ配下の相対パス |
| `files[].nas_path` | string | ✓ | NAS 側の絶対 SMB パス |
| `files[].sha256` | string | ✓ | 検証基準（不一致で `CacheSyncError`） |
| `files[].size` | int | 任意 | 事前チェック用 |
| `priority` | enum(`high`/`normal`) | 任意 | 既定 `normal`。`high` はウォームアップより優先 |
| `reason` | string | 任意 | ログ用（`job_id=...` 等） |
| `verify_sha256` | boolean | 任意 | 既定 `true`。`false` なら mtime + size のみ |

**レスポンス 202**:

```json
{
  "sync_id": "sync_01H...",
  "status": "queued",
  "total_bytes": 7516192768,
  "progress_url": "/cache/sync/sync_01H.../stream"
}
```

**エラー**:
- `400 ValidationError`: `type` 不正 / パス不正
- `503 ResourceUnavailableError`: NAS 未接続

**サイドエフェクト**: バックグラウンドで SMB 経由のコピーを開始。temp ファイル → rename で原子的に配置。完了後 `model_cache_manifest` 反映のため Pi の `/cache/sync/.../stream` 経由で結果を通知。

### 3.3 GET `/cache/sync/{sync_id}/stream`（SSE）

同期ジョブの進捗を SSE で送出。

**イベント**:

| event | data |
|---|---|
| `progress` | `{ "percent": int, "current_file": string, "bytes_done": int, "bytes_total": int, "mbps": float }` |
| `file_done` | `{ "filename": string, "sha256_ok": bool }` |
| `status` | `{ "status": "queued"\|"running"\|"done"\|"failed"\|"cancelled" }` |
| `error` | 1.4 フォーマット（`CacheSyncError` など） |
| `done` | `{}` |

**サイドエフェクト**: なし（既存ジョブの購読）。

### 3.4 POST `/cache/sync/{sync_id}/cancel`

進行中の同期をキャンセル。

**レスポンス 200**: `{ "ok": true, "status": "cancelled" }`

**エラー**: `404` / `409`（既に終端状態）

**サイドエフェクト**: 現在の temp ファイルを削除。配置済みファイルは保持。

---

## 4. 画像生成

### 4.1 POST `/image/generate`

ComfyUI にワークフローを投入する。即時 `202` を返し、進捗は SSE または polling で取得。

**リクエストボディ**:

```json
{
  "job_id": "img_01H...",
  "workflow_json": { "3": { "inputs": {...}, "class_type": "KSampler" }, "...": {...} },
  "inputs": {
    "positive": "1girl, forest, ...",
    "negative": "lowres, bad anatomy, ...",
    "seed": 1234567890,
    "steps": 30,
    "cfg": 5.5,
    "sampler_name": "euler_ancestral",
    "scheduler": "normal",
    "width": 1024,
    "height": 1024,
    "ckpt_name": "chenkinNoobXL_v05.safetensors",
    "lora_1": "project_a/epoch10.safetensors",
    "lora_1_w": 0.8,
    "filename_prefix": "2026-04-15_123456_img_01H_1234567890",
    "output_dir": "//nas/secretary-bot/ai-image/outputs/2026-04/2026-04-15"
  },
  "timeout_sec": 300,
  "required_models": [
    { "type": "checkpoints", "filename": "chenkinNoobXL_v05.safetensors" }
  ]
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `job_id` | string | ✓ | Pi 側採番の UUID。Agent 内で冪等キーとして利用 |
| `workflow_json` | object | ✓ | ComfyUI API Format。プレースホルダは Pi 側で置換済み |
| `inputs` | object | 任意 | デバッグ・ログ・メタデータ埋め込み用（置換済みでも再掲） |
| `timeout_sec` | int | 任意 | 既定はプリセット依存。Agent 側でタイムアウト監視（未指定時の扱いは §12.4） |
| `required_models[]` | array | 任意 | Pi が検証済みの前提で Agent が最終チェック |

**レスポンス 202**:

```json
{
  "job_id": "img_01H...",
  "status": "running",
  "progress_url": "/image/jobs/img_01H.../stream",
  "comfyui_prompt_id": "a1b2c3d4"
}
```

**エラー**:
- `400 WorkflowValidationError`: ComfyUI が workflow を拒否
- `400 ValidationError`: 必須モデル欠損・プレースホルダ未解決
- `409`: 同一 `job_id` で既に稼働中
- `423`: `busy=true`（学習中）
- `503`: ComfyUI プロセス未起動

**サイドエフェクト**:
- ComfyUI `/prompt` にキュー投入
- WebSocket `/ws` 購読開始、進捗を内部バッファに保存
- 完了時 NAS `outputs/YYYY-MM/YYYY-MM-DD/` へ書き出し（`extra_model_paths.yaml` と `output_dir` で解決）

### 4.2 GET `/image/jobs/{job_id}`

ジョブの現在状態をスナップショットで返す（polling 向け）。

**レスポンス 200**:

```json
{
  "job_id": "img_01H...",
  "status": "running",
  "progress": 42,
  "started_at": "2026-04-15T12:34:56+09:00",
  "finished_at": null,
  "comfyui_prompt_id": "a1b2c3d4",
  "result_paths": [],
  "last_error": null
}
```

完了時は `status="done"`、`result_paths` に NAS パス配列（例: `["//nas/secretary-bot/ai-image/outputs/2026-04/2026-04-15/2026-04-15_123456_img_01H_1234567890.png"]`）。

**エラー**: `404`

**サイドエフェクト**: なし。

### 4.3 GET `/image/jobs/{job_id}/stream`（SSE）

生成ジョブの進捗を SSE 配信。

**イベント**:

| event | data |
|---|---|
| `progress` | `{ "percent": int, "step": int, "total": int, "node_id": string }` |
| `log` | `{ "level": string, "message": string, "ts": ISO8601 }` |
| `preview` | `{ "b64_png": string, "step": int }`（ComfyUI の中間プレビュー、送出ポリシは §12.1） |
| `status` | `{ "status": "running"\|"done"\|"failed"\|"cancelled" }` |
| `result` | `{ "result_paths": [string] }` |
| `error` | 1.4 フォーマット |
| `done` | `{}` |

### 4.4 POST `/image/jobs/{job_id}/cancel`

ComfyUI に `/interrupt` を発行。

**レスポンス 200**: `{ "ok": true, "status": "cancelled" }`

**エラー**: `404` / `409`（既に終端）

**サイドエフェクト**: ComfyUI 実行中ステップを中断。既に書き出し済みの部分成果物は保持（Pi 側で削除判断）。

---

## 5. ComfyUI 管理

### 5.1 POST `/comfyui/setup`

初回セットアップ（`git clone` + `pip install` + 初期 custom_nodes 導入）。

**リクエストボディ**（任意）:

```json
{
  "ref": "main",
  "custom_nodes": ["ComfyUI-Manager", "rgthree-comfy"],
  "force": false
}
```

**レスポンス 202**: `{ "task_id": "setup_...", "stream_url": "/system/logs?task_id=..." }`

**エラー**: `409`（既にインストール済みで `force=false`）

**サイドエフェクト**:
- `${SECRETARY_BOT_ROOT}/comfyui/` に clone
- `${SECRETARY_BOT_ROOT}/venv-comfyui/` に venv 作成・依存導入
- `extra_model_paths.yaml` を `${SECRETARY_BOT_CACHE}/models/` 向けに生成
- セットアップ成功後、常駐起動（`:8188`）

### 5.2 POST `/comfyui/update`

ComfyUI 本体とカスタムノードの更新。

**リクエストボディ**（任意）:

```json
{ "include_custom_nodes": true, "restart": true }
```

**レスポンス 202**: `{ "task_id": "update_...", "stream_url": "/system/logs?task_id=..." }`

**サイドエフェクト**:
1. ComfyUI プロセス stop（graceful）
2. `git pull` + `pip install -r requirements.txt`
3. `custom_nodes_snapshot` を適用（ComfyUI-Manager 経由）
4. `restart=true` なら再起動

**エラー**: `409`（実行中のジョブあり。`busy=true`）

### 5.3 POST `/comfyui/restart`

プロセス再起動のみ。

**レスポンス 200**: `{ "ok": true, "pid": 12345 }`

**エラー**: `409`（実行中ジョブあり）/ `503`（起動失敗）

**サイドエフェクト**: PID 管理で graceful shutdown → 再起動。3 回連続失敗で `capability.comfyui_available=false`。

---

## 6. kohya_ss 管理

### 6.1 POST `/kohya/setup`

sd-scripts のセットアップ。Main/Sub 両 PC で有効。

**リクエストボディ**（任意）:

```json
{ "ref": "sdxl", "force": false }
```

**レスポンス 202**: `{ "task_id": "kohya_setup_...", "stream_url": "/system/logs?task_id=..." }`

**サイドエフェクト**:
- `${SECRETARY_BOT_ROOT}/kohya/` に clone
- `${SECRETARY_BOT_ROOT}/venv-kohya/` に venv 作成
- 常駐はしない（学習ジョブ時にのみ起動）

### 6.2 POST `/kohya/update`

**リクエストボディ**（任意）: `{ "restart": false }`（kohya は非常駐なので通常 `restart` は不要）

**レスポンス 202**: `{ "task_id": "...", "stream_url": "/system/logs?task_id=..." }`

**エラー**: `409`（学習ジョブ実行中）

**サイドエフェクト**: `git pull` + `pip install`。実行中学習があれば拒否。

---

## 7. LoRA 学習

### 7.1 POST `/lora/train/start`

LoRA 学習ジョブを開始。AgentPool の priority に従い Main/Sub いずれでも実行可能（設計書 §Windows Agent セットアップ・運用）。

**リクエストボディ**:

```json
{
  "job_id": "lora_01H...",
  "project_name": "project_a",
  "config_toml": "[general]\npretrained_model_name_or_path=...\n...",
  "dataset_path": "//nas/secretary-bot/ai-image/lora_datasets/project_a",
  "output_path": "//nas/secretary-bot/ai-image/lora_work/project_a/checkpoints",
  "sample_prompts": [
    "1girl, solo, upper body, ...",
    "1girl, solo, full body, ...",
    "1girl, solo, bust up, ..."
  ],
  "tb_logdir": "//nas/secretary-bot/ai-image/lora_work/project_a/logs"
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `job_id` | string | ✓ | Pi 採番 |
| `project_name` | string | ✓ | NAS ディレクトリ名 |
| `config_toml` | string | ✓ | kohya 向け TOML 全文 |
| `dataset_path` | string | ✓ | NAS 絶対 SMB パス |
| `output_path` | string | ✓ | NAS 絶対 SMB パス |
| `sample_prompts` | string[] | 任意 | 学習中サンプル生成用 |
| `tb_logdir` | string | 任意 | TensorBoard ログ出力先 |

**レスポンス 202**:

```json
{
  "job_id": "lora_01H...",
  "status": "running",
  "pid": 23456,
  "stream_url": "/lora/train/lora_01H.../stream"
}
```

**エラー**:
- `400 ValidationError`: TOML パース失敗 / データセット不在
- `409`: 既に同一 `job_id` 稼働中、または別の学習ジョブ実行中
- `503`: kohya 未導入（先に `/kohya/setup`）

**サイドエフェクト**:
- `capability.busy=true` に遷移（以降、生成ジョブ受付停止）
- `venv-kohya` で `sd-scripts/sdxl_train_network.py` を起動
- stdout / stderr を内部バッファに格納し SSE で中継
- サンプル画像と epoch checkpoint を `output_path` に書き出し

### 7.2 GET `/lora/train/{job_id}/status`

学習ジョブの現況。

**レスポンス 200**:

```json
{
  "job_id": "lora_01H...",
  "status": "running",
  "progress": 37,
  "current_epoch": 4,
  "total_epochs": 12,
  "loss_avg": 0.082,
  "samples": [
    { "epoch": 3, "path": "//nas/secretary-bot/ai-image/lora_work/project_a/samples/epoch3_01.png" }
  ],
  "checkpoints": [
    { "epoch": 3, "path": "//nas/secretary-bot/ai-image/lora_work/project_a/checkpoints/epoch3.safetensors" }
  ],
  "started_at": "2026-04-15T12:00:00+09:00",
  "finished_at": null,
  "last_error": null
}
```

**エラー**: `404`

### 7.3 GET `/lora/train/{job_id}/stream`（SSE）

学習進捗を SSE 配信。

**イベント**:

| event | data |
|---|---|
| `progress` | `{ "percent": int, "epoch": int, "step": int, "loss": float }` |
| `log` | `{ "level": string, "message": string, "ts": ISO8601 }` — kohya stdout/stderr ダイジェスト |
| `sample` | `{ "epoch": int, "path": string }` — サンプル画像書き出し時 |
| `checkpoint` | `{ "epoch": int, "path": string }` — epoch checkpoint 保存時 |
| `metric` | `{ "name": string, "value": float, "step": int }` — TB メトリクスの一部 |
| `status` | `{ "status": "running"\|"done"\|"failed"\|"cancelled" }` |
| `error` | 1.4 フォーマット |
| `done` | `{}` |

### 7.4 POST `/lora/train/{job_id}/cancel`

学習プロセスを中断。

**レスポンス 200**: `{ "ok": true, "status": "cancelled" }`

**エラー**: `404` / `409`（既に終端）

**サイドエフェクト**:
- kohya プロセスに SIGTERM（Windows では `terminate`）、タイムアウトで SIGKILL
- 中間 checkpoint・サンプルは保持（Pi の WebGUI から明示的に削除可能）
- `capability.busy=false` に戻す

---

## 8. システムログ

### 8.1 GET `/system/logs`

Agent の最近のログを取得。WebGUI のトラブルシュート用。

**クエリパラメータ**:

| 名前 | 型 | 既定 | 説明 |
|---|---|---|---|
| `source` | enum(`agent`/`comfyui`/`kohya`) | `agent` | 取得対象 |
| `task_id` | string | 任意 | セットアップ/更新の `task_id` 指定時はそのタスクのログのみ |
| `lines` | int | `200` | 末尾から取得する行数（最大 2000） |
| `level` | enum(`debug`/`info`/`warn`/`error`) | `info` | 閾値。以上のレベルのみ返す |
| `follow` | boolean | `false` | `true` のとき `text/event-stream` で継続配信 |

**レスポンス 200（follow=false, JSON）**:

```json
{
  "source": "comfyui",
  "lines": [
    { "ts": "2026-04-15T12:34:56+09:00", "level": "info", "message": "queue size 1" }
  ]
}
```

**レスポンス 200（follow=true, SSE）**:

| event | data |
|---|---|
| `log` | `{ "ts": ISO8601, "level": string, "message": string }` |
| `log_dropped` | `{ "count": int }` — サーバ側キュー溢れで破棄した件数（§12.2） |
| `done` | `{}` |

バッファサイズ上限は §12.2 を参照。

**エラー**: `404`（`task_id` 不明）

**サイドエフェクト**: なし。

---

## 9. Dispatcher 状態機械との対応

Pi Dispatcher（`image_jobs.status`）と Agent 応答の対応関係。詳細は設計書 §Dispatcher 状態機械。

| Pi 状態 | Agent 側の契機 | 典型レスポンス |
|---|---|---|
| `queued` → `dispatching` | なし（Pi 内処理） | — |
| `dispatching` → `warming_cache` | `POST /cache/sync` で `202` | `sync_id` を `image_jobs.cache_sync_id` に記録 |
| `warming_cache` → `running` | `/cache/sync/.../stream` で `status=done` 受信、続いて `POST /image/generate` | `202` + `progress_url` |
| `running` → `done` | `/image/jobs/.../stream` で `result` + `status=done` 受信 | — |
| `running` → `failed` | SSE `error` + `retryable=false`、または `POST /image/generate` で 4xx | Pi が `last_error` 記録 |
| `running` → `queued` (retry) | SSE `error` + `retryable=true`、または接続断 | Pi が `retry_count++`、`next_attempt_at` 設定 |
| 任意 → `cancelled` | `POST /image/jobs/.../cancel` または `POST /cache/sync/.../cancel` を Agent が受理 | `200` |

**冪等性**: 同一 `job_id` / `sync_id` で再投入された場合、Agent は既存ジョブの状態を返し新規実行はしない（`409` は返さず `202` の再掲）。ネットワーク断後の Pi 側再送を安全にするため。

**busy フラグ**: LoRA 学習中は `capability.busy=true`。この状態で `/image/generate` に来た場合は `423 Locked` + `retryable=true` を返し、Pi は `queued` に戻して `next_attempt_at` を延ばす。

---

## 10. エラー分類と Pi 側アクション対応

設計書 §エラーハンドリング・リトライ方針 のクラス階層を API 応答にマップ:

| `error_class` | retryable | 代表 HTTP | Pi アクション |
|---|---|---|---|
| `ValidationError` | false | 400 | 即 `failed` |
| `WorkflowValidationError` | false | 400 | 即 `failed` |
| `CacheSyncError` | true | 500/503 | retry（`warming_cache → queued`） |
| `ResourceUnavailableError` | true | 503 | retry（backoff 長め） |
| `TransientError` | true | 503 | retry（標準 backoff） |
| `AgentCommunicationError` | true | —（接続断検出） | retry |
| `ComfyUIError.OOMError` | true | 500 | **別 Agent へ retry**（同 Agent 再投入禁止） |
| `ComfyUIError`（一般） | false | 500 | 即 `failed`（解像度/プリセット変更を提案） |

`retryable=true` でも `retry_count >= max_retries` なら Pi 側で `failed` 確定。

---

## 11. 参考: リクエスト例

### 11.1 生成フロー最小 curl（Pi 視点）

```bash
# 1) capability 取得
curl -H "X-Agent-Token: $TOKEN" http://main-pc:7777/capability

# 2) 不足モデル同期
curl -X POST -H "X-Agent-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"files":[{"type":"checkpoints","filename":"chenkinNoobXL_v05.safetensors","nas_path":"//nas/secretary-bot/ai-image/models/checkpoints/chenkinNoobXL_v05.safetensors","sha256":"abc..."}]}' \
  http://main-pc:7777/cache/sync

# 3) 同期完了を SSE で待機
curl -N -H "X-Agent-Token: $TOKEN" http://main-pc:7777/cache/sync/sync_01H.../stream

# 4) 画像生成投入
curl -X POST -H "X-Agent-Token: $TOKEN" -H "X-Trace-Id: trace_xxx" -H "Content-Type: application/json" \
  -d @payload.json http://main-pc:7777/image/generate

# 5) 進捗購読
curl -N -H "X-Agent-Token: $TOKEN" http://main-pc:7777/image/jobs/img_01H.../stream
```

---

## 12. 運用パラメータ（確定事項）

初版で「実装時に詰める」としていた項目の確定内容。Phase 番号は `docs/image_gen/todo.md` の区分を指す。

### 12.1 `preview` イベント送出頻度（§4.3）

- **Phase 1**: 送出しない。Agent は ComfyUI WebSocket のバイナリメッセージ（preview PNG）を受信しても破棄する（`windows-agent/tools/image_gen/workflow_runner.py` で `continue` 扱い）。ComfyUI 側の起動オプションは `--preview-method none` を既定とする。
- **Phase 2 以降**（送出有効化時）:
  - ComfyUI は `--preview-method auto`（JPEG, 最大辺 256px）で起動。
  - Agent は **最低 500ms 間隔** でスロットリングし、直近送出から 500ms 未満の preview は捨てる。
  - 1 生成ジョブあたり **最大 10 件** で打ち切り（以降は受信しても転送しない）。
  - `data.b64_png` は base64 後サイズ **128KiB** を超えた場合は送出スキップ（Pi 側 SSE 帯域の保護）。

### 12.2 `/system/logs` の `follow=true` 時のバッファサイズ上限（§8.1）

- Agent 内部の source 別リングバッファ（`collections.deque(maxlen=N)`）は現行実装に合わせて固定:

  | source | maxlen | 実装位置 |
  |---|---|---|
  | `agent` | 500 | agent 共通ロガー |
  | `comfyui` | 500 | `comfyui_manager.ProcessState.log_tail` |
  | `kohya` | 600 | `kohya_train.TrainState.log_tail` |
  | `setup` (task_id 指定時) | 400 | `setup_manager.SetupTask.log_tail` |

- `follow=true` 接続のサーバ側キュー上限は **1000 件 / 接続**。溢れた場合は最古要素を破棄し、代わりに `event: log_dropped` / `data: { "count": <破棄件数> }` を 1 件送出してクライアントに欠落を通知する。
- 初回 snapshot は `lines` クエリ（既定 200、上限 2000）に従い、以降は新規行のみを配信。
- keepalive は他 SSE と同じ **15 秒に 1 回** の `: keepalive` コメント。

### 12.3 `cache/sync` の NAS 並行読み出し本数（§3.2）

- **Phase 1**: 直列（concurrency=1）。`_run_sync_job` は `for f in files` の逐次コピーを維持。
- **Phase 2 以降**: `asyncio.Semaphore(N)` を導入し、既定 **N=2**（1GbE ≒ 125MB/s の下では 2 本で実効スループットを概ね飽和、3 本目以降は温度/断片化リスクの割に利得小）。
- 上書き可能: `config.yaml` の `image_gen.cache.sync_concurrency`（許容値 **1〜4**、範囲外は 2 に丸めて warn ログ）。
- `priority=high` のジョブでも並行度は変えない（NIC 律速のため）。優先度はキュー順のみに反映。
- 1 ファイルの read ブロックサイズは 4MiB（`copy_with_progress(block=4 * 1024 * 1024)` 固定）。

### 12.4 `/image/generate` の `timeout_sec` 未指定時の扱い（§4.1）

- **Pi 側で必ず埋める**を正の運用とする。`workflows.default_timeout_sec`（DDL 既定 300、プリセットごとに設定）を `src/units/image_gen/dispatcher.py` が body に載せて送る。
- Agent は防衛的フォールバックとして、body に `timeout_sec` が無ければ **300 秒** を用いる（`windows-agent/tools/image_gen/router.py` の `int(body.get("timeout_sec") or 300)`）。Pi のバグや旧クライアントへの安全弁として残す。
- 観測上 Agent のフォールバックが発火した場合は `log` イベントに `level=warn, message="timeout_sec not provided by Pi, fallback=300"` を 1 行出す（実装時に追加）。

### 12.5 確定済み（再掲）

- ~~kohya の `sample_prompts` 埋め込み方法~~ → **確定 (2026-04-20)**: Pi が `<NAS>/secretary-bot/ai-image/lora_work/<project>/sample_prompts.txt` を事前生成し、TOML の `sample_prompts` でそのパスを参照する。Agent は TOML をそのまま渡すだけ。

## 13. Phase 4 (LoRA 学習) で追加するエンドポイント

`POST /lora/dataset/tag` と `POST /lora/dataset/sync` は §6/§7 から派生する Phase 4 新設 API。仕様確定は実装時に本書に追記する（暫定方針は `docs/image_gen/todo.md` Phase 4 を参照）。
