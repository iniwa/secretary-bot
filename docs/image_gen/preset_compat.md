# 画像生成プリセット互換性チェック

> 対象: `src/units/image_gen/presets/t2i_base.json` を起点とした Main/Sub PC 間のプリセット移植性
> 関連: [`design.md`](design.md) §ファイル配置・キャッシュ戦略（案 B）／§プリセット可搬性、[`api.md`](api.md) §2.1 `GET /capability`
> 版: 2026-04-15（初版）

> **注意**: 本書で述べる実機検証（seed 固定による画素比較・サンプラーごとの差分測定など）は **Phase 1 完了後** に実施する。Phase 1 の段階では、本書の目的はプリセット JSON と capability 応答の項目設計、および差分が出た場合の運用フローの合意形成にある。

---

## 1. 目的

Main PC（RTX 4080）と Sub PC（RTX 5060 Ti）は AgentPool の priority で切り替わるため、**同一プリセット・同一パラメータなら同一（または知覚的にほぼ同一）の画像**を得られる前提で Pi Dispatcher が振り分けを行う。本書は、その前提が成立するために両 PC 間で一致していなければならない項目を、`t2i_base.json` を最小共通プリセットとして整理する。

具体的には、以下の観点で両 PC を比較する:

1. `GET /capability` が返す導入済みモデル／カスタムノード／サンプラー／ComfyUI バージョン
2. GPU の CUDA Compute Capability、VRAM、および xFormers 等の任意最適化の有効状態
3. ComfyUI Manager Snapshot の適用状況（custom_nodes の git commit まで一致）

これらのうち 1 件でも差分があれば、そのプリセットは `workflows.main_pc_only = 1` で片方の PC に固定するか、snapshot 同期で揃える。

---

## 2. `t2i_base.json` の capability 要件

`t2i_base` は「ChenkinNoob-XL 単体 + Euler a + normal scheduler」という最小構成であり、ComfyUI 本体の **built-in ノードのみ**で完結する。したがってカスタムノード非依存で、Main/Sub 両 PC で最も移植性が高いプリセットとなる。

### 2.1 必要ノードとビルトイン判定

| ノード（`class_type`） | 由来 | 備考 |
|---|---|---|
| `KSampler` | ComfyUI built-in (`nodes.py`) | seed/steps/cfg/sampler/scheduler を可変化 |
| `CheckpointLoaderSimple` | ComfyUI built-in | MODEL/CLIP/VAE を 1 ファイルから同時ロード |
| `EmptyLatentImage` | ComfyUI built-in | 解像度は 1024×1024 既定、縦横変更可 |
| `CLIPTextEncode` × 2 | ComfyUI built-in | positive/negative |
| `VAEDecode` | ComfyUI built-in | CheckpointLoader 由来の VAE を流用 |
| `SaveImage` | ComfyUI built-in | `filename_prefix` のみ可変、出力ディレクトリは `extra_model_paths.yaml` + Agent 側で解決 |

**必須カスタムノード**: なし。`capability.custom_nodes` の照合対象は空集合であり、ComfyUI Manager Snapshot の差分が存在しても `t2i_base` は影響を受けない。

### 2.2 必須モデル

| 種別 | ファイル名 | 参照ノード |
|---|---|---|
| `checkpoints` | `chenkinNoobXL_v05.safetensors`（ChenkinNoob-XL-V0.5） | `CheckpointLoaderSimple.ckpt_name` |

VAE／テキストエンコーダ（CLIP）は checkpoint に同梱されているため、`vae/`・`clip/` サブディレクトリに追加ファイルを置く必要はない。将来 VAE を外部化する場合（`VAELoader` ノード追加）は §6 の注意点を参照。

### 2.3 サンプラー／スケジューラ対応状況

| 項目 | 既定値 | 備考 |
|---|---|---|
| `sampler_name` | `euler_ancestral`（Euler a） | ComfyUI 内部の `k_diffusion` 実装。全 GPU で動作 |
| `scheduler` | `normal` | `k_diffusion` 標準スケジューラ |

Euler a + normal は ComfyUI 初期搭載から提供されているため、`comfyui_version` が極端に古い（0.1 系未満）でなければ両 PC とも利用可能。`capability.comfyui_version` が `>= 0.3.0` であれば問題にならない。

---

## 3. Main PC / Sub PC capability 差分チェック表

両 PC の構成は意図的にそろえてあるが、ドライバ・ComfyUI バージョン・custom_nodes の更新タイミングで差分が生じる。`GET /capability` 応答を以下の観点で比較する。

| 項目 | Main PC（期待値） | Sub PC（期待値） | 差分時の影響 |
|---|---|---|---|
| `agent_id` | `main-pc` | `sub-pc` | ルーティングのみ、互換性に影響なし |
| `gpu_info.name` | NVIDIA GeForce RTX 4080 | NVIDIA GeForce RTX 5060 Ti | 参考情報 |
| `gpu_info.cuda_compute` | `8.9` | `8.9` | 同一（sm_89）。不一致なら xFormers/TorchCompile の挙動差に注意 |
| `gpu_info.vram_total_mb` | 16384 | 16384 | 同値。Hires.fix や大バッチ時のみ有意 |
| `gpu_info.vram_free_mb` | 実行時値 | 実行時値 | ジョブ直前の動的判断に使用 |
| `comfyui_version` | 最新 stable | 最新 stable | メジャー差分は禁止（KSampler 挙動変更リスク） |
| `custom_nodes[]` | Snapshot 適用済み | Snapshot 適用済み | `t2i_base` は影響なし。他プリセットは §5 参照 |
| `models[]` に `chenkinNoobXL_v05.safetensors` | 有 | 有 | 片方欠損なら先に `/cache/sync` |
| 利用可能サンプラー | Euler a / normal を含む | 同左 | built-in なので差分は基本出ない |
| xFormers 有効 | 任意 | 任意 | 有効時は微小な数値差（目視で同等）が出得る |

> **補足**: Main/Sub とも sm_89 で VRAM 16GB と揃えているため、`t2i_base` レベルでは OOM もサンプラー非対応も実質起きない。差分が露呈し始めるのは Hires.fix や LoRA 複数枚適用時で、それらは別プリセット（`t2i_hires` / `t2i_lora_N`）側の検証に切り出す。

---

## 4. 互換性検証手順

Phase 1 完了後、以下の順で両 PC の整合を確認する。現時点ではプロセス設計のみ確定させる。

1. **capability の一致確認**
   - Pi から `curl -H "X-Agent-Token: $TOKEN" http://main-pc:7777/capability` と `http://sub-pc:7777/capability` を取得し、§3 の表に沿って diff を取る。
   - `comfyui_version` / `models[*].filename` / `custom_nodes[*].commit` の 3 点は厳密一致、`gpu_info.vram_free_mb` のみ動的値として許容。
2. **seed 固定での生成比較**（Phase 1 完了後に実施）
   - 同一 `workflow_json` と同一 `inputs`（seed / steps / cfg / sampler / scheduler / width / height / ckpt_name）で両 PC に `POST /image/generate`。
   - 得られた PNG を SSIM / perceptual hash で比較し、想定閾値（未確定、SSIM ≥ 0.98 目安）を下回る場合は snapshot 同期または `main_pc_only` 化を検討。
   - ComfyUI は決定的生成を保証しないため、完全一致ではなく **知覚的同等**を合格基準とする。
3. **記録**
   - 検証結果は `docs/image_gen/preset_compat.md`（本書）に追記するか、別途 `docs/image_gen/preset_compat_results.md` を設けて日付・版ごとに残す。

---

## 5. 差分が出た場合の対処フロー

### 5.1 `workflows.main_pc_only = 1` へロックする基準

以下のいずれかに該当する場合、当該プリセットは Main PC 固定化する。

- ワークフローが要求する custom_node が Sub PC に未導入で、ComfyUI Manager Snapshot でも自動導入できない（Python 依存が衝突するケース等）
- SSIM 等の知覚的同等性チェックに Sub PC が連続で不合格（閾値 3 回）
- VRAM 不足で Sub PC のみ OOM を再現する（例: Hires.fix の upscale 倍率を下げても復旧しない）
- Main PC のみに導入済みのモデル（NAS 未配置のローカル検証用など）に依存している

ロック解除は (a) 原因解消後に再検証、(b) capability 応答で差分ゼロを再確認、の 2 段で行う。

### 5.2 ComfyUI Manager Snapshot 同期の手順

`design.md` §プリセット可搬性／カスタムノード同期（2 段構え）に従い、以下を運用する。

1. Main PC の ComfyUI Manager で `Save Snapshot` → `<date>.json` を書き出す。
2. Pi が `<NAS>/ai-image/snapshots/<date>.json` を正本として配置（書き込みは Pi 権限）。
3. Sub PC の Agent が WebGUI からの「更新」トリガで snapshot を適用（`POST /comfyui/update` → `custom_nodes_snapshot` 反映）。
4. 適用後、両 PC に `GET /capability` を再送し `custom_nodes[*].commit` の一致を確認する。
5. 不一致が残る場合は該当ノードの GitHub リビジョンを手動指定するか、当該プリセットを `main_pc_only=1` に降格。

---

## 6. 既知の制約

- **VAE 外部化と tile sampling**: `VAELoader` ノードで VAE を外部ファイル化すると、ComfyUI 側の VAE タイリング処理のパラメータ（`tile_size` 等）差異が微小な画素差を生む。checkpoint 同梱 VAE を使う `t2i_base` は影響外だが、将来のバリエーションプリセットでは両 PC の VAE タイリング設定を明示的に揃える必要がある。
- **OS／GPU ドライバ版差**: Windows 11 上の NVIDIA ドライバのバージョン差（例: 550 系 vs 560 系）による cuBLAS/cuDNN の挙動差で、seed 固定でも 1 ビット単位では一致しない場合がある。**知覚的同等**（SSIM 目安 0.98 以上）を合格基準とし、ビット一致は求めない。
- **xFormers の有無**: どちらか一方のみ xFormers が有効だと sampler 内のアテンション実装が変わるため微小差が出る。`capability` 応答に xFormers 有効フラグを含めるかは Phase 2 で決めるが、当面は両 PC で有効状態を一致させる運用とする。
- **ComfyUI 本体のマイナーバージョン差**: KSampler 内のデフォルト挙動（eta の扱いなど）が変更される可能性があるため、`comfyui_version` は両 PC で同じ git tag に合わせる。片側のみ先行更新した場合は一時的に `main_pc_only=1` で隔離する。
- **sm_89 以外の GPU を追加する場合**: 将来 Sub PC を差し替えて CUDA Compute Capability が変わった場合は本書の差分チェック表を改訂し、再度 §4 の検証を実施する。
