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

```
1. WebGUI: プロジェクト作成 → NAS に datasets/{project_name}/ を確保
2. 画像配置後「タグ付け実行」→ WD14 tagger ワークフロー（ComfyUI）を MainPC で起動
3. 結果を lora_dataset_items に保存、WebGUI でキャプション編集
4. 「学習設定生成」→ LLM が枚数・目的からテンプレート TOML を提案、手動調整
5. 「学習開始」→ POST /lora/train/start (MainPC固定)
6. 進捗監視: stdout stream + サンプル画像 + TB メトリクス
7. 完了: .safetensors を NAS の loras/ へ配置 → 次回生成で選択可
```

---

## 初期プリセット（ChenkinNoob-XL-V0.5 向け）

SDXL 派生のため標準的な SDXL 設定を適用。初期は以下 3 つ:

| 名前 | 内容 |
|---|---|
| `t2i_base` | 最小構成（Checkpoint → CLIP Text Encode × 2 → KSampler → VAE Decode → Save）。1024×1024、30 steps、CFG 5.5、Euler a、denoise 1.0 |
| `t2i_hires` | Hires.fix（1024 → 1.5× latent upscale → 2nd KSampler denoise 0.4） |
| `t2i_lora` | LoRA ローダー複数段（最大 3）を挟んだ版 |

※ サンプラー・CFG 等は model card の推奨に合わせて初回セットアップ時に調整。

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

## 未詳細項目（次フェーズ）

- WebGUI 画面遷移・コンポーネント設計
- キャプション編集 UI（タグオートコンプリート、一括置換）
- LoRA 学習設定の推奨値テーブル（キャラ/衣装/画風別）
- Discord スラッシュコマンドの具体仕様
- サンプル生成の頻度・枚数デフォルト
- 実装の着手順・マイルストーン
