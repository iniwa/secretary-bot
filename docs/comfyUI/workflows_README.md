# ComfyUI ワークフロープリセット集

ComfyUI に**ドラッグ＆ドロップするだけ**で読み込めるワークフロー JSON のセットです。

---

## ファイル一覧

| ファイル | 用途 | 難易度 |
|---|---|---|
| `workflow_txt2img_basic.json` | テキスト→画像 (基本) | ⭐ 入門 |
| `workflow_img2img.json` | 画像→画像変換 (スタイル転換等) | ⭐⭐ 初級 |
| `workflow_hires_fix.json` | 高解像度 Fix (512→1024px) | ⭐⭐⭐ 中級 |

---

## 使い方

1. ComfyUI をブラウザで開く (`http://<ホストIP>:8188`)
2. JSON ファイルをキャンバスに**ドラッグ＆ドロップ**
3. `Load Checkpoint` ノードでモデルを選択
4. プロンプトを編集して **「Queue Prompt」** をクリック

---

## 各ワークフローの詳細

### 1. `workflow_txt2img_basic.json` — テキスト→画像 (基本)

最もシンプルな構成。ComfyUI を初めて使うときに最適。

```
[Checkpoint] → [CLIP Encode+] ─┐
             → [CLIP Encode-] ──┤→ [KSampler] → [VAE Decode] → [Save Image]
             → [Empty Latent] ──┘
```

**変更ポイント:**
- `Positive Prompt` ノード: 生成したい内容を入力
- `Negative Prompt` ノード: 含めたくない要素を入力
- `EmptyLatentImage`: 解像度を変更 (SD1.5 は 512x512 推奨)
- `KSampler` の `seed` を固定すると同じ画像を再現可能

---

### 2. `workflow_img2img.json` — 画像→画像変換

既存の画像をベースにスタイル変換や改変を行うワークフロー。

```
[Load Image] → [VAE Encode] ─┐
[Checkpoint] → [CLIP Encode+]─┤→ [KSampler (denoise=0.65)] → [VAE Decode] → [Save Image]
             → [CLIP Encode-]─┘
```

**重要パラメータ — KSampler の `denoise`:**

| denoise 値 | 効果 |
|---|---|
| `0.3〜0.5` | 元画像をほぼ維持、細部だけ変化 |
| `0.5〜0.7` | バランス良く変換 (デフォルト: 0.65) |
| `0.8〜1.0` | 元画像から大きく離れる |

**使い方:**
1. `Load Image` ノードに変換したい画像をアップロード
2. プロンプトで目標スタイルを指定 (例: `oil painting style`)
3. `denoise` を好みで調整して実行

---

### 3. `workflow_hires_fix.json` — 高解像度 Fix

小さい解像度で素早く生成してから 2x に拡大、さらに細部を描き直す 2 パス構成。
高品質な最終出力が必要な場合に使用。

```
Phase 1: [512x512 生成] → [Save Image (before_hires)]
                        ↓
Phase 2: [x2 Upscale] → [VAE Encode] → [KSampler (denoise=0.5)] → [Save Image (hires_fix)]
```

**なぜ 2 パスにするの？**
- 1024x1024 を直接生成すると構図が崩れやすい
- 512 で構図を確定させてから拡大すると安定した高品質画像が得られる
- VRAM 使用量を抑えながら高解像度を実現できる

**調整ポイント:**
- `ImageScaleBy` の倍率: デフォルト 2x (変更可能)
- `KSampler (Hires fix)` の `denoise`: `0.4〜0.6` が推奨範囲

---

## カスタマイズのヒント

### プロンプトのコツ (SD 1.5 系)

**Positive:**
```
masterpiece, best quality, ultra detailed, (具体的な描写)
```

**Negative (汎用):**
```
(worst quality:2), (low quality:2), blurry, watermark, text,
extra limbs, bad anatomy, deformed, ugly, duplicate
```

### KSampler パラメータ早見表

| パラメータ | 推奨値 | 備考 |
|---|---|---|
| steps | 20〜25 | 速さと品質のバランス |
| cfg | 6〜8 | 高いほどプロンプト追従、崩れやすい |
| sampler | dpmpp_2m | 安定・高品質 |
| scheduler | karras | dpmpp 系と相性良好 |

---

## モデルの差し替え

各ワークフローの `Load Checkpoint` ノードのドロップダウンからモデルを選択できます。
`models/checkpoints/` に `.safetensors` ファイルを配置すると自動で認識されます。

| モデル系統 | 解像度 | 特徴 |
|---|---|---|
| SD 1.5 系 | 512x512 | 軽量・高速・LoRA 豊富 |
| SDXL 系 | 1024x1024 | 高品質・細部が鮮明 |
| Flux 系 | 1024x1024 | 最新・テキスト描画が得意 |
