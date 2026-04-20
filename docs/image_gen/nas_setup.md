# NAS 共有ディレクトリ 初期化・運用手順書

secretary-bot の AI 画像生成機能（`docs/image_gen/design.md`）で使う NAS 共有 `ai-image/` の初期化および運用手順。モデル・生成画像・LoRA データセットの**正本**置き場として扱う。

---

## 1. 概要

### 役割
- **正本ファイルの保管**: SDXL 本体、LoRA、VAE、embeddings などモデル類の唯一の正本
- **生成成果物の保存**: 生成画像（`outputs/`）は保持無期限
- **LoRA 学習基盤**: データセット（`lora_datasets/`）と中間成果物（`lora_work/`）の保管場所
- 各 PC は NAS を正として、初回のみローカル SSD にキャッシュする（「案 B」方式）

### 接続前提
| 項目 | 内容 |
|---|---|
| プロトコル | SMB |
| 回線・ストレージ | 1GbE + HDD（SDXL 本体 約 7GB は初回ロード 約 1 分） |
| 接続元 | Raspberry Pi 4 / Main PC / Sub PC すべてから接続可 |
| 共有名 | `ai-image` |

### 参照すべき設計書セクション
- 「ファイル配置・キャッシュ戦略（案 B）」
- 「NAS ディレクトリ構造」
- 「書き込み権限」ルール
- 「ハッシュサイドカー」

---

## 2. 初期ディレクトリ作成

共有 `ai-image/` をマウントしたうえで、下記のツリーを構築する。

```
<NAS 共有>/ai-image/
├── models/
│   ├── checkpoints/
│   ├── loras/
│   ├── vae/
│   ├── embeddings/
│   ├── controlnet/
│   ├── upscale_models/
│   └── clip/
├── outputs/
├── lora_datasets/
├── lora_work/
├── workflows/
└── snapshots/
```

### bash（Raspberry Pi / Linux）

```bash
# マウント済み前提（例: /mnt/ai-image）
BASE=/mnt/ai-image

mkdir -p "$BASE"/models/{checkpoints,loras,vae,embeddings,controlnet,upscale_models,clip}
mkdir -p "$BASE"/outputs
mkdir -p "$BASE"/lora_datasets
mkdir -p "$BASE"/lora_work
mkdir -p "$BASE"/workflows
mkdir -p "$BASE"/snapshots

# 確認
ls -la "$BASE"
```

### PowerShell（Main PC / Sub PC）

```powershell
# マウント済み前提（例: Z: ドライブ）
$Base = "Z:\"

$dirs = @(
  "models\checkpoints", "models\loras", "models\vae",
  "models\embeddings", "models\controlnet", "models\upscale_models", "models\clip",
  "outputs", "lora_datasets", "lora_work", "workflows", "snapshots"
)
foreach ($d in $dirs) {
  New-Item -ItemType Directory -Path (Join-Path $Base $d) -Force | Out-Null
}

# 確認
Get-ChildItem $Base
```

---

## 3. 権限・認証情報

### SMB 共有の推奨設定（NAS 側）

| 対象 | 権限 |
|---|---|
| 全員（読み取り） | `ai-image/` 配下すべて読み取り可 |
| Pi のサービスユーザー | `workflows/`, `lora_datasets/`, `snapshots/` に書き込み |
| Main PC / Sub PC のサービスユーザー | `outputs/`, `lora_work/`, `models/loras/` に書き込み |
| 管理者のみ | `models/checkpoints/`, `models/vae/`, `models/embeddings/` 等への書き込み（モデル配置は管理者操作） |

- ゲストアクセス無効、**ユーザー認証必須**
- 書き込み可能な最小範囲のみ割り当てる（誤操作防止）

### 認証情報の管理

- 各 PC の **`.env`** で管理する（`config.yaml` には書かない）
- GitHub パブリックリポジトリ前提のため、`.env` は必ず `.gitignore` 対象であること

`.env` 追記例（各 PC 共通）:

```dotenv
# NAS (SMB)
NAS_HOST=192.168.1.xx
NAS_SHARE=ai-image
NAS_USER=ai-image-rw
NAS_PASSWORD=********
NAS_MOUNT_POINT=/mnt/ai-image   # Linux 用
NAS_DRIVE_LETTER=Z              # Windows 用
```

---

## 4. マウント手順

### 4.1 Raspberry Pi (Linux, arm64)

#### 要件
```bash
sudo apt update
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/ai-image
```

#### 認証ファイル
`/etc/cifs-credentials-ai-image`（root 所有・600）:

```
username=ai-image-rw
password=********
domain=WORKGROUP
```

```bash
sudo chown root:root /etc/cifs-credentials-ai-image
sudo chmod 600 /etc/cifs-credentials-ai-image
```

#### `/etc/fstab` エントリ例

```fstab
//192.168.1.xx/ai-image  /mnt/ai-image  cifs  credentials=/etc/cifs-credentials-ai-image,iocharset=utf8,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,vers=3.0,nofail,x-systemd.automount,x-systemd.device-timeout=10  0  0
```

- `iocharset=utf8`: 日本語ファイル名の文字化け防止
- `nofail` + `x-systemd.automount`: NAS 未起動でも Pi のブートを止めない
- `vers=3.0`: SMB プロトコル明示

#### 反映・テスト
```bash
sudo systemctl daemon-reload
sudo mount -a
ls /mnt/ai-image
```

### 4.2 Windows Main PC / Sub PC

ドライブレター **`Z:`** を推奨（`extra_model_paths.yaml` 設定やスクリプトでの統一のため）。

#### `net use` 方式（バッチ / 手動）

```bat
net use Z: \\192.168.1.xx\ai-image /user:ai-image-rw <password> /persistent:yes
```

#### PowerShell `New-SmbMapping` 方式（推奨）

```powershell
# 資格情報は対話入力（スクリプト内に平文パスワードを書かない）
$cred = Get-Credential -UserName "ai-image-rw" -Message "NAS (ai-image) credentials"
New-SmbMapping -LocalPath "Z:" -RemotePath "\\192.168.1.xx\ai-image" `
  -UserName $cred.UserName -Password $cred.GetNetworkCredential().Password `
  -Persistent $true

# 確認
Get-SmbMapping
```

#### 自動再接続のサービス化（任意）
Windows Agent 起動前に必ずマウントされている状態にするため、`start_agent.bat` の先頭で `net use` を叩いて冪等に再接続させる構成が安全。

---

## 5. SHA256 サイドカー生成

キャッシュ検証のため、モデル系ファイル（`*.safetensors`, `*.ckpt`, `*.pt`, `*.bin` 等）の横に `<filename>.sha256` を配置する（設計書「ハッシュサイドカー」参照）。

### 書式
ファイル内容はテキスト 1 行で、ハッシュのみ（lowercase hex）:

```
1a2b3c4d5e6f...（64 桁）
```

### 5.1 新規配置時（単発）

#### bash
```bash
# 使い方: ./make_sha256.sh <file>
f="$1"
sha256sum "$f" | awk '{print $1}' > "${f}.sha256"
echo "wrote ${f}.sha256"
```

#### PowerShell
```powershell
# 使い方: .\Make-Sha256.ps1 -Path <file>
param([Parameter(Mandatory=$true)][string]$Path)
$hash = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
[System.IO.File]::WriteAllText("$Path.sha256", $hash)
Write-Host "wrote $Path.sha256"
```

### 5.2 既存ファイルへの一括適用

対象は `models/` 配下のモデル拡張子。既にサイドカーがあればスキップ（再計算コストが高いため）。

#### bash（Pi で一括）
```bash
BASE=/mnt/ai-image/models
find "$BASE" -type f \( -name "*.safetensors" -o -name "*.ckpt" -o -name "*.pt" -o -name "*.bin" \) \
  | while read -r f; do
      if [ -f "${f}.sha256" ]; then
        echo "skip: $f"
        continue
      fi
      echo "hashing: $f"
      tmp="${f}.sha256.tmp"
      sha256sum "$f" | awk '{print $1}' > "$tmp"
      mv "$tmp" "${f}.sha256"   # 原子的書き込み
    done
```

#### PowerShell（Windows で一括）
```powershell
$Base = "Z:\models"
$exts = @("*.safetensors", "*.ckpt", "*.pt", "*.bin")

Get-ChildItem -Path $Base -Recurse -File -Include $exts | ForEach-Object {
  $side = "$($_.FullName).sha256"
  if (Test-Path $side) {
    Write-Host "skip: $($_.FullName)"
    return
  }
  Write-Host "hashing: $($_.FullName)"
  $hash = (Get-FileHash -Algorithm SHA256 -Path $_.FullName).Hash.ToLower()
  $tmp = "$side.tmp"
  [System.IO.File]::WriteAllText($tmp, $hash)
  Move-Item -Force $tmp $side   # 原子的書き込み
}
```

> 注意: 1GbE + HDD では SDXL 本体 1 個のハッシュに数十秒〜1 分かかる。Pi で長時間走らせるなら `nice` / `ionice` 併用。

---

## 6. 保持ポリシーの確認

設計書「保持ポリシー」に準拠。**自動クリーニングは原則行わない**。

| ディレクトリ | ポリシー |
|---|---|
| `outputs/` | **削除しない**（無期限）。`YYYY-MM/YYYY-MM-DD/` で日毎フォルダ分け |
| `lora_work/` | **基本保持**。WebGUI の手動削除ボタンで対応。NAS 容量逼迫時（例: 共有残り < 50GB）のみ最古プロジェクトから自動削除、`models/loras/` に昇格済みのものを優先対象 |
| `lora_datasets/` | 削除しない（明示的な削除操作のみ） |
| `models/loras/` | 削除しない（明示的な削除操作のみ） |
| `models/` 配下その他 | 管理者による明示削除のみ |
| `workflows/` / `snapshots/` | バックアップ扱い、削除は明示操作 |

---

## 7. 動作確認

初期化完了後、各 PC から読み書きテストを行う。

### 7.1 読み取りテスト（全 PC）

#### Linux (Pi)
```bash
ls /mnt/ai-image
stat /mnt/ai-image/models
```

#### Windows (Main/Sub)
```powershell
Get-ChildItem Z:\
Get-ChildItem Z:\models
```

### 7.2 書き込みテスト（役割別）

それぞれの PC で「書き込み権限があるはず」のパスと「無いはず」のパスの両方を叩き、期待通り成功/失敗するかを確認。

#### Pi（書き込み可: `workflows/`, `lora_datasets/`, `snapshots/`）
```bash
echo "test $(date -Is)" > /mnt/ai-image/workflows/_pi_write_test.txt && \
  echo OK || echo NG
rm /mnt/ai-image/workflows/_pi_write_test.txt

# 書き込み不可のはず（models/checkpoints/）
touch /mnt/ai-image/models/checkpoints/_should_fail.txt 2>&1 | head -1
```

#### Main PC / Sub PC（書き込み可: `outputs/`, `lora_work/`, `models/loras/`）
```powershell
"test $(Get-Date -Format o)" | Out-File Z:\outputs\_win_write_test.txt
if ($?) { "OK" } else { "NG" }
Remove-Item Z:\outputs\_win_write_test.txt

# 書き込み不可のはず（models/checkpoints/）
try { "x" | Out-File Z:\models\checkpoints\_should_fail.txt -ErrorAction Stop }
catch { Write-Host "EXPECTED DENY: $($_.Exception.Message)" }
```

### 7.3 クロス可視性テスト
Pi で書いたファイルが Main/Sub から即座に見えるか、逆方向も確認。

```bash
# Pi
echo "hello from pi" > /mnt/ai-image/workflows/_cross_test.txt
```
```powershell
# Main
Get-Content Z:\workflows\_cross_test.txt
```

確認後に削除:
```bash
rm /mnt/ai-image/workflows/_cross_test.txt
```

---

## 8. トラブルシュート

### 8.1 認証失敗（`mount error(13): Permission denied` / `SYSTEM ERROR 1326`）
- `.env` / `/etc/cifs-credentials-ai-image` のユーザー名・パスワードを再確認
- NAS 側で該当ユーザーがロック状態になっていないか
- Windows の資格情報マネージャーに古い情報が残っていないか確認:
  ```powershell
  cmdkey /list
  cmdkey /delete:192.168.1.xx
  ```
- Pi 側で SMB バージョン不一致のときは `vers=3.0` → `vers=2.1` / `vers=3.1.1` を試す

### 8.2 文字化け（日本語ファイル名が `????` 等になる）
- Linux: `/etc/fstab` に `iocharset=utf8` が入っているか
- Windows: 基本 UTF-16 で問題ないが、Pi で作ったファイルが ASCII 以外だと化ける場合は NAS 側のエンコーディング設定（UTF-8）を確認
- スクリプトは英数字・ハイフン・アンダースコアに限定するのが無難（設計書「ファイル名規約」参照）

### 8.3 権限エラー（書き込み可のはずが失敗）
- NAS 側の ACL（Windows Server 型）と共有レベル権限の**両方**を確認（AND で効く）
- Linux マウント時の `file_mode` / `dir_mode` / `uid` / `gid` を確認。Pi のサービスユーザー UID に合わせる
- Windows: ドライブに別資格情報でマッピングされていないか（`Get-SmbMapping` で確認）

### 8.4 マウントが勝手に外れる
- Linux: `/etc/fstab` に `x-systemd.automount` と `nofail` があるか
- Windows: `net use` に `/persistent:yes`、`New-SmbMapping` に `-Persistent $true` を付与
- ネットワーク断→復帰後に再マウントが必要なら、Agent 起動スクリプトで冪等に再接続

### 8.5 書き込み中にクラッシュしてファイルが壊れる
- 設計ルール「**原子的書き込み**（temp → rename）」を遵守。本書のスクリプト例（`*.sha256.tmp` → `mv`）を参照
- ComfyUI / kohya_ss もデフォルトで rename パターンなので、カスタムノードがこれを破壊しないか確認

### 8.6 SHA256 サイドカー一括生成が遅い
- 1GbE + HDD が律速。Pi より MainPC からローカル感覚で回す方が速いケースがある
- 並列化は HDD のシーク競合で逆効果になりがち。**直列で回すのが基本**
- 途中停止してもサイドカー未作成ファイルだけ再実行されるので、長時間スクリプトは `tmux` / タスクスケジューラで

---

## 付録: 関連設計

- 全体像: `docs/image_gen/design.md`
- API 仕様（予定）: `docs/image_gen/api.md`
- `.env` 追加項目: `SECRETARY_BOT_ROOT`, `SECRETARY_BOT_CACHE`, `NAS_HOST` ほか
