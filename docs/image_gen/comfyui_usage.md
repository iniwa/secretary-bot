# ComfyUI 使い方ガイド

Secretary-bot の画像生成は **ComfyUI** をバックエンドに採用している。本ドキュメントは：

1. ベースとなる ComfyUI 単体の使い方
2. Secretary-bot の WebGUI（`/image-gen/`）経由での使い方
3. 両者をどう組み合わせるか

を一通りまとめる。

---

## 1. 前提

- ComfyUI はローカル SSD にインストール済（`C:/secretary-bot/comfyui/`）
- モデル・LoRA は NAS を正とし、各 PC のキャッシュ `C:/secretary-bot-cache/models/` に必要なときだけ同期される
- 起動・停止は基本的に **Windows Agent が管理**する（WebGUI の起動/停止ボタンから制御）
- 外部公開は Cloudflare Tunnel + Cloudflare Access 経由
  - MainPC: `https://comfyui-main.iniwach.com/`
  - SubPC: `https://comfyui-sub.iniwach.com/`

---

## 2. ベース ComfyUI（単体）の使い方

### 2.1 起動・停止

通常は **Secretary-bot WebGUI の起動/停止ボタン**から操作する（§3.2）。Agent 抜きで手動起動したい場合：

```powershell
# MainPC / SubPC どちらでも同じ
cd C:\secretary-bot\comfyui
..\venv-comfyui\Scripts\python.exe main.py --listen 0.0.0.0 --port 8188 --extra-model-paths-config extra_model_paths.yaml
```

終了は `Ctrl+C`。

### 2.2 UI の基本

アクセス：`https://comfyui-main.iniwach.com/`（Cloudflare Access のログイン → ブラウザへ）

ComfyUI は **ノードベース** のインターフェース：

- **左ドラッグ**: ノード移動 / キャンバスパン（背景）
- **ホイール**: ズーム
- **右クリック（背景）**: ノード追加メニュー
- **右クリック（ノード）**: ノード操作（複製 / 無効化 / 削除）
- **左上メニュー**: Queue Prompt（生成実行）・Save・Load・Refresh など

### 2.3 典型的な t2i ワークフローの要点ノード

| ノード | 役割 |
|---|---|
| `Load Checkpoint` | ベースモデルの読み込み |
| `CLIP Text Encode (Prompt)` | プロンプトを embedding に変換（positive / negative の 2 つ） |
| `Empty Latent Image` | 生成する解像度の latent 初期化 |
| `KSampler` | 実際のサンプリング（seed / steps / cfg / sampler / scheduler） |
| `VAE Decode` | latent → 画像 |
| `Save Image` | 出力を保存 |

### 2.4 Workflow の保存・読み込み

- **Save (.json)**: 現在のグラフを ComfyUI 形式 JSON としてダウンロード
- **Save (API Format)**: Secretary-bot から叩く用の **API 形式**。Workflow 登録は **こちらが必須**
- **Load**: JSON を読み込む。画像ファイル（PNG）に埋め込まれた workflow も復元可能

### 2.5 モデル / LoRA / VAE の配置

NAS 側（正）に配置：

| 種類 | パス |
|---|---|
| Checkpoint | `\\iniwaNAS\secretary-bot\ai-image\models\checkpoints\` |
| LoRA | `\\iniwaNAS\secretary-bot\ai-image\models\loras\` |
| VAE | `\\iniwaNAS\secretary-bot\ai-image\models\vae\` |
| Embedding | `\\iniwaNAS\secretary-bot\ai-image\models\embeddings\` |

各 PC 側は `C:/secretary-bot-cache/models/` に同期される。**ComfyUI の `extra_model_paths.yaml` がキャッシュ側を参照する設定なので、ローカルにファイルがあればそのまま拾われる。**

Secretary-bot 経由でジョブを投入すると、未キャッシュのモデルは自動で NAS から同期される（cache_sync）。ComfyUI 単体運用で未キャッシュのモデルを使いたいときは、該当ファイルをキャッシュディレクトリに手動コピーすればよい。

---

## 3. Secretary-bot WebGUI 経由の使い方

### 3.1 アクセス

```
https://secretary.iniwach.com/image-gen/
```

Basic 認証を通過するとページが開く。画面は 3 セクション：

- **Generate**: プロンプト入力とジョブ投入
- **Jobs**: ジョブの進捗・キャンセル
- **Gallery**: NAS 保存の生成画像閲覧

### 3.2 ComfyUI コントロールパネル（Generate カード上部）

2 行（メインPC / サブPC）表示され、各行に：

| 要素 | 意味 |
|---|---|
| ● 緑 | 稼働中（available） |
| ● 黄 | 起動中・応答待ち |
| ● 灰 | 停止 |
| ● 赤 | Agent 応答なし |
| PID | ComfyUI プロセス ID |
| **起動 / 停止** | ボタンでプロセス制御 |
| **開く** | Cloudflare 公開 URL を新規タブで開く |

- 自動更新は 15 秒周期
- **起動ボタンは必須ではない**。画像生成ジョブを投入した時点で Agent が自動起動する。事前に起動させておくと初回ジョブの待ち時間が消える
- **明示的な停止ボタン**はアイドル自動停止機構がないため、VRAM を解放したいときに使う

### 3.3 画像を生成する

1. **Workflow** を選択（登録済みの API 形式 JSON から選ぶ）
2. **Positive prompt / Negative prompt** を入力
3. **Width / Height / Steps / CFG / Seed / Sampler / Scheduler** を必要なら上書き（空欄なら workflow の既定値）
4. **投入** ボタンでキューに入る

投入後の流れ：

```
queued → warming_cache → dispatching → running → done
```

- `warming_cache`: 該当 PC のキャッシュに足りないモデルを NAS からコピー
- `dispatching`: ComfyUI 未起動なら自動起動、プロンプト送信
- `running`: ComfyUI 側で生成中
- `done`: NAS に保存、Gallery に反映

### 3.4 Jobs セクション

- 各ジョブのステータス・進行・消費時間が一覧
- 実行中のジョブはキャンセル可能
- 失敗したジョブはエラー詳細を展開表示

### 3.5 Gallery セクション

- NAS の `outputs/YYYY-MM/YYYY-MM-DD/` 配下を自動列挙
- サムネイルクリックで拡大
- 30 秒周期で自動更新

---

## 4. ComfyUI と Secretary-bot の組み合わせ方

### 4.1 新しい Workflow を追加する流れ

1. ComfyUI で直接 Workflow を組む
2. `Save (API Format)` で JSON をダウンロード
3. Secretary-bot の Workflow 登録機能に取り込む（詳細は `docs/image_gen/api.md`）
4. 登録後、`/image-gen/` の Workflow プルダウンに現れる

**API 形式** でなければ Secretary-bot からは叩けない点に注意。

### 4.2 ComfyUI 側でパラメータを試行錯誤 → Secretary-bot で量産

典型的な運用：

1. **ComfyUI WebGUI（開くボタン）** で構図・プロンプト・CFG・サンプラーを対話的に試す
2. 決まった構成を `Save (API Format)` し Secretary-bot に登録
3. あとは `/image-gen/` からプロンプトだけ差し替えて量産

Secretary-bot 側は「確定したワークフローを日常的に叩く」用途に最適化されている。

### 4.3 モデル追加の流れ

1. NAS の `secretary-bot/ai-image/models/<種類>/` に配置
2. Secretary-bot 経由でジョブを投入すると `warming_cache` で自動同期
3. ComfyUI 単体運用で急ぎたい場合は `C:/secretary-bot-cache/models/<種類>/` にも手動コピー

### 4.4 いつ ComfyUI WebGUI を開くか

- **ノード構成を編集したいとき**: Secretary-bot は Workflow を選んで叩くだけなのでノード編集は ComfyUI 側で
- **途中経過を可視化したいとき**: ComfyUI は KSampler の preview を出してくれる
- **モデルの一覧を確認したいとき**: `Load Checkpoint` ノードのプルダウンが同期済みモデル一覧になる

日常的なプロンプト差し替えだけなら WebGUI `/image-gen/` の方が早い。

---

## 5. トラブルシューティング

### 起動ボタンを押しても「起動中」のまま

- `recent_logs` を Agent の `/comfyui/status` で確認（WebGUI の停止中起動ボタン直後なら、Agent 側の取り込みが遅れていることも）
- `comfyui.db` のロック競合：前プロセスが残存。**停止 → 起動** で解消することが多い

### 画像が生成されない / `warming_cache` で止まる

- NAS マウント（`N:\` / `/mnt/nas/...`）が外れていないか
- 該当モデルが NAS に存在するか
- `docs/image_gen/setup/verify.md` のチェックリストを実施

### 「開く」リンクが 502 になる

- Cloudflare Tunnel が Pi で動いているか（`ssh iniwapi "systemctl status cloudflared"`）
- 該当 Public Hostname が Zero Trust で登録されているか
- Cloudflare Access の認可ポリシーで自分のメールが許可されているか

### VRAM が解放されない

- Secretary-bot に自動停止機構はない。**停止ボタン**で明示的に切る
- 別 PC（例: MainPC で ComfyUI 起動中なので SubPC で作業したい）に切り替える場合、MainPC 側を停止すると低優先タスクが SubPC に回る

---

## 関連ドキュメント

- `docs/image_gen/design.md` — 画像生成基盤全体の設計
- `docs/image_gen/api.md` — Pi ↔ Windows Agent の API 仕様
- `docs/image_gen/nas_setup.md` — NAS 配置・命名規則
- `docs/image_gen/setup/README.md` — 初期セットアップ手順
- `docs/image_gen/setup/mainpc.md` / `subpc.md` / `pi.md` — 各 PC ごとのセットアップ
- `docs/image_gen/setup/verify.md` — 動作確認チェックリスト
