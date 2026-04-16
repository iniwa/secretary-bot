# ComfyUI 完全ガイド

> **対象読者**: ComfyUI を初めて使う人 / セルフホスト環境で動かしたい人  
> **環境想定**: Docker (Portainer) / NVIDIA GPU  
> **最終更新**: 2025-04

---

## 目次

1. [ComfyUI とは](#1-comfyui-とは)
2. [ComfyUI のメリット](#2-comfyui-のメリット)
3. [画像生成以外にできること](#3-画像生成以外にできること)
4. [必要なスペック](#4-必要なスペック)
5. [セットアップ手順](#5-セットアップ手順)
6. [初回画像生成の手順](#6-初回画像生成の手順)
7. [ノードの基本概念](#7-ノードの基本概念)
8. [おすすめの拡張 (Custom Nodes)](#8-おすすめの拡張-custom-nodes)
9. [トラブルシューティング](#9-トラブルシューティング)

---

## 1. ComfyUI とは

ComfyUI は **ノードベースの AI 画像・動画生成 GUI** です。  
プロンプトを入力するだけのシンプルな UI (Stable Diffusion WebUI 等) とは異なり、  
生成パイプライン全体をノードとワイヤーで「視覚的に組み立てる」ことができます。

```
[テキストプロンプト] → [CLIPエンコード] → [KSampler] → [VAEデコード] → [出力画像]
```

ブラックボックスだった生成プロセスが完全に可視化・カスタマイズ可能になります。

---

## 2. ComfyUI のメリット

| メリット | 説明 |
|---|---|
| **完全な制御** | サンプラー・ステップ数・CFGスケール・VAEを個別に差し替え可能 |
| **ワークフロー再現性** | JSON で保存・共有でき、同じ結果を何度でも再現できる |
| **高速** | 必要なノードだけ実行するため、他の UI より処理が速い傾向あり |
| **拡張性** | カスタムノード (プラグイン) で機能を無限に追加可能 |
| **API 対応** | REST API が内蔵されており、外部ツールや自動化と連携しやすい |
| **無料・OSS** | MIT ライセンス。商用利用も可能 |
| **コンテンツフィルタなし** | ローカル実行のためクラウドサービスの制限を受けない |

> 💡 **他の UI との比較**  
> - Stable Diffusion WebUI (A1111): タブUI、初心者向け。ComfyUI より低速  
> - InvokeAI: ノードあり、使いやすいが機能は限定的  
> - **ComfyUI**: 最も高機能・高速。学習コストは高め

---

## 3. 画像生成以外にできること

ComfyUI は名前に "Image" とありますが、実際には **マルチモーダルな生成プラットフォーム** です。

### 🎬 動画生成
- **AnimateDiff**: 静止画からアニメーション動画を生成
- **Stable Video Diffusion (SVD)**: 画像を動画に変換
- **HunyuanVideo / LTX-Video / Wan**: 高品質テキスト→動画生成
- **WanMove**: モーション制御つき動画生成 (2025年追加)

### 🖼️ 画像編集・加工
- **Inpainting**: 画像の一部を描き直す (消去・追加)
- **Outpainting**: 画像を外側に拡張する
- **img2img**: 既存画像を元にスタイル変換
- **背景除去**: セグメンテーションモデルを使った自動切り抜き
- **アップスケーリング**: ESRGAN 等で解像度を最大 4K/8K に拡大

### 🎛️ 高度な制御
- **ControlNet**: ポーズ・輪郭・深度マップで構図を制御
- **IP-Adapter**: 参照画像のスタイル・顔を別画像に転写
- **LoRA / LyCORIS**: 追加学習モデルを重ねがけ

### 🔊 音声・3D
- **テキスト→音声付き動画**: Kling の TextToVideoWithAudio (2025年対応)
- **3D モデル生成**: Tripo3.0 ノードで 3D オブジェクト生成 (2025年追加)
- **オブジェクト検出**: RT-DETRv4 による物体検出パイプライン

### 🤖 LLM 連携
- **テキスト生成ノード**: Qwen3.5 等の LLM をワークフロー内で実行
- **プロンプト自動生成**: LLM でプロンプトを生成→そのまま画像生成に渡す

### ⚙️ 自動化・API 活用
- **内蔵 REST API**: ワークフローを外部から呼び出して自動生成
- **バッチ処理**: 大量プロンプトを連続実行
- **他ツールとの連携**: n8n や Python スクリプトと組み合わせたパイプライン構築

---

## 4. 必要なスペック

| 項目 | 最小 | 推奨 |
|---|---|---|
| GPU VRAM | 6 GB (NVIDIA) | 12 GB 以上 |
| RAM | 16 GB | 32 GB 以上 |
| ストレージ | 20 GB (OS + ComfyUI) | 100 GB 以上 (モデル保存) |
| GPU | NVIDIA (CUDA) | RTX 3080 / 4070 以上 |

> **AMD GPU**: ROCm 経由で動作可能 (一部機能に制限あり)  
> **Apple Silicon (M1/M2/M3/M4)**: MPS バックエンドで動作可能

---

## 5. セットアップ手順

### 5-1. Docker Compose (Portainer 推奨)

Portainer の **Stacks → Add stack → Web editor** に以下を貼り付けます。

```yaml
services:
  comfyui:
    image: yanwk/comfyui-boot:latest
    container_name: iniwa-comfyui
    restart: unless-stopped
    ports:
      - "8188:8188"
    volumes:
      - ./models:/root/ComfyUI/models
      - ./output:/root/ComfyUI/output
      - ./input:/root/ComfyUI/input
      - ./custom_nodes:/root/ComfyUI/custom_nodes
    environment:
      - CLI_ARGS=--listen 0.0.0.0
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

> **ポイント**  
> - `models/` ボリュームをバインドマウントにするとモデルファイルがホスト側に残る  
> - `--listen 0.0.0.0` でホスト外からアクセス可能になる  
> - Stack 名は `iniwa-comfyui` 推奨 (命名規則に統一)

### 5-2. モデルのダウンロード

ComfyUI だけではモデルが入っていないため、別途ダウンロードが必要です。

**主なモデル配布サイト**

| サイト | URL | 特徴 |
|---|---|---|
| Hugging Face | https://huggingface.co | 公式・研究用モデル多数 |
| Civitai | https://civitai.com | コミュニティ作成モデル豊富 |

**配置場所** (コンテナ内 / バインドマウントした `./models/` 以下)

```
models/
├── checkpoints/   ← メインモデル (.safetensors / .ckpt)
├── loras/         ← LoRA モデル
├── vae/           ← VAE モデル
├── controlnet/    ← ControlNet モデル
├── upscale_models/ ← アップスケーラー
└── embeddings/    ← Textual Inversion
```

**初心者向け推奨モデル**

| 用途 | モデル名 | サイズ |
|---|---|---|
| 汎用 (軽量) | SD 1.5 系 (e.g., `v1-5-pruned.safetensors`) | ~2 GB |
| 高品質 | SDXL Base 1.0 | ~7 GB |
| 最新・高品質 | FLUX.1 (dev / schnell) | ~12–24 GB |

### 5-3. 起動確認

```
http://<ホストIP>:8188
```

ブラウザでアクセスしてノードキャンバスが表示されれば起動成功です。

---

## 6. 初回画像生成の手順

### Step 1: デフォルトワークフローを確認

起動直後のキャンバスには **デフォルトワークフロー** が読み込まれています。  
以下のノードが接続されているはずです:

```
[Load Checkpoint] → [CLIP Text Encode (Positive)] ─┐
                  → [CLIP Text Encode (Negative)] ──┤→ [KSampler] → [VAE Decode] → [Save Image]
                  → [Empty Latent Image] ────────────┘
```

### Step 2: モデルを選択

`Load Checkpoint` ノードのドロップダウンから、  
ダウンロード済みの `.safetensors` ファイルを選択します。

### Step 3: プロンプトを入力

- **Positive (CLIPTextEncode 上)**: 生成したい内容  
  例: `a beautiful sunset over mountains, photorealistic, 8k`
- **Negative (CLIPTextEncode 下)**: 含めたくない内容  
  例: `blurry, low quality, extra limbs, watermark`

### Step 4: 生成パラメータを調整

`KSampler` ノードで以下を設定します:

| パラメータ | 推奨値 | 説明 |
|---|---|---|
| `seed` | -1 (ランダム) | 固定すると同じ結果を再現できる |
| `steps` | 20〜30 | ステップ数。多いほど高品質だが遅い |
| `cfg` | 7.0〜8.0 | プロンプト追従度。高すぎると崩れる |
| `sampler_name` | `dpmpp_2m` | DPM++ 2M Karras が安定しておすすめ |
| `scheduler` | `karras` | |
| `denoise` | 1.0 | img2img 以外は 1.0 固定 |

`Empty Latent Image` ノードで解像度を設定:  
- SD 1.5: `512x512` または `768x512`  
- SDXL: `1024x1024`

### Step 5: 生成実行

画面右上の **「Queue Prompt」** ボタンをクリック。  
右下のキューに追加され、完了すると `Save Image` ノードに結果が表示されます。

### Step 6: 画像を保存

生成画像を右クリック → **Save Image** でダウンロード、  
またはコンテナの `./output/` フォルダに自動保存されています。

---

## 7. ノードの基本概念

| 用語 | 説明 |
|---|---|
| **ノード** | 処理の単位。入力と出力を持つブロック |
| **ワイヤー (接続線)** | ノード間のデータの流れ |
| **Checkpoint** | 画像生成の核となる学習済みモデル |
| **CLIP** | テキストプロンプトを数値ベクトルに変換する部分 |
| **Latent** | VAE で圧縮された画像の中間表現 |
| **KSampler** | ノイズ除去を繰り返し画像を生成するコア処理 |
| **VAE** | Latent を実際の画像ピクセルに変換するモデル |

**ノードの追加方法**: キャンバスを右クリック → `Add Node` から選択  
**ノードの削除**: ノードを選択して `Delete` キー  
**ワークフロー保存**: `Save` ボタン → JSON ファイルとして保存

---

## 8. おすすめの拡張 (Custom Nodes)

**ComfyUI Manager** を最初にインストールするとワンクリックで他の拡張を管理できます。

```bash
# コンテナ内 or バインドマウントした custom_nodes フォルダで
git clone https://github.com/Comfy-Org/ComfyUI-Manager custom_nodes/comfyui-manager
```

| ノード名 | 用途 |
|---|---|
| ComfyUI-Manager | カスタムノードの一括管理 |
| ComfyUI-Impact-Pack | 顔修正・ADetailer 相当 |
| ComfyUI-ControlNet-Aux | ControlNet 前処理 (ポーズ検出等) |
| ComfyUI_IPAdapter_plus | IP-Adapter (スタイル転写) |
| ComfyUI-VideoHelperSuite | 動画入出力・フレーム処理 |
| was-node-suite-comfyui | 汎用ユーティリティ多数 |

---

## 9. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `CUDA out of memory` | VRAM 不足 | 解像度を下げる / `--lowvram` オプションを追加 |
| モデルが認識されない | 配置場所が違う / 拡張子が違う | `models/checkpoints/` に `.safetensors` or `.ckpt` で配置 |
| 生成結果が真っ黒 | VAE が合っていない | モデルに対応した VAE を別途読み込む |
| 顔が崩れる | CFG が高すぎる / 低ステップ | CFG を 6〜7 に下げる / ADetailer を使う |
| 生成が遅い | CPU フォールバック | GPU ドライバ・CUDA バージョンを確認 |

---

## 参考リンク

- [ComfyUI 公式 GitHub](https://github.com/comfyanonymous/ComfyUI)
- [公式ドキュメント](https://docs.comfy.org)
- [Civitai (モデル配布)](https://civitai.com)
- [OpenArt (ワークフロー共有)](https://openart.ai/workflows)
- [ComfyUI Manager](https://github.com/Comfy-Org/ComfyUI-Manager)
