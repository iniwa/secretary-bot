# ComfyUI 上級ワークフロー解説

> 基本の txt2img / img2img / hires fix が動くようになったら次のステップ。  
> このドキュメントでは **ControlNet・LoRA・AnimateDiff** の 3 つを解説します。

---

## 目次

1. [ControlNet — 構図・ポーズを制御する](#1-controlnet--構図ポーズを制御する)
2. [LoRA 重ねがけ — スタイルをブレンドする](#2-lora-重ねがけ--スタイルをブレンドする)
3. [AnimateDiff — 静止画からアニメーションを作る](#3-animatediff--静止画からアニメーションを作る)
4. [ワークフロー比較まとめ](#4-ワークフロー比較まとめ)

---

## 1. ControlNet — 構図・ポーズを制御する

### ControlNet とは？

通常のプロンプトだけでは「人物の右手を上げた状態で生成して」のような**空間的・構造的な制御**が難しいです。  
ControlNet は「参照画像（条件画像）」を別途入力することで、**構図・ポーズ・輪郭・奥行きを高精度に制御**できる追加モデルです。

```
[参照画像] → [前処理 (Preprocessor)] → [条件マップ] → ControlNet が生成に反映
```

### 主な ControlNet の種類

| タイプ | 参照する情報 | 用途 |
|---|---|---|
| **OpenPose** | 人体の骨格ポーズ | キャラクターのポーズを固定したい |
| **Canny** | エッジ（輪郭線） | 既存画像の構図を維持しながら絵柄変換 |
| **Depth** | 奥行き（深度マップ） | 3D 的な空間構造を維持 |
| **Lineart** | 線画 | 線画から着色・塗り直し |
| **Tile** | 細部のテクスチャ | アップスケール時の細部強化 |
| **Inpaint** | 塗りつぶし領域 | 部分的な描き直し |

### ノード構成

ControlNet ワークフローには **前処理ノード** と **適用ノード** の 2 段階が必要です。

```
[Load Image (参照画像)]
        ↓
[Preprocessor ノード]  ← ポーズ検出 / エッジ抽出 etc.
        ↓
[条件マップ画像]
        ↓
[Apply ControlNet] ← Positive Conditioning と ControlNet モデルを受け取る
        ↓
  通常の KSampler へ接続
```

**フロー全体像:**

```
[Checkpoint] ──────────────────────────────────────┐
                                                   ↓
[Load Image] → [Preprocessor] → [Apply ControlNet] → [KSampler] → [VAE Decode] → [Save Image]
                                         ↑
                            [Load ControlNet Model]

[CLIP Encode (Positive)] ──────────────────────────┘
[CLIP Encode (Negative)] ──────────────────────────┘
[Empty Latent Image] ──────────────────────────────┘
```

### 必要なファイル

1. **ControlNet モデル** (`.safetensors`)  
   配置場所: `models/controlnet/`  
   Hugging Face の `lllyasviel/ControlNet-v1-1` から入手可能

2. **前処理ノード** (ComfyUI-ControlNet-Aux)  
   ComfyUI Manager からインストール:
   ```
   ComfyUI Manager → Install Custom Nodes → "ControlNet Auxiliary Preprocessors" を検索
   ```

### 使用するノード

| ノード名 | 役割 |
|---|---|
| `LoadImage` | 参照画像を読み込む |
| `DWPreprocessor` / `OpenposePreprocessor` | ポーズ骨格を検出 |
| `CannyEdgePreprocessor` | エッジを抽出 |
| `ControlNetLoader` | ControlNet モデルを読み込む |
| `ControlNetApply` / `ControlNetApplyAdvanced` | Conditioning に ControlNet を適用 |

### パラメータ解説

`ControlNetApply` ノードの主要パラメータ:

| パラメータ | 範囲 | 説明 |
|---|---|---|
| `strength` | 0.0〜2.0 | ControlNet の影響度。1.0 が標準、上げすぎると崩れる |
| `start_percent` | 0.0〜1.0 | 生成の何割目から ControlNet を適用し始めるか |
| `end_percent` | 0.0〜1.0 | 生成の何割目まで ControlNet を適用し続けるか |

> **Tips**: `strength=0.8`、`start=0.0`、`end=0.8` が多くの場合で安定します。  
> ControlNet を最初だけ効かせて後半は自由に生成させると自然な仕上がりになります。

### ControlNet のよくある使い方

**① キャラクターのポーズ固定 (OpenPose)**
1. ポーズ参照画像を用意
2. `DWPreprocessor` でスケルトン抽出
3. `openpose_full.safetensors` を ControlNet として適用
4. プロンプトで服装・背景を指定

**② 写真を別スタイルに変換 (Canny)**
1. 変換元の写真を用意
2. `CannyEdgePreprocessor` でエッジ抽出
3. `control_v11p_sd15_canny.safetensors` を適用
4. プロンプトで目標スタイルを指定 (`anime style`, `oil painting` 等)

---

## 2. LoRA 重ねがけ — スタイルをブレンドする

### LoRA とは？

LoRA (Low-Rank Adaptation) は**ベースモデルに追加する軽量な追加学習データ**です。  
「特定のキャラクター」「特定の画風」「特定のオブジェクト」を学習させた小さなファイル (~10〜200MB) で、  
ベースモデルの出力を方向付けることができます。

単体で使うだけでなく、**複数の LoRA を重ねてスタイルを合成できる**のが強力な機能です。

### ノード構成

LoRA は `CheckpointLoader` と `KSampler` の間に `LoraLoader` ノードを**チェーン接続**します。

```
[Load Checkpoint]
       ↓
[Load LoRA #1 (画風 LoRA)]   ← MODEL と CLIP を受け取り、改変済みを出力
       ↓
[Load LoRA #2 (キャラ LoRA)] ← さらに重ねがけ
       ↓
[Load LoRA #3 (ポーズ LoRA)] ← さらに追加 (任意)
       ↓
[KSampler]
```

各 `LoraLoader` は `MODEL` と `CLIP` の両方を変換して次のノードに渡します。  
`CLIP Text Encode` には最後の `LoraLoader` から出た `CLIP` を接続します。

### フロー全体像

```
[Checkpoint]
    │ MODEL ──→ [LoRA #1] ──→ [LoRA #2] ──→ [KSampler]
    │ CLIP  ──→ [LoRA #1] ──→ [LoRA #2] ──→ [CLIP Encode+] ──→ [KSampler]
    │                                      → [CLIP Encode-] ──→ [KSampler]
    │ VAE   ───────────────────────────────────────────────────→ [VAE Decode]
```

### LoraLoader ノードのパラメータ

| パラメータ | 範囲 | 説明 |
|---|---|---|
| `lora_name` | (ファイル選択) | `models/loras/` 以下のファイルを選択 |
| `strength_model` | 0.0〜2.0 | モデル（画像生成部分）への影響度 |
| `strength_clip` | 0.0〜2.0 | テキスト解釈部分への影響度 |

通常は `strength_model` と `strength_clip` は同じ値にします。

### ブレンド戦略

複数 LoRA を使う場合の強度設定の考え方:

| 戦略 | 設定例 | 効果 |
|---|---|---|
| **均等ブレンド** | LoRA A: 0.7 / LoRA B: 0.7 | 2 つのスタイルを均等に合成 |
| **主従ブレンド** | LoRA A: 1.0 / LoRA B: 0.4 | A を主体に B のエッセンスを加える |
| **アクセント追加** | LoRA A: 1.0 / LoRA B: 0.2 | ほぼ A のスタイル、B は微調整 |

> ⚠️ **注意**: 強度の合計が 2.0 を超えると崩壊しやすい。  
> 3 つ以上使う場合は各強度を 0.4〜0.6 程度に抑えるのが無難。

### プロンプトでの LoRA 指定

LoRA によってはプロンプトに**トリガーワード**を含める必要があります。

```
# LoRA のトリガーワードをプロンプトに含める例
masterpiece, best quality, <lora_trigger_word>, 1girl, solo...
```

Civitai のモデルページに各 LoRA のトリガーワードが記載されています。

### LoRA の入手先

- **Civitai** (https://civitai.com) — 最も豊富。フィルターで LoRA を選択
- **Hugging Face** (https://huggingface.co) — 研究・公式系が多い

配置場所: `models/loras/` に `.safetensors` ファイルを置くと自動認識されます。

---

## 3. AnimateDiff — 静止画からアニメーションを作る

### AnimateDiff とは？

AnimateDiff は**通常の Stable Diffusion モデルに動きの概念を追加するモジュール**です。  
静止画を生成するのと同じフローで、**複数フレームを時間軸方向に一貫性を保ちながら生成**し、GIF または MP4 として出力できます。

> 📹 テキストから直接動画を作る Wan / HunyuanVideo とは異なり、  
> AnimateDiff は既存の SD 1.5 モデルや LoRA をそのまま活用できるのが強みです。

### 必要なもの

1. **AnimateDiff カスタムノード**  
   ```
   ComfyUI Manager → "AnimateDiff Evolved" を検索してインストール
   ```

2. **Motion Module** (AnimateDiff のモーションモデル)  
   配置場所: `models/animatediff_models/`  
   入手先: Hugging Face `guoyww/animatediff`  
   推奨ファイル: `mm_sd_v15_v2.ckpt`

3. **ベースモデル**: SD 1.5 系のチェックポイント  
   ※ AnimateDiff は主に SD 1.5 系で動作。SDXL 対応版 (AnimateDiff-SDXL) は別途必要。

### ノード構成

AnimateDiff は通常の txt2img に **3 つのノードを追加**するだけで使えます。

```
[ADE_AnimateDiffLoaderWithContext]  ← Motion Module を読み込む
                ↓
[ADE_UseEvolvedSampling]            ← KSampler の代わりに使用
                ↓
[VHS_VideoCombine]                  ← フレームを動画に結合して出力
```

### フロー全体像

```
[Checkpoint]
[Motion Module (ADE_AnimateDiffLoaderWithContext)]
       ↓
[CLIP Encode (Positive)]  "walking in the park, smooth motion"
[CLIP Encode (Negative)]
       ↓
[Empty Latent Image]  ← 1フレーム分のサイズを指定
       ↓
[ADE_UseEvolvedSampling]  ← フレーム数・fps をここで設定
       ↓
[VAE Decode]  ← IMAGE バッチ (複数フレーム) が出力される
       ↓
[VHS_VideoCombine]  ← GIF または MP4 に変換して保存
```

### 主要パラメータ

**`ADE_UseEvolvedSampling` / `AnimateDiffSampler` ノード:**

| パラメータ | 推奨値 | 説明 |
|---|---|---|
| `frame_count` | 16〜24 | 生成するフレーム数。多いほど長い動画 (VRAM 大) |
| `motion_scale` | 1.0〜1.5 | モーションの大きさ。上げすぎるとブレが大きくなる |

**`Empty Latent Image` (解像度):**

| 用途 | 推奨解像度 |
|---|---|
| 標準 | 512 x 512 |
| 横長 (16:9) | 768 x 432 |
| 縦長 (9:16) | 432 x 768 |

**`VHS_VideoCombine` ノード:**

| パラメータ | 推奨値 | 説明 |
|---|---|---|
| `frame_rate` | 8〜16 | 再生 fps。8fps でもアニメ調なら十分 |
| `format` | `video/h264-mp4` | MP4 出力。GIF より高品質 |
| `loop_count` | 0 | 0 = 無限ループ |

### プロンプトのコツ

AnimateDiff では**動きを表す言葉**をプロンプトに含めると効果的です。

```
# Good: 動きを示すキーワードを含める
"a girl walking slowly, gentle wind, hair flowing, smooth motion, cinematic"

# Negative にはブレを抑えるワードを追加
"(worst quality:2), blurry, flickering, static, watermark"
```

### VRAM 使用量の目安

| フレーム数 | 解像度 | 必要 VRAM |
|---|---|---|
| 16 frames | 512x512 | ~6 GB |
| 24 frames | 512x512 | ~8 GB |
| 16 frames | 768x512 | ~10 GB |
| 32 frames | 512x512 | ~12 GB |

> 💡 VRAM が足りない場合は `--lowvram` オプションを ComfyUI に追加してください。  
> Portainer の Stack 編集で `CLI_ARGS=--listen 0.0.0.0 --lowvram` に変更。

### AnimateDiff × LoRA の組み合わせ

AnimateDiff は LoRA との組み合わせが特に強力です。

```
[Checkpoint] → [LoRA (画風)] → [LoRA (キャラ)] → [AnimateDiff Sampler]
```

好きな画風 LoRA を適用したまま動画化できます。  
例: アニメ風 LoRA を適用して人物が歩くアニメーション、水彩画風 LoRA で揺れる花のアニメーション、など。

---

## 4. ワークフロー比較まとめ

### 用途別の選び方

| やりたいこと | 使うワークフロー |
|---|---|
| テキストから画像を生成したい | txt2img (基本) |
| 画像のスタイルを変えたい | img2img |
| 高解像度でキレイに出したい | hires fix |
| ポーズ・構図を指定したい | ControlNet (OpenPose / Canny) |
| 特定の画風・キャラを出したい | LoRA |
| 複数スタイルをミックスしたい | LoRA 重ねがけ |
| アニメーション・動画を作りたい | AnimateDiff |
| 静止画を高品質な動画に | SVD / Wan (別モデル) |

### 組み合わせの例

これらは組み合わせて使えます:

```
最強構成例:
txt2img
  + LoRA (画風) × 2
  + ControlNet (OpenPose でポーズ固定)
  + hires fix (2x 高解像度化)
  → AnimateDiff で動画化
```

### VRAM 別の推奨構成

| VRAM | 推奨 |
|---|---|
| 6 GB | txt2img / img2img のみ。512x512 固定 |
| 8 GB | LoRA 重ねがけ・ControlNet・AnimateDiff (16f) |
| 12 GB | hires fix (1024px)・AnimateDiff (24f 768px) |
| 16 GB 以上 | SDXL + LoRA + ControlNet 同時使用が快適 |

---

## 参考リンク

| リソース | URL |
|---|---|
| AnimateDiff Evolved (GitHub) | https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved |
| ControlNet Auxiliary Nodes | https://github.com/Fannovel16/comfyui_controlnet_aux |
| Motion Module ダウンロード | https://huggingface.co/guoyww/animatediff |
| ControlNet モデル | https://huggingface.co/lllyasviel/ControlNet-v1-1 |
| LoRA 配布 (Civitai) | https://civitai.com/models?type=LORA |
| ワークフロー共有 (OpenArt) | https://openart.ai/workflows |
