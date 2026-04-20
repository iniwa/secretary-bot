# AI 画像生成基盤 設計書

## 概要

Secretary-bot に AI 画像生成機能を追加する。Main/Sub PC の ComfyUI を計算リソースとして活用し、プロンプト・プリセット・ジョブ管理はすべて Pi で一元管理する。

### 思想

- **計算は各 PC、状態は Pi**: GPU を持つ Windows PC は ComfyUI / kohya_ss の実行に徹し、テキスト・プロンプト・ファイル管理はすべて Pi でホストする
- **ステートレスな PC**: 各 PC にはプリセットや設定を持たせない。Pi から都度ワークフロー JSON を配布する
- **NAS を正とするファイル共有**: モデル・LoRA・生成画像・学習データは NAS に配置。各 PC は初回のみローカル SSD にキャッシュ
- **MainPC 優先の同時稼働**: AgentPool の priority 機能で MainPC を第一候補、空いていなければ SubPC へフォールバック

### スコープ

- t2i（text-to-image）生成
- プロンプト会話編集（LLM 補助）
- プリセット（ComfyUI ワークフロー JSON）管理
- LoRA 学習（本体ホスト）
- NAS 保存画像の閲覧（Pi WebGUI）

**非スコープ（別途検討）**: i2i、inpainting、ControlNet、動画生成

### 対象モデル

- 初期: ChenkinNoob-XL-V0.5（SDXL 派生）
- 推奨解像度: 1024×1024、896×1152、1152×896

---

## 全体アーキテクチャ

```
┌─────────── Discord / WebGUI ───────────┐
                   ↓
┌────────────────── Pi (Secretary-bot) ──────────────────┐
│  Units:                                                 │
│    image_gen       — t2i ジョブ受付・ディスパッチ      │
│    prompt_crafter  — LLM 会話でプロンプト編集          │
│    lora_train      — LoRA 学習オーケストレーション     │
│    workflow_mgr    — プリセット登録・検証              │
│    model_sync      — キャッシュ manifest 管理          │
│                                                         │
│  SQLite: ジョブ/プリセット/セッション/LoRAプロジェクト │
│  Job Dispatcher (async worker)                         │
└─────────────────────────────────────────────────────────┘
                   ↓ AgentPool (priority: MainPC > SubPC)
┌─────── Windows Agent (:7777) × 2 ───────┐
│  /capability  /cache/*  /image/*  /lora/*│
│  ComfyUI subprocess (:8188)              │
│  kohya_ss subprocess (MainPC のみ)       │
│  ローカル SSD キャッシュ (LRU)           │
└──────────────────────────────────────────┘
                   ↓ SMB
          ┌──────── NAS ────────┐
          │ models/ loras/ vae/ │
          │ outputs/ datasets/  │
          └─────────────────────┘
```

### 責務分担

| 責務 | 配置 |
|---|---|
| プロンプトテンプレート / ComfyUI ワークフロー / ジョブ履歴 / LoRA 学習設定・進捗メタ | Pi (SQLite) |
| 生成画像 / 学習用データセット / 学習済み LoRA / モデル本体 (`.safetensors`) | NAS |
| ComfyUI 実行環境 / kohya_ss sd-scripts / GPU | Main/Sub PC |

---

## GUI 方針

ComfyUI のノードエディタを Pi に再実装**しない**。役割で画面を分ける:

| 画面 | 用途 | 場所 |
|---|---|---|
| ComfyUI ネイティブ UI | プリセット（ワークフロー）を**作る**とき | 各 PC のブラウザで `http://<PC>:8188` に直接アクセス |
| Secretary-bot WebGUI 画像生成ページ | プリセットを**使う**とき。プロンプト入力・プリセット選択・パラメータ指定・ギャラリー・キュー | Pi（外部から常時アクセス可） |

運用: 普段は Pi の簡易フォームから生成、新しい表現を開拓したいときだけ PC のブラウザで ComfyUI を直接触る。`Save (API Format)` で JSON 書き出し → Pi WebGUI にアップロードで登録。

---

## ファイル配置・キャッシュ戦略（案 B）

NAS を正本とし、各 PC のローカル SSD にキャッシュ。

### 前提

- NAS: SMB、Pi/Main/Sub から接続可、1GbE + HDD
- SDXL 本体 ≒ 7GB: NAS からの初回ロード ≒ 1 分（1GbE HDD 想定）

### 検証

- NAS 側でファイル更新時に SHA256 を `.sha256` サイドカーで書き出し
- 各 PC は mtime + サイズの軽量比較 → 不一致時のみハッシュ検証

### ウォームアップ

- Windows Agent 起動時: 「常用モデル + スター付き LoRA」を事前同期
- ジョブ投入時: Pi が対象 agent の cache manifest を問い合わせ → 未キャッシュなら**先にダウンロード指示 → 完了後 ComfyUI キュー投入**の 2 段

### ストレージ管理

- 各 PC 側は LRU で古いものを削除（容量上限は設定可能、例: 100GB）
- `starred=true` のファイルは保護

### ComfyUI 参照パス

- ComfyUI は**マウント経由ではなくローカル SSD のみを参照**
- `extra_model_paths.yaml` にローカルキャッシュディレクトリを指定

---

## NAS ディレクトリ構造

親共有 `secretary-bot/` 配下の `ai-image/` サブディレクトリ。データ種別ごとにトップレベルを切る。`models/` 配下は ComfyUI 標準配置と同じ命名にして `extra_model_paths.yaml` のマッピングを最小化する。

```
<NAS 共有>/secretary-bot/ai-image/
├── models/                       # 推論・生成に使うモデル類（ComfyUI 配置準拠）
│   ├── checkpoints/              # ベースモデル（SDXL 等）
│   ├── loras/                    # LoRA（学習完成品も含む）
│   │   └── <project_name>/       # 自作 LoRA はプロジェクト単位
│   ├── vae/
│   ├── embeddings/               # Textual Inversion
│   ├── controlnet/               # 将来用（予約）
│   ├── upscale_models/           # ESRGAN など
│   └── clip/                     # SDXL 用 CLIP モデル
│
├── outputs/                      # 生成画像（ユーザー成果物、保持無期限）
│   └── YYYY-MM/YYYY-MM-DD/
│       └── <job_id>_<seed>.png   # PNG メタデータにワークフロー埋め込み
│
├── lora_datasets/                # LoRA 学習の入力
│   └── <project_name>/
│       ├── images/
│       ├── captions/
│       └── meta.json
│
├── lora_work/                    # LoRA 学習の中間成果物（基本保持、肥大化時のみ削除）
│   └── <project_name>/
│       ├── logs/                 # TensorBoard ログ
│       ├── samples/              # 学習中サンプル生成
│       └── checkpoints/          # epoch 途中の .safetensors
│
├── workflows/                    # ComfyUI ワークフロー JSON バックアップ
│   └── <id>_<name>.json
│
└── snapshots/                    # ComfyUI Manager の custom_nodes スナップショット
    └── <date>.json
```

### ルール

- **ハッシュサイドカー**: 各モデルファイルの横に `<filename>.sha256` を配置。キャッシュ検証用
- **LoRA 完成品の昇格**: `lora_work/<project>/checkpoints/` の学習結果を、ユーザー認定後に `models/loras/<project>/` に配置
- **書き込み権限**:
  - Pi: `workflows/`, `lora_datasets/`, `snapshots/` に書き込み
  - Windows Agent: `outputs/`, `lora_work/`, `models/loras/` に書き込み
  - すべて全員読み取り可
- **原子的書き込み**: ComfyUI / kohya とも temp ファイル → rename パターンを遵守

### 保持ポリシー

| ディレクトリ | ポリシー |
|---|---|
| `outputs/` | **削除しない**。日毎フォルダ分け (`YYYY-MM/YYYY-MM-DD/`) で管理 |
| `lora_work/` | **基本保持**。WebGUI に**手動削除ボタン**を設置（プロジェクト単位 / 中間 checkpoint 単位）。NAS 容量逼迫時のみ自動削除（閾値は設定可能、例: 共有残り < 50GB で最古のプロジェクトから削除。完成品 `models/loras/` にあるものが優先対象） |
| `lora_datasets/` | 削除しない（明示的な削除操作のみ） |
| `models/loras/` | 削除しない（明示的な削除操作のみ） |

---

## プリセット（ComfyUI ワークフロー）可搬性

ワークフローは純粋な JSON なので可搬。MainPC で設計 → Pi に登録 → SubPC でも自動で同じ結果。

### 登録フロー

```
MainPC ComfyUI で設計
    ↓ Save (API Format)
Pi WebGUI にアップロード（or 監視フォルダ自動取込）
    ↓ workflow_mgr がノード・モデル依存を抽出
    ↓ workflows テーブルに登録
以降、ジョブ投入時に Pi が JSON を取り出し AgentPool 経由で配布
```

### 依存関係の同期が必要なもの

| 要素 | 同期方法 |
|---|---|
| モデル・LoRA・VAE・embeddings | キャッシュ機構（案 B）。ジョブ投入前に Agent が自動取得 |
| カスタムノード (`custom_nodes/`) | ComfyUI Manager Snapshot で両 PC 揃える + capability 検査で安全網 |
| Python 依存パッケージ | カスタムノード同期時に併せて処理 |

### カスタムノード同期（2 段構え）

1. **ComfyUI Manager Snapshot** — MainPC で snapshot 書き出し → Pi に配置 → SubPC に適用
2. **Capability 検査** — 各 Agent が `GET /capability` で導入済み一覧を返却、Pi がワークフロー要件と照合。不足時は自動で `main_pc_only=true` を付与

---

## DB スキーマ（Pi SQLite）

```sql
-- プリセット（ComfyUI ワークフロー JSON）
workflows(
  id PK, name UNIQUE, description, category,   -- t2i/lora_train/util
  workflow_json TEXT,                           -- API フォーマット JSON
  required_nodes JSON, required_models JSON, required_loras JSON,
  main_pc_only BOOL, starred BOOL,
  created_at, updated_at
)

-- プロンプト断片（保存・再利用）
prompt_templates(
  id PK, name, positive, negative, notes, tags JSON, created_at
)

-- 会話的プロンプト編集セッション
prompt_sessions(
  id PK, user_id, platform,                     -- discord/web
  positive, negative,
  history_json,                                 -- LLM 対話履歴
  base_workflow_id FK, params_json,
  updated_at, expires_at                        -- TTL 7日
)

-- 生成ジョブキュー
image_jobs(
  id UUID PK, user_id, platform,
  workflow_id FK, positive, negative,
  params_json,                                  -- steps/cfg/seed/size/lora
  status,                                       -- queued/warming_cache/running/done/failed/cancelled
  assigned_agent, priority, progress,
  error_message, result_paths JSON,             -- NAS パス配列
  created_at, started_at, finished_at           -- 履歴 30日保持
)

-- LoRA プロジェクト
lora_projects(
  id PK, name, description,
  dataset_path,                                 -- NAS パス
  base_model, config_json,                      -- kohya TOML 元
  status,                                       -- draft/tagging/ready/training/done/failed
  output_path, created_at, updated_at
)

lora_dataset_items(
  id PK, project_id FK, image_path, caption, tags JSON, reviewed_at
)

lora_train_jobs(
  id PK, project_id FK, status, progress,
  tb_logdir, sample_images JSON,
  started_at, finished_at, error_message
)

-- 各 PC のキャッシュ状況
model_cache_manifest(
  agent_id, file_type,                          -- model/lora/vae/embedding
  filename, sha256, size, last_used_at, starred,
  PRIMARY KEY (agent_id, file_type, filename)
)
```

---

## ユニット構成

`src/units/` 配下に新規追加:

| ユニット | DELEGATE | 主責務 |
|---|---|---|
| `image_gen` | — (Pi内 dispatch) | ジョブ受付、プリセット+プロンプト解決、ディスパッチ、進捗集約 |
| `prompt_crafter` | — | LLM 会話でプロンプト育成、セッション永続化、Discord/WebGUI 両対応 |
| `lora_train` | — | LoRA プロジェクト管理、タグ付けワークフロー起動、kohya config 生成、学習ジョブ発行 |
| `workflow_mgr` | — | ComfyUI JSON 受け取り、ノード・モデル依存を抽出、capability 照合 |
| `model_sync` | — | 定期的な capability ポーリング、ジョブ投入前のキャッシュ整合チェック、ウォームアップ指示 |

すべて Pi 内で動作（実際の GPU 処理は Windows Agent 経由で委託）。

---

## Windows Agent API（:7777 拡張）

既存の `X-Agent-Token` 認証を継承。

```
GET  /capability
  → { custom_nodes: [...], models: [...], loras: [...], vaes: [...],
      comfyui_version, has_kohya, gpu_info, busy: bool }

GET  /cache/manifest
  → { models: [{filename, sha256, size, mtime}], loras: [...], ... }

POST /cache/sync
  body: { files: [{type, filename, nas_path, sha256}] }
  → { status: "syncing", progress_url: "/cache/sync/{sync_id}" }

POST /image/generate
  body: { job_id, workflow_json, inputs: {positive, negative, seed, ...} }
  → 即座に 202、進捗は WS または /image/jobs/{job_id} polling

GET  /image/jobs/{job_id}         # 進捗・完了パス
POST /image/jobs/{job_id}/cancel

POST /lora/train/start            # MainPC のみ有効
  body: { job_id, config_toml, dataset_path, output_path }
GET  /lora/train/{job_id}/status  # stdout / sample images / TB metrics
POST /lora/train/{job_id}/cancel

GET  /health                      # 既存
```

### Agent 内部

- 起動時に ComfyUI をサブプロセス起動（`:8188`、`extra_model_paths.yaml` はローカル SSD を指す）
- ComfyUI WebSocket `/ws` を subscribe → 進捗・完了を Pi へ中継
- LRU キャッシュ管理（設定可能上限、`starred` フラグ保護）
- LoRA 学習中は `capability.busy=true` を立てて同 PC の生成受付を停止

---

## 動作フロー

### t2i 生成

```
1. User → "〇〇な女の子を生成" (Discord/WebGUI)
2. image_gen: プリセット名＋プロンプト＋パラメータを受領
   （prompt_crafter セッションがあれば positive/negative を注入）
3. image_gen: image_jobs に status=queued で挿入
4. Dispatcher:
   a. workflow の required_models/loras を抽出
   b. AgentPool から MainPC（priority=1）を候補化、main_pc_only ならここで固定
   c. 選定 agent の cache manifest と照合
   d. 不足あれば POST /cache/sync → 完了待ち（status=warming_cache）
   e. POST /image/generate で workflow JSON を投入（status=running）
5. agent: ComfyUI に queue、進捗を WS で Pi に stream、progress を UPDATE
6. 完了: NAS の outputs/YYYY-MM-DD/ に画像書き出し
   → Pi が result_paths 更新 → Discord reply / WebGUI push
```

### プリセット登録（MainPC で設計 → SubPC にも配布）

```
1. MainPC の ComfyUI でワークフロー設計 → Save (API Format)
2. Pi WebGUI「プリセット管理」に JSON アップロード
3. workflow_mgr:
   a. JSON パース → 使用ノード・モデル・LoRA を抽出
   b. 両 agent の /capability と照合
   c. SubPC に不足があれば main_pc_only=1 を自動付与（警告表示）
   d. workflows テーブルに保存
4. 以降、名前で呼び出すだけで両 PC から利用可
```

### プロンプト会話編集

```
User:  "森の女の子"
  → prompt_crafter: LLM に「SDXL 向けに変換して」と指示
  → positive/negative 生成、prompt_sessions に保存
User:  "猫を足して、夕暮れっぽく"
  → LLM に「現状 + 指示」を渡し差分編集（完全再生成はしない）
User:  "これで生成" or WebGUI「プリセット選んで投入」
  → image_gen に委譲
```

LLM への system prompt は ChenkinNoob-XL 系の好む記法（Danbooru タグ + 自然文ミックス、weight 指定 `(tag:1.2)` 等）に最適化。

### LoRA 学習

**Phase 4 確定設計（2026-04-20）**:

```
1. WebGUI: プロジェクト作成（name = トリガーワード）→ NAS lora_datasets/<name>/ を Pi が確保
2. WebGUI 上で画像を drag-drop → Pi が multipart 受け取り → NAS lora_datasets/<name>/<basename>.png に保存
   → lora_dataset_items に image_path 登録
3. 「タグ付け実行」→ Agent POST /lora/dataset/tag
   → Agent が venv-kohya で kohya 同梱 tag_images_by_wd14_tagger.py を実行
   → 結果 (各画像の tag リスト) を Pi に返却 → lora_dataset_items.tags へ保存
4. WebGUI でタグ編集 → caption = "<trigger>, <tags>" を自動構築
5. 「学習設定生成」→ Pi 側固定 TOML テンプレ（SDXL: dim=8 / alpha=4 / lr=1e-4 / 8ep / batch=1）を WebGUI に表示
   → ユーザーが手動編集
6. 「学習開始」→
   a. Pi が NAS lora_datasets/<name>/<basename>.txt に caption を書き出し
   b. Pi が NAS lora_work/<name>/sample_prompts.txt を書き出し（プロジェクトに登録された 3-5 行のテストプロンプト）
   c. Agent POST /lora/dataset/sync で NAS → ローカル SSD にコピー
   d. Agent POST /lora/train/start で sdxl_train_network.py をサブプロセス起動
   e. capability.busy=true を立て、同 PC の生成ジョブ受付を停止
7. 進捗監視: SSE で log/loss/epoch/sample/done を Pi に転送
   → WebGUI でリアルタイム表示（loss グラフ + sample 画像）
8. 完了: lora_work/<name>/checkpoints/ に複数の .safetensors（epoch 毎）が並ぶ
9. 手動昇格: WebGUI でユーザーがチェックポイントを選択 → 承認
   → Pi が NAS lora_work/.../checkpoints/<file> → models/loras/<name>/<file> にコピー
   → 次回生成で選択可（model_sync の既存ウォームアップで各 PC に配布）
```

**設計判断のメモ**:

- WD14 タグ付けを ComfyUI ノード化せず kohya 同梱スクリプトを直接呼ぶ理由: 学習側 venv (`venv-kohya`) で完結し、ComfyUI custom_nodes の依存を増やさない
- TOML を LLM 生成ではなく固定テンプレで始める理由: Phase 4 の MVP 範囲を絞るため。LLM 補助は Phase 4.1 で検討
- 昇格を手動にする理由: LoRA は試行錯誤が多く、自動採用は事故の元（古いチェックポイントを誤って正本扱い）
- データセットを NAS → ローカル SSD コピーする理由: 学習中の per-step ファイル読み出しが NAS 直読みだと SMB レイテンシで遅くなる

---

## 初期プリセット（ChenkinNoob-XL-V0.5 向け）

### 共通方針

- **API フォーマット JSON** を正とする（ComfyUI の `Save (API Format)` で書き出される形式、ノード ID をキーとする dict 構造）
- **パラメータ可変化**: positive / negative / seed / steps / cfg / width / height / ckpt_name / sampler_name / scheduler / lora_* などを Pi が実行時に差し込むため、プレースホルダは `{{VAR_NAME}}` を使う
- **ノード入力検証**: Pi が JSON をロードする時点で必須プレースホルダが揃っているか検証し、不足なら `ValidationError`
- **ファイル名規約**: `YYYY-MM-DD_HHMMSS_{job_id}_{seed}.png`（日時先頭、SMB/Windows 安全な文字のみ）
- **出力先**: Pi が実行時に `{{OUTPUT_DIR}} = //nas/secretary-bot/ai-image/outputs/YYYY-MM/YYYY-MM-DD/` を解決して差し込む
- **PNG メタデータ**: ComfyUI 既定でワークフロー JSON が PNG に埋め込まれる → ギャラリーの「設定再現」機能で利用
- **サンプラー既定**: Euler a (`euler_ancestral`)、scheduler `normal`。**プリセット定義および実行時パラメータの両方で上書き可能**

### プリセット一覧

| 名前 | 内容 |
|---|---|
| `t2i_base` | 最小構成。1024×1024、30 steps、CFG 5.5、Euler a、denoise 1.0 |
| `t2i_hires` | Hires.fix（1024 → 1.5× latent upscale → 2nd KSampler、15 steps、denoise 0.4） |
| `t2i_lora_1` | LoRA 1 枚適用 |
| `t2i_lora_2` | LoRA 2 枚適用 |
| `t2i_lora_3` | LoRA 3 枚適用 |

**LoRA 枚数は動的ノード除去ではなく、枚数ごとに別プリセット**として管理する（シンプルさ優先）。Hires.fix + LoRA の組み合わせが必要になれば後日 `t2i_hires_lora_N` を追加。

### `t2i_base` ノード構成

```
CheckpointLoaderSimple (ckpt_name={{CKPT}})
    ↓ MODEL, CLIP, VAE
CLIPTextEncode #positive ← CLIP, text={{POSITIVE}}
CLIPTextEncode #negative ← CLIP, text={{NEGATIVE}}
EmptyLatentImage (width={{WIDTH}}, height={{HEIGHT}}, batch_size=1)
KSampler (
    seed={{SEED}}, steps={{STEPS}}, cfg={{CFG}},
    sampler_name={{SAMPLER}}, scheduler={{SCHEDULER}},
    denoise=1.0
)
    ↓ LATENT
VAEDecode ← VAE
    ↓ IMAGE
SaveImage (filename_prefix={{FILENAME_PREFIX}}, output_path={{OUTPUT_DIR}})
```

**可変**: `CKPT`, `POSITIVE`, `NEGATIVE`, `WIDTH`, `HEIGHT`, `SEED`, `STEPS`, `CFG`, `SAMPLER`, `SCHEDULER`, `FILENAME_PREFIX`, `OUTPUT_DIR`

### `t2i_hires` ノード構成

`t2i_base` 構成 + 以下を追加:

```
(t2i_base の KSampler 出力 LATENT)
    ↓
LatentUpscaleBy (scale_by={{HIRES_SCALE}})   # 既定 1.5
KSampler #2 (
    seed={{SEED}}, steps={{HIRES_STEPS}},   # 既定 15
    cfg={{CFG}}, sampler_name={{SAMPLER}}, scheduler={{SCHEDULER}},
    denoise={{HIRES_DENOISE}}                # 既定 0.4
)
VAEDecode → SaveImage
```

### `t2i_lora_N` ノード構成

`t2i_base` の Checkpoint と CLIPTextEncode の間に LoRA ローダーを N 段挟む:

```
CheckpointLoaderSimple
    ↓ MODEL, CLIP, VAE
LoraLoader #1 (lora_name={{LORA_1}}, strength_model={{LORA_1_W}}, strength_clip={{LORA_1_W}})
    ↓ MODEL, CLIP
(N=2 なら LoraLoader #2、N=3 なら #3 まで)
    ↓
CLIPTextEncode 以降は t2i_base と同じ
```

**追加可変**: `LORA_1`, `LORA_1_W`, ..., `LORA_N`, `LORA_N_W`

---

## セキュリティ・運用

- Windows Agent の新エンドポイントは既存の `X-Agent-Token` 認証を継承
- NAS SMB マウント資格情報は各 PC の `.env` に（Pi からは直接操作せず、agent 経由で sync 指示のみ）
- LoRA 学習中は同 PC の `image_jobs` 受付を一時停止（capability に `busy=true` を立てる）
- ジョブ・セッションの TTL:
  - `prompt_sessions`: 7 日
  - `image_jobs` 履歴: 30 日
  - 結果画像は NAS 側で管理（別ポリシー）

---

## 開発体制: エージェントチーム活用を前提とする

本機能は範囲が広く、互いに独立したサブシステムで構成されるため、**Claude Code のエージェントチーム（Team 機能）で並列・分担して進める**ことを標準運用とする。単発の小さな修正以外は Team 化を第一候補にする。

### 並列化しやすい分担例

| チームメンバー | 担当領域 |
|---|---|
| `pi-backend` | Pi 側ユニット（image_gen / prompt_crafter / workflow_mgr / model_sync / lora_train）、SQLite マイグレーション、Dispatcher |
| `win-agent` | Windows Agent の API 拡張、ComfyUI/kohya サブプロセス管理、キャッシュ同期実装 |
| `webgui` | Secretary-bot WebGUI への画面追加（プリセット管理・プロンプト編集・ギャラリー・LoRA プロジェクト UI） |
| `workflow-presets` | ChenkinNoob-XL 向け初期プリセット作成・検証、WD14 tagger ワークフロー整備 |
| `lora-config` | LoRA 学習設定テンプレート、推奨値テーブル、kohya TOML 生成ロジック |

### 運用ルール

- サブシステム境界（Pi SQLite スキーマ、Agent API、NAS ディレクトリ構造）を**先に確定**してから各メンバーに展開する
- Team 間で共有すべき契約（API 仕様・DB スキーマ・ファイルパス規約）は本設計書および後続の詳細設計ドキュメントで管理
- 相互検証が有効な場面（プリセット互換性テスト、キャッシュ同期の結合、学習 → 生成の E2E）は別チームメンバーにレビューを依頼する
- 単純で明確な単発操作（単一ファイルの軽微な修正等）は Team 化せず通常フローで進める

参考: https://code.claude.com/docs/en/agent-teams.md

---

## Windows Agent セットアップ・運用

### ComfyUI / kohya_ss の管理方式

**Agent が subprocess として起動・管理する方式（案 A）** を採用。既存 Windows Agent の思想（ネイティブ Python で一元管理）に整合。

- ComfyUI: Agent 起動時に常駐起動
- kohya_ss: 学習ジョブ到着時のみ都度起動（常駐しない）、**Main/Sub 両 PC に導入**
- 停止・再起動: PID 管理 + graceful shutdown
- ヘルスチェック: `GET http://127.0.0.1:8188/system_stats` を 30 秒周期
- クラッシュ時: 自動再起動最大 3 回、失敗で capability に `unavailable` を立てる
- ログ: Agent が `logs/` にファイル出力、エラー時のみ Pi に転送

### LoRA 学習の PC 割当（両 PC 導入に伴う変更）

- kohya を両 PC に導入するため、学習ジョブも **AgentPool の priority に従い MainPC 優先 → SubPC フォールバック** で動作
- 従来の「MainPC 固定」は廃止。ただし既定は MainPC 優先のまま
- `capability.busy=true` 中（学習中）は同 PC の生成ジョブ受付を停止するポリシーは継続

### インストール先パス（`.env` で変更可能）

PC ごとに SSD 構成が異なるため、すべて環境変数で上書き可能にする。既定値:

```
SECRETARY_BOT_ROOT=C:/secretary-bot           # Agent 本体・ComfyUI・kohya
SECRETARY_BOT_CACHE=C:/secretary-bot-cache    # モデル等のローカルキャッシュ
```

ディレクトリ構造（Agent 起動時に自動生成）:

```
${SECRETARY_BOT_ROOT}/
├── agent.py
├── units/
├── comfyui/                   # ComfyUI リポジトリ clone 先
├── kohya/                     # sd-scripts clone 先（両 PC）
├── venv-comfyui/              # ComfyUI 用 venv
├── venv-kohya/                # kohya 用 venv
└── logs/

${SECRETARY_BOT_CACHE}/
└── models/
    ├── checkpoints/
    ├── loras/
    ├── vae/
    ├── embeddings/
    ├── upscale_models/
    └── clip/
```

### `extra_model_paths.yaml`

Agent が起動時に自動生成し、`${SECRETARY_BOT_CACHE}/models/` を指す:

```yaml
pi_managed:
    base_path: ${SECRETARY_BOT_CACHE}/models/
    checkpoints: checkpoints/
    loras: loras/
    vae: vae/
    embeddings: embeddings/
    upscale_models: upscale_models/
    clip: clip/
```

### 更新ポリシー

- **実行は Pi からの手動トリガーのみ**（WebGUI の「更新」ボタン）
- **Agent 起動時に毎回、更新有無だけをチェック**して `capability.updates_available` に反映
  - `git fetch` + `HEAD` 比較で ComfyUI / kohya / custom_nodes それぞれ確認
  - `pip list --outdated` で Python 依存の更新有無も確認
- WebGUI は capability を参照し、更新ありのときバッジ表示
- 更新フロー:
  1. `POST /comfyui/update` or `/kohya/update` → Agent が該当プロセスを stop
  2. `git pull` + `pip install -r requirements.txt`
  3. `custom_nodes_snapshot` を適用（必要なら）
  4. プロセス再起動、結果を Pi に返却

### Agent 追加エンドポイント

既存の API に加え:

```
POST /comfyui/setup         # 初回セットアップ（git clone + pip install + 初期 custom_nodes）
POST /comfyui/update        # 更新トリガー
POST /comfyui/restart       # 再起動のみ
POST /kohya/setup
POST /kohya/update
GET  /system/logs           # 最近のログ取得（Pi WebGUI のトラブルシュート用）
```

---

## WebGUI 設計

### ページ構成

Secretary-bot WebGUI に以下のページを追加。既存のナビゲーション骨格は踏襲しつつ、画像生成セクションは独自スタイルを許容する。

| パス | 用途 |
|---|---|
| `/image/generate` | 生成フォーム。プリセット選択・プロンプト入力・パラメータ（steps/CFG/seed/size）・LoRA 選択・即投入 |
| `/image/gallery` | NAS `outputs/` 閲覧。日毎フォルダ、プレビュー、PNG メタから設定再現 |
| `/image/jobs` | ジョブキュー・履歴。進捗表示・キャンセル・同条件再投入 |
| `/image/prompts` | プロンプト会話セッション（ミミと対話しながら育てる）、テンプレ管理 |
| `/image/presets` | プリセット管理。ワークフロー JSON アップロード、capability 照合結果、star 付与 |
| `/lora/projects` | LoRA プロジェクト一覧・新規作成 |
| `/lora/projects/<id>` | データセット閲覧・タグ編集・学習設定・進捗表示・成果物管理（手動削除ボタンもここ） |
| `/system/agents` | Agent 稼働状況・capability・更新通知バッジ・手動更新トリガー |

### Discord との機能分担

| 操作 | Discord | WebGUI |
|---|---|---|
| プロンプト会話 → 生成 | ◎ | ◎ |
| プリセット選んで即生成 | ◎（スラッシュコマンド） | ◎ |
| 生成画像プレビュー | ○（埋め込み画像） | ◎（ギャラリー・再現） |
| プリセット登録（JSON アップロード） | ✕ | ◎ |
| LoRA プロジェクト管理・学習 | ✕ | ◎ |
| ジョブキャンセル | ◎ | ◎ |
| Agent 更新トリガー | ✕ | ◎ |

### リアルタイム更新方式

既存 WebGUI の流儀（`src/web/static/js/pages/chat.js` 参照）に合わせる:

| 用途 | 方式 | エンドポイント例 |
|---|---|---|
| ジョブ進捗（個別） | **SSE** | `/api/image/jobs/<id>/stream`（ComfyUI 進捗 WS を Pi が SSE に変換して中継） |
| プロンプト会話のストリーミング応答 | **SSE** | `/api/image/prompt/<session_id>/stream` |
| キャッシュ同期進捗 | **SSE** | `/api/agents/<id>/cache/sync/<sync_id>/stream` |
| ジョブキュー一覧 | ポーリング | `/api/image/jobs`（10 秒間隔） |
| ギャラリー | ポーリング | `/api/image/gallery?date=YYYY-MM-DD`（15 秒間隔） |
| Agent 稼働状況・capability | ポーリング | `/api/agents`（15 秒間隔） |
| LoRA 学習進捗 | **SSE** + ポーリング併用 | 進捗/stdout を SSE、サンプル画像は 30 秒ポーリング |

サーバ側は Starlette の `StreamingResponse` を使って実装（既存の `/api/flow/stream` と同じパターン）。

### デザイン方針

- 既存ナビ・ヘッダー・フッターは踏襲
- 画像生成セクションは新規 CSS を許容（ギャラリーのグリッド・プロンプトエディタ・ノードプレビュー等、既存ページと形が異なるため）
- 独自スタイルは `src/web/static/css/image_gen.css` 等に分離

---

## プロンプト変換 LLM system prompt

### 方針

- **出力は JSON 構造**（positive / negative を明示分離、パーサで扱いやすく）
- **2 モード**: 初回生成 / 差分編集
- **タグ記法**: Danbooru タグ主体 + 自然文補助、強調は `(tag:1.2)` 形式（最大 `1.4`）
- **出力言語**: positive / negative は英語タグ固定、`notes` のみ日本語
- **LLM**: Ollama `qwen3` 優先、Gemini fallback（既存 LLM Router 流用）
- **Temperature**: 0.4（タグ一貫性優先、追加要素の選定に必要な分のクリエイティブ性）

### 初回生成モードの system prompt

```
あなたは SDXL (ChenkinNoob-XL) 用のイラスト生成プロンプト設計者「ミミ」です。
ユーザーが自然言語で指定したイメージを、SDXL 系モデルが最もよく理解する
"Danbooru タグ中心 + 短い自然文" のプロンプトへ変換してください。

## 規則

1. positive / negative を分けて JSON で出力
2. positive は以下の順で並べる:
   a) 品質タグ: masterpiece, best quality, ultra detailed, very aesthetic, newest
   b) 画風タグ: ユーザー指定があればここに（例: anime style, semi-realistic）
   c) 主題: キャラ・人数・構図（例: 1girl, solo, upper body）
   d) 外見: 髪・目・表情・服装（Danbooru タグで）
   e) シーン: 場所・時間・天候・光
   f) 追加演出: ユーザーの文脈から必要なら（cinematic lighting 等）
3. タグは英語の Danbooru 形式を優先。自然文は短いフレーズで補助的に使う。
4. 強調が必要な要素は (tag:1.2) の weight 記法（最大 1.4 まで）
5. negative は ChenkinNoob-XL 向け標準セットをベースに、ユーザーが明示的に避けたい要素を追加
6. ユーザーの意図を歪めない。不明瞭な場合は堅実な解釈を選ぶ
7. 出力は必ず次の JSON 形式のみ（他の文章は書かない）:

{
  "positive": "<タグ列>",
  "negative": "<タグ列>",
  "notes": "<日本語で 1 文、解釈のポイント>"
}

## 標準 negative（ベース）
lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit,
fewer digits, cropped, worst quality, low quality, normal quality,
jpeg artifacts, signature, watermark, username, blurry
```

### 差分編集モードの system prompt

```
あなたは SDXL 用プロンプトの編集者「ミミ」です。既存の positive/negative プロンプトに、
ユーザーの指示に沿った変更を加えてください。

## 規則

1. 既存プロンプトの構造・順序・既存要素を尊重。指示された差分のみ反映（全面書き換え禁止）
2. "追加" 指示: 適切な位置に挿入（品質タグの後、関連要素の近く）
3. "削除" 指示: 該当タグを除去。副次的な依存関係も整理
4. "変更" 指示: 該当要素を置換、weight 調整で済むならそれで
5. 矛盾が生じる場合は新しい指示を優先
6. 出力形式は初回生成と同じ JSON

## 入力フォーマット
- current_positive: 現在の positive
- current_negative: 現在の negative
- instruction: ユーザーの指示（日本語）
```

### User メッセージの組み立て

**初回**:
```json
{"request": "森の中で笑ってる女の子、夕暮れ"}
```

**差分**:
```json
{
  "current_positive": "masterpiece, best quality, ..., 1girl, solo, forest, smile, ...",
  "current_negative": "lowres, bad anatomy, ...",
  "instruction": "猫を足して、光を夕暮れっぽく"
}
```

### パース失敗時の挙動

- JSON パース失敗 → 1 回だけ再試行（`temperature=0.2` に下げて再生成）
- 再試行も失敗 → ユーザーに「うまく変換できなかった、もう一度言い方を変えてほしい」と返し、セッション状態は変更しない

### セッション保持

- `prompt_sessions.history_json` に LLM 対話履歴（user / assistant）を保存
- 履歴は**直近 6 ターンまで**を system prompt 後に注入（コンテキスト肥大化防止）
- 「リセット」で history / positive / negative を全消去

---

## Discord スラッシュコマンド仕様

### 方針

- **コマンド体系は 4 本**: `/gen`, `/prompt`, `/jobs`, `/presets`
- **ephemeral 応答の使い分け**: 状態確認系（一覧・ステータス）は ephemeral、生成結果は public
- **完了通知は固定チャンネル**: `config.yaml` に `image_gen.discord_output_channel_id` を追加し、ジョブ実行元に関わらず生成結果は固定チャンネルへ投稿
- **Discord interaction の 15 分タイムアウト対策**: 初回 `defer()` → 完了時は**固定チャンネルへの新規メッセージ**として follow-up
- **オートコンプリート**: preset 名と lora 名は DB と agent capability から動的補完
- **セッションスコープ**: プロンプトセッションは `user_id` 単位（チャンネル・DM 問わず共有）

### `/gen` — 直接生成

| 引数 | 型 | 必須 | 既定 | 備考 |
|---|---|---|---|---|
| `prompt` | 文字列 | ✓ | — | positive の中身 |
| `preset` | choice | — | `t2i_base` | DB から補完 |
| `negative` | 文字列 | — | 標準セット | 指定時は標準セットに追記 |
| `size` | choice | — | `1024×1024` | `1024×1024` / `896×1152` / `1152×896` |
| `seed` | 整数 | — | ランダム | — |
| `steps` | 整数 | — | preset 既定 | 上書き |
| `cfg` | 小数 | — | preset 既定 | 上書き |
| `lora` | choice | — | — | 指定時は自動で `t2i_lora_1` に切替 |
| `lora_strength` | 小数 | — | `0.8` | lora 指定時のみ、`0.0`〜`1.5` |

**Discord 経由の LoRA は 1 枚のみ対応**。複数枚（2〜3 枚）は WebGUI 専用、Discord 対応は将来の拡張。

**応答フロー**:
1. コマンド受信 → `defer()`（ephemeral ack）
2. ジョブ投入 → ephemeral で「ジョブ投入したよ（ID: `abc12345`）」
3. 進捗 **50%** で固定チャンネルに進捗 follow-up（以降は通知しない）
4. 完了 → 固定チャンネルに public follow-up で画像埋め込み + メタ + `[🔁 再投入]` 等のボタン
5. 失敗 → ephemeral で失敗理由 + `[🔁 リトライ]` ボタン

### `/prompt` — プロンプト会話セッション

| サブコマンド | 引数 | 挙動 |
|---|---|---|
| `/prompt new` | `initial`（任意） | 新規セッション、`initial` があれば即 LLM で初回生成 |
| `/prompt edit` | `instruction`（必須） | 差分編集 |
| `/prompt show` | — | 現 positive/negative/notes を ephemeral 表示 |
| `/prompt reset` | — | セッション消去 |
| `/prompt commit` | `preset` その他 `/gen` と同じ | 現プロンプトでジョブ投入（以降 `/gen` と同じフロー） |

LLM 応答は段階 edit で更新、Discord API レート（概ね 1 メッセージ / 1 秒）に合わせデバウンス。

### `/jobs` — ジョブ管理

| サブコマンド | 引数 | 挙動 |
|---|---|---|
| `/jobs list` | `limit`（既定 10） | 自分のジョブ最近 N 件（ephemeral） |
| `/jobs status` | `job_id` | 指定ジョブ状態（ephemeral） |
| `/jobs cancel` | `job_id` | キャンセル |
| `/jobs redo` | `job_id`、`same_seed`（任意 bool） | 同条件で再投入 |

### `/presets` — 一覧

| コマンド | 引数 | 挙動 |
|---|---|---|
| `/presets` | — | t2i プリセット一覧を ephemeral、starred 優先 |

プリセット登録・削除は WebGUI のみ。

### オートコンプリート

| 対象 | ソース | 絞り込み |
|---|---|---|
| `preset` | `workflows` テーブル | `category='t2i'`、starred 優先 |
| `lora` | 各 Agent `/capability` の union | 両 PC 共通を優先、片方のみは末尾 |

### 応答スタイル

- ミミの口調（`config.yaml` persona 準拠）
- 成功例: 「できたよ！ `1024×1024` で 42 秒」
- エラー例: 「OOM だったみたい、解像度下げて試そっか」「全部埋まってるから、ちょっと待ってね」

### ボタン

- 完了: `[🔁 再投入]` `[🔧 seed 変えて再投入]` `[❌ 削除]`
- 失敗: `[🔁 リトライ]` `[🛠 設定を見直す]`（WebGUI リンク）
- 進捗: `[⏹ キャンセル]`

### 権限

- 既存 Secretary-bot と同様、`config.yaml` の許可リスト準拠
- `/jobs cancel` / `redo` / `/prompt reset` は**他ユーザーのジョブ・セッション操作不可**（`user_id` で絞る）

### 設定追加

`config.yaml`:

```yaml
image_gen:
  discord_output_channel_id: <固定チャンネルID>
  discord_lora_max_slots: 1        # 将来拡張時に引き上げ
```

---

## LoRA 学習推奨値テーブル

### 方針

- **プリセット形式**: 「目的」×「データセット規模」で一覧化、kohya TOML の主要パラメータを決め打ち
- **LLM 補助**: ユーザー入力（「キャラ LoRA で画像 30 枚」等）から最適プリセットを自動選択し、必要に応じて微調整案を提示
- **ベース**: SDXL LoRA 学習の定説（rank 8〜32、AdamW8bit、cosine、warmup 有）を踏襲
- **推奨値は初期値**: 学習結果を見て調整する前提。`lora_config_templates` テーブルに保存し、ユーザーが WebGUI から複製・編集可

### プリセット分類

目的別（3 分類）:

| カテゴリ | 狙い |
|---|---|
| `character` | 特定キャラ（顔・髪型・服装・体型の一貫性） |
| `outfit` | 特定衣装（服・装飾品の再現） |
| `style` | 画風（線・塗り・色彩傾向） |

ポーズ等の別分類が必要になった時点で拡張する（初期は 3 分類で固定）。

データセット規模別（3 段階）:

| 規模 | 枚数 | 備考 |
|---|---|---|
| `small` | 15〜30 枚 | キャラ LoRA の最小ライン |
| `medium` | 30〜80 枚 | 標準 |
| `large` | 80〜300 枚 | 画風 LoRA 向け |

3 × 3 = **9 種の初期テンプレート**を DB 初期データとして migration に含める。

### 推奨値テーブル（初期版）

| カテゴリ × 規模 | rank | alpha | lr (unet) | lr (text) | batch | epochs | total steps 目安 | scheduler |
|---|---|---|---|---|---|---|---|---|
| character × small | 16 | 8 | 1e-4 | 5e-5 | 2 | 10 | ~1500 | cosine |
| character × medium | 16 | 8 | 1e-4 | 5e-5 | 2 | 8 | ~2400 | cosine |
| character × large | 32 | 16 | 1e-4 | 5e-5 | 2 | 6 | ~3600 | cosine |
| outfit × small | 16 | 8 | 1e-4 | 5e-5 | 2 | 12 | ~1800 | cosine |
| outfit × medium | 16 | 8 | 1e-4 | 5e-5 | 2 | 10 | ~3000 | cosine |
| outfit × large | 32 | 16 | 1e-4 | 5e-5 | 2 | 8 | ~4800 | cosine |
| style × small | 32 | 16 | 5e-5 | 2.5e-5 | 2 | 15 | ~2250 | cosine |
| style × medium | 32 | 16 | 5e-5 | 2.5e-5 | 2 | 12 | ~3600 | cosine |
| style × large | 64 | 32 | 5e-5 | 2.5e-5 | 2 | 10 | ~6000 | cosine |

画風は rank 高め・lr 低め、キャラ・衣装は標準的な rank 16 で様子見。

### 全テンプレ共通の既定値（TOML）

```toml
[general]
pretrained_model = "<NAS path to ChenkinNoob-XL-V0.5>"
train_data_dir = "<NAS lora_datasets/<project>/images>"
output_dir = "<NAS lora_work/<project>/checkpoints>"
logging_dir = "<NAS lora_work/<project>/logs>"
sample_output_dir = "<NAS lora_work/<project>/samples>"

[training]
network_module = "networks.lora"
optimizer_type = "AdamW8bit"
mixed_precision = "bf16"
gradient_checkpointing = true
gradient_accumulation_steps = 1
lr_warmup_steps = 100
lr_scheduler = "cosine"
max_data_loader_n_workers = 2
xformers = true
cache_latents = true
cache_text_encoder_outputs = true
min_snr_gamma = 5
noise_offset = 0.0357

[resolution]
resolution = "1024,1024"
enable_bucket = true
bucket_reso_steps = 64
min_bucket_reso = 704
max_bucket_reso = 1408

[save]
save_every_n_epochs = 2
save_model_as = "safetensors"
save_precision = "fp16"

[sample]
sample_every_n_epochs = 1
sample_prompts = "<project meta.json で定義>"
sample_sampler = "euler_a"
```

### LLM 補助（学習設定生成）

Phase 4 の `lora-config` が実装する system prompt（草案）:

```
あなたは SDXL LoRA 学習の設計支援者「ミミ」です。
ユーザーが目的とデータセットの概要を伝えるので、最適なプリセット分類を選定し、
必要なら推奨値を微調整してください。

## 入力
- purpose: character / outfit / style
- image_count: 画像枚数
- notes: ユーザーの補足（例: "角度のバリエーションが多い"、"線画が特殊"）

## 規則
1. purpose × image_count から基本プリセットを決定（規模は small/medium/large を自動判定）
2. notes に応じて rank / lr / epochs を微調整（最大 ±50% まで）
3. 出力は JSON:

{
  "base_preset": "character_small",
  "overrides": { "rank": 16, "epochs": 12 },
  "reasoning": "<日本語で 1〜2 文>",
  "sample_prompts": ["<3件、学習対象を確認できるプロンプト>"]
}
```

### サンプル生成プロンプト（自動提案）

学習中のサンプル生成用プロンプトを LLM に **3 件**自動生成させる:

- `character`: キャラを単独・バストアップ・全身の 3 構図で
- `outfit`: 衣装を別キャラ・別ポーズで着せた構図で
- `style`: 異なる主題 3 種（人物・風景・オブジェクト）で

これにより学習の「キャラ覚えた度」「過学習」を視覚的に把握可能。

### DB テーブル追加

```sql
lora_config_templates(
  id PK,
  category,                          -- character/outfit/style
  size_class,                        -- small/medium/large
  rank INT, alpha INT,
  lr_unet REAL, lr_text REAL,
  batch_size INT, epochs INT,
  scheduler,
  extra_json,                        -- 上記 TOML 共通設定の差分
  is_default BOOL,                   -- 初期 9 テンプレは true、ユーザー作成は false
  created_at, updated_at
)
```

ユーザーは WebGUI から既存テンプレを複製・編集して独自プリセットを作成可能。

---

## 実装マイルストーン

Team で並列開発する前提で Phase を区切る。各 Phase は「ユーザーが実際に触って動く状態」を Definition of Done とする。

### Team 編成（本機能専属）

| Team メンバー | 担当範囲 |
|---|---|
| `pi-backend` | Pi 側ユニット（image_gen / prompt_crafter / workflow_mgr / model_sync / lora_train）、Dispatcher、SQLite マイグレーション |
| `win-agent` | Windows Agent の API 拡張、ComfyUI/kohya サブプロセス管理、キャッシュ同期、両 PC セットアップ |
| `webgui` | WebGUI への画面追加、SSE/ポーリング実装、独自 CSS |
| `workflow-presets` | 初期 ComfyUI ワークフロー JSON 作成、WD14 tagger ワークフロー整備、capability 検査ロジックの検証 |
| `lora-config` | LoRA 学習推奨値テーブル、kohya TOML テンプレート、LLM 補助の system prompt（Phase 4 で稼働開始） |

### Phase 0: 契約と足場（Team 展開前に必須）

Team に分担する前に、インターフェース契約を固定する。ここは本設計書本体と、そこから切り出す API 仕様書で管理。

- [ ] DB マイグレーション SQL 作成（全テーブル定義）
- [ ] Agent API 仕様書を独立ドキュメント化（`docs/image_gen/api.md`）
- [ ] NAS 共有 `secretary-bot/ai-image/` 初期ディレクトリ作成
- [ ] 各 PC の `.env` 追加項目確定（`SECRETARY_BOT_ROOT`, `SECRETARY_BOT_CACHE`, NAS 認証情報）
- [ ] 既定プリセット `t2i_base` の API フォーマット JSON を MainPC 手作業で export → リポジトリに配置

この Phase は分担せず一括で進める（依存が強い）。

### Phase 1: 最小 t2i（Walking Skeleton）

**目標**: 「既定プリセットでプロンプトを投げて MainPC で生成、NAS に保存、WebGUI で閲覧」が一気通貫で動く。

| Team | 実装内容 |
|---|---|
| `win-agent` | Agent: ComfyUI subprocess 起動、`/capability`（最低限）、`/image/generate`、`/cache/sync`、NAS SMB マウント |
| `pi-backend` | Dispatcher、`image_gen` ユニット、`workflow_mgr`（手動投入プリセット 1 個だけ対応）、ジョブ SSE エンドポイント |
| `webgui` | `/image/generate`（最小フォーム）、`/image/jobs`、`/image/gallery`（最小）、独自 CSS 雛形 |
| `workflow-presets` | `t2i_base` の API フォーマット JSON を正式化、MainPC/SubPC で互換性確認 |

**Definition of Done**:
- [ ] MainPC で ComfyUI が Agent 経由で起動、`/capability` が返る
- [ ] WebGUI から「プロンプト入力 → 投入」でジョブがキューに入る
- [ ] 生成画像が NAS `outputs/YYYY-MM/YYYY-MM-DD/` に保存される
- [ ] ギャラリーに最新画像が表示される
- [ ] ジョブ進捗が SSE で WebGUI にリアルタイム反映される

### Phase 2: 複数 PC 分散 + キャッシュ最適化

**目標**: MainPC/SubPC で分散動作、初回キャッシュ同期が自動で走る。

| Team | 実装内容 |
|---|---|
| `win-agent` | `/comfyui/setup` `/comfyui/update` `/kohya/setup` `/kohya/update`、起動時更新チェック、custom_nodes snapshot 適用、LRU キャッシュ |
| `pi-backend` | `model_sync` ユニット、capability ベースルーティング、ウォームアップ事前指示、SubPC フォールバック |
| `webgui` | `/system/agents`（稼働状況・capability・更新バッジ・手動更新ボタン）、キャッシュ同期進捗 SSE |
| `workflow-presets` | SubPC での `t2i_base` 動作確認、capability 差分検証プロセスの確立 |

**Definition of Done**:
- [ ] SubPC でも同じプリセットが動く
- [ ] MainPC が busy なら SubPC に自動フォールバック
- [ ] 未キャッシュのモデルがあれば自動同期してから生成
- [ ] WebGUI の Agent 画面で両 PC の状態・更新有無が見える
- [ ] 「更新」ボタンで ComfyUI / kohya が更新される

### Phase 3: プリセット管理 + プロンプト補助

**目標**: ユーザーが自分でワークフローを登録でき、LLM 会話でプロンプトを作れる。

| Team | 実装内容 |
|---|---|
| `pi-backend` | `prompt_crafter` ユニット、`prompt_sessions` CRUD、Discord スラッシュコマンド実装 |
| `webgui` | `/image/presets`（JSON アップロード、capability 照合結果表示）、`/image/prompts`（会話 UI） |
| `workflow-presets` | `t2i_hires`、`t2i_lora` プリセット作成・検証 |
| `lora-config`（プロンプト担当としても参加） | プロンプト変換 LLM の system prompt 設計（Danbooru 記法、weight 指定、negative 定石） |

**Definition of Done**:
- [ ] WebGUI から JSON アップロードで新規プリセット登録、capability 照合結果表示
- [ ] ミミと会話しながらプロンプトを育て、そのまま生成に流せる
- [ ] Discord スラッシュコマンドで同等操作
- [ ] `t2i_hires` / `t2i_lora` プリセットが両 PC で動作

### Phase 4: LoRA 学習

**目標**: プロジェクト作成 → データセット登録 → タグ付け → 学習 → 成果物利用が一気通貫で回る。

| Team | 実装内容 |
|---|---|
| `win-agent` | kohya subprocess 管理、`/lora/train/*`、TensorBoard ログ転送、busy フラグ |
| `pi-backend` | `lora_train` ユニット、プロジェクト CRUD、WD14 タグ起動、TOML 生成 |
| `webgui` | `/lora/projects` 系全部（一覧・詳細・タグ編集・学習監視・手動削除ボタン） |
| `workflow-presets` | WD14 tagger ワークフロー整備、学習中のサンプル生成ワークフロー |
| `lora-config` | 推奨値テーブル（キャラ/衣装/画風別）、TOML テンプレート、LLM 補助設計生成 |

**Definition of Done**:
- [ ] WebGUI から LoRA プロジェクト作成、画像アップロード
- [ ] WD14 tagger でタグ付け、WebGUI で編集
- [ ] 学習設定生成 → 学習開始 → 進捗・サンプル画像の監視
- [ ] 完成 LoRA を `t2i_lora` で参照して生成成功
- [ ] `lora_work/` の手動削除ボタンが機能

### Phase 5: 堅牢性・運用整備

**目標**: 実運用に耐える堅牢性と監視性を持たせる。

| Team | 実装内容 |
|---|---|
| `pi-backend` | タイムアウト・スタックジョブ検知、リトライポリシー、エラー分類 |
| `win-agent` | ComfyUI/kohya クラッシュ時の自動復旧、ログ集約、容量逼迫時の LRU 発火 |
| `webgui` | エラー表示 UX、ジョブ再投入、ログビューア |
| 全 Team | 運用手順書・トラブルシュートドキュメント |

**Definition of Done**:
- [ ] Agent 不在時のジョブ挙動が定義通り
- [ ] タイムアウト・リトライが機能
- [ ] ログ収集・閲覧可
- [ ] 運用手順書がある

---

### Phase 間の並列化可能性

- Phase 0 は逐次必須（全 Phase の前提）
- Phase 1 は Team 並列だが相互依存が強いので、**先に Phase 0 で API 契約を確定**してから 3 Team 同時着手
- Phase 2 以降は Phase 1 の稼働物に積むため、Phase 1 完了が条件
- Phase 3 と Phase 4 は**ほぼ独立**なので並列着手可（workflow-presets だけ両方で稼働）
- Phase 5 は Phase 1〜4 すべての上に積むため最後

---

## Dispatcher 状態機械

### ステート定義

| 状態 | 意味 | 終端 |
|---|---|---|
| `queued` | 投入済み、Dispatcher 未処理（リトライ待機中もここ） | — |
| `dispatching` | Dispatcher が選定処理中（capability 照合・Agent 選定） | — |
| `warming_cache` | 選定 Agent にキャッシュ同期を指示済み、完了待ち | — |
| `running` | Agent の ComfyUI に投入済み、実行中 | — |
| `done` | 成功完了、`result_paths` 記録済み | ✓ |
| `failed` | リトライ不可の失敗、`last_error` 記録 | ✓ |
| `cancelled` | ユーザーキャンセル | ✓ |

### 遷移図

```
  ┌─────────────── retry (backoff 後) ─────────────────┐
  │                                                    │
  ↓                                                    │
queued → dispatching → warming_cache → running → done  │
             │               │           │             │
             └──── retry可能エラー ──────┤             │
                                         │             │
            いずれの非終端 → cancelled（ユーザー操作）  │
            リトライ超過 or 致命エラー → failed        │
```

### `image_jobs` への追加フィールド

```sql
image_jobs(
  ...既存...
  retry_count INT DEFAULT 0,
  max_retries INT DEFAULT 2,
  last_error TEXT,
  cache_sync_id TEXT,
  next_attempt_at TIMESTAMP,
  dispatcher_lock_at TIMESTAMP,
  timeout_at TIMESTAMP
)
```

`workflows` にも `default_timeout_sec INT` を追加（プリセットごとの running 上限）。

### 遷移ルール

| From → To | 契機 | 副作用 |
|---|---|---|
| `queued → dispatching` | Dispatcher worker が pickup | `dispatcher_lock_at=now`、`timeout_at=now+30s` |
| `dispatching → warming_cache` | 選定 Agent でキャッシュ不足 | `cache_sync_id` 記録、`timeout_at=now+10min` |
| `dispatching → running` | キャッシュ揃い済み、即投入成功 | `assigned_agent`・`started_at=now`、`timeout_at=now+preset.timeout` |
| `dispatching → queued` | 利用可能 Agent ゼロ | `retry_count++`、`next_attempt_at=now+backoff` |
| `warming_cache → running` | Agent から sync 完了通知 | 上記と同様 |
| `warming_cache → queued` | sync 一時エラー | `retry_count++`、`next_attempt_at` 設定 |
| `warming_cache → failed` | sync 致命エラー（NAS に該当ファイル無し等） | `last_error` 記録 |
| `running → done` | Agent から完了通知 + `result_paths` | `finished_at=now` |
| `running → failed` | ComfyUI バリデーション/致命エラー | `last_error` 記録 |
| `running → queued` | Agent 切断・一時エラー | `retry_count++`、`next_attempt_at` 設定 |
| `任意非終端 → cancelled` | ユーザーキャンセル | Agent にも中断指示（sync 中断 or ComfyUI interrupt） |

### 楽観ロック

SQLite 前提、複数 worker の race condition を排除:

```sql
UPDATE image_jobs
   SET status='dispatching',
       dispatcher_lock_at=?,
       timeout_at=datetime('now','+30 seconds')
 WHERE id=? AND status='queued'
   AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now'));
-- 影響行 1 なら取得成功、0 なら他 worker に取られた
```

### Worker 構成（Pi 内 async タスク）

| Worker | 責務 | 頻度 |
|---|---|---|
| `job_dispatcher` | `queued` → `dispatching` → `warming_cache` or `running` | イベント駆動 + 2 秒フォールバックポーリング |
| `cache_sync_monitor` | `warming_cache` 中の Agent 進捗を購読、完了で `running` 遷移 | Agent SSE 購読 |
| `running_monitor` | `running` 中の進捗購読、`progress` 更新、完了で `done` | Agent SSE 購読 |
| `stuck_reaper` | `timeout_at` 経過のジョブを検知、`failed` or リトライ | 30 秒周期 |

### タイムアウト既定値

| 状態 | デフォルト | 備考 |
|---|---|---|
| `queued` | 24 時間 | 超過で `failed` |
| `dispatching` | 30 秒 | 超過はほぼバグ、`failed` |
| `warming_cache` | 10 分 | 7GB × 1GbE HDD を吸収 |
| `running` | プリセット設定（既定 5 分、hires は 15 分、LoRA 学習は別枠） | `workflows.default_timeout_sec` |

### 進捗報告

`running` 中は ComfyUI WebSocket の `progress` を Agent が購読 → Pi へ中継 → `image_jobs.progress` (0-100) を更新 → WebGUI SSE へブロードキャスト。**DB 書き込みは 2 秒に 1 回デバウンス**。

### イベントログ（`image_job_events`）

遷移追跡用に別テーブル。Phase 1 から導入:

```sql
image_job_events(
  id PK,
  job_id FK,
  from_status, to_status,
  agent_id,                     -- nullable
  detail_json,                  -- retry 回数、エラー詳細、所要時間
  occurred_at DEFAULT now
)
```

---

## エラーハンドリング・リトライ方針

### エラークラス階層

既存 `BotError` を継承:

```python
class ImageGenError(BotError): ...             # 画像生成基盤の基底
class ValidationError(ImageGenError): ...      # retry 不可（入力不正・必須モデル欠損）
class ResourceUnavailableError(ImageGenError): ...  # retry 可（Agent 全滅・NAS 切断）
class TransientError(ImageGenError): ...       # retry 可（通信一時エラー）
class CacheSyncError(ImageGenError): ...
class AgentCommunicationError(TransientError): ...
class ComfyUIError(ImageGenError):
    class OOMError(ComfyUIError): ...          # retry 可（一時）
    class WorkflowValidationError(ComfyUIError): ...  # retry 不可
```

**リトライ可否は例外クラスで決まる**: `TransientError` / `ResourceUnavailableError` / `OOMError` / `CacheSyncError` は retry 可。それ以外は即 `failed`。

### リトライポリシー

- **Max retries**: 2（合計 3 試行）
- **Backoff 式**: `min(base * 2^retry_count + jitter, max)`
  - `base = 30s`, `max = 300s`
  - `jitter = ±10% random`（thundering herd 回避）
- **リトライ回数は DB 永続化**（worker 再起動を跨いでも維持）
- **OOM エラー時**: 同じ Agent では再試行せず、別 Agent へルーティング（同じ GPU 容量なら再 OOM 確実）

### サーキットブレーカー統合

既存 `src/circuit_breaker.py` を各 Agent ごとに適用:

- 短時間に連続失敗（既定 5 回 / 60 秒）で Agent を `unavailable` マーク → ルーティング対象から除外
- クールダウン後（既定 3 分）に healthy check → 成功で復帰
- 失敗継続で Agent は WebGUI に「要対応」として表示

### 典型的な失敗シナリオと挙動

| シナリオ | 検出 | 挙動 |
|---|---|---|
| Agent がジョブ実行中にクラッシュ | heartbeat 欠落 | `running → queued`（retry）、2 PC あれば別 Agent へ |
| NAS 切断（sync 中） | Agent の SMB エラー | `warming_cache → queued`（retry）、3 回失敗で `failed` |
| ComfyUI OOM | Agent が OOM 固有エラーを返す | 別 Agent へ retry、両方 OOM なら `failed`（解像度削減を提案） |
| キャッシュファイル破損 | sha256 不一致 | 自動再 sync、NAS 側も壊れてれば `failed` |
| ComfyUI プロセス恒常停止 | Agent watchdog 3 回再起動失敗 | capability に `comfyui_unavailable`、全ジョブを別 Agent へ |
| 両 Agent 不在 | capability ポーリング全滅 | ジョブは `queued` のまま、長めの backoff でリトライ継続 |
| LLM 不在（prompt_crafter） | Ollama / Gemini 双方 down | 会話編集のみ停止、直接プロンプト入力での生成は継続可 |

### 縮退運転（Graceful Degradation）

| 障害 | 縮退挙動 |
|---|---|
| NAS 切断 | 新規ジョブ受付停止（前提が崩れる）、既 `running` のジョブのみ継続（キャッシュ済みモデルで完走可能なら） |
| MainPC 停止 | SubPC に全負荷、WebGUI で通知 |
| 両 PC 停止 | ジョブは `queued` 継続、WebGUI に「Agent 待機中」表示 |
| LLM 停止 | プロンプト会話は機能停止、既存プロンプトでの生成は継続 |

### エラーメッセージ UX

- **ユーザー向け**: ミミの口調で平易な日本語（「OOM だったみたい、解像度下げて試そっか」等）
- **内部ログ**: `trace_id`・`job_id`・`agent_id`・`retry_count`・例外クラス名・traceback（既存構造化ログに JSON 出力）
- **Discord**: エラー埋め込みカード、「再投入」ボタン付き
- **WebGUI**: ジョブ一覧のステータスバッジ、クリックで詳細・再投入

### トレース

`trace_id` を Entry (Discord/WebGUI) → Dispatcher → Agent → ComfyUI まで伝搬。Agent の `/image/generate` リクエストヘッダに `X-Trace-Id` を載せ、Agent 側ログにも記録。

### ログサンプリング

- エラーログは全件
- 進捗イベント（`progress` 更新）は DB 書き込みのみ、ログは 10 秒に 1 回ダイジェスト
- 遷移イベントは `image_job_events` に全件、ログは INFO で全件

---

## 実装フェーズで詰める項目

設計書レベルの検討は完了。以下は各 Team が実装着手時に詰める粒度の項目として列挙:

- **WebGUI コンポーネント詳細**: ページ単位のワイヤーフレーム、React/Vanilla JS の選定（既存流儀に合わせる）、状態管理
- **キャプション編集 UI の具体**: タグオートコンプリート（WD14 語彙）、一括置換、タグクラウド表示
- **ComfyUI カスタムノード初期セット**: Phase 2 の custom_nodes snapshot に含める推奨ノード群（ComfyUI-Manager、rgthree-comfy など定番）の選定
- **`extra_model_paths.yaml` 生成ロジック**: Agent 起動時のテンプレート展開、既存設定との衝突回避
- **Discord Button 実装**: `[🔁 再投入]` 等のカスタム ID 設計・状態引き継ぎ
- **プロンプト履歴の圧縮戦略**: 6 ターン超過時の古い履歴の要約方法（LLM 要約 or 単純切り捨て）
- **画像再現機能**: PNG メタから `prompt_sessions` を復元する UX 詳細
- **LoRA 学習完成品の昇格 UX**: `lora_work → models/loras` の「昇格ボタン」の確認フロー
- **テスト方針**: ユニットテスト対象・結合テスト方針・E2E のモック戦略
