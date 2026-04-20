# NAS 共有ディレクトリ 初期化・運用手順書

secretary-bot が使う NAS 共有 `secretary-bot/` の初期化および運用手順。画像生成機能（`ai-image/`）と配信切り抜き機能（`auto-kirinuki/`）の共通基盤として扱う。モデル・生成画像・LoRA データセット・切り抜き中間成果物の**正本**置き場。

> 旧 `ai-image` 共有からの移行手順は `docs/auto_kirinuki/nas_migration.md` を参照。

---

## 1. 概要

### 役割
- **正本ファイルの保管**: SDXL 本体、LoRA、VAE、embeddings などモデル類の唯一の正本
- **生成成果物の保存**: 生成画像（`ai-image/outputs/`）は保持無期限
- **LoRA 学習基盤**: データセット（`ai-image/lora_datasets/`）と中間成果物（`ai-image/lora_work/`）の保管場所
- **切り抜き成果物の保存**: Whisper 文字起こし・ハイライト・EDL・MP4（`auto-kirinuki/outputs/`）
- **Whisper モデル正本**: `auto-kirinuki/models/whisper/`。各 PC は NAS を正として、初回のみローカル SSD にキャッシュする

### 接続前提
| 項目 | 内容 |
|---|---|
| プロトコル | SMB |
| 回線・ストレージ | 1GbE + HDD（SDXL 本体 約 7GB は初回ロード 約 1 分） |
| 接続元 | Raspberry Pi 4 / Main PC / Sub PC すべてから接続可 |
| 共有名 | `secretary-bot` |

### 参照すべき設計書セクション
- `docs/image_gen/design.md` の「ファイル配置・キャッシュ戦略（案 B）」「書き込み権限」「ハッシュサイドカー」
- `docs/auto_kirinuki/design.md` の「NAS ディレクトリ再編」

---

## 2. 初期ディレクトリ作成

共有 `secretary-bot/` をマウントしたうえで、下記のツリーを構築する。

```
<NAS 共有>/secretary-bot/
├── ai-image/
│   ├── models/
│   │   ├── checkpoints/
│   │   ├── loras/
│   │   ├── vae/
│   │   ├── embeddings/
│   │   ├── controlnet/
│   │   ├── upscale_models/
│   │   └── clip/
│   ├── outputs/
│   ├── lora_datasets/
│   ├── lora_work/
│   ├── workflows/
│   └── snapshots/
└── auto-kirinuki/
    ├── inputs/
    ├── outputs/
    └── models/
        └── whisper/
```

### bash（Raspberry Pi / Linux）

```bash
# マウント済み前提（例: /mnt/secretary-bot）
BASE=/mnt/secretary-bot

mkdir -p "$BASE"/ai-image/models/{checkpoints,loras,vae,embeddings,controlnet,upscale_models,clip}
mkdir -p "$BASE"/ai-image/{outputs,lora_datasets,lora_work,workflows,snapshots}
mkdir -p "$BASE"/auto-kirinuki/{inputs,outputs,models/whisper}

# 確認
ls -la "$BASE"
ls -la "$BASE"/ai-image
ls -la "$BASE"/auto-kirinuki
```

### PowerShell（Main PC / Sub PC）

```powershell
# マウント済み前提（例: N: ドライブ = \\NAS\secretary-bot）
$Base = "N:\"

$dirs = @(
  "ai-image\models\checkpoints", "ai-image\models\loras", "ai-image\models\vae",
  "ai-image\models\embeddings", "ai-image\models\controlnet", "ai-image\models\upscale_models", "ai-image\models\clip",
  "ai-image\outputs", "ai-image\lora_datasets", "ai-image\lora_work", "ai-image\workflows", "ai-image\snapshots",
  "auto-kirinuki\inputs", "auto-kirinuki\outputs", "auto-kirinuki\models\whisper"
)
foreach ($d in $dirs) {
  New-Item -ItemType Directory -Path (Join-Path $Base $d) -Force | Out-Null
}

# 確認
Get-ChildItem $Base
Get-ChildItem N:\ai-image
Get-ChildItem N:\auto-kirinuki
```

---

## 3. 権限・認証情報

### SMB 共有の推奨設定（NAS 側）

| 対象 | 権限 |
|---|---|
| 全員（読み取り） | `secretary-bot/` 配下すべて読み取り可 |
| Pi のサービスユーザー | `ai-image/workflows/`, `ai-image/lora_datasets/`, `ai-image/snapshots/`, `auto-kirinuki/inputs/` に書き込み |
| Main PC / Sub PC のサービスユーザー | `ai-image/outputs/`, `ai-image/lora_work/`, `ai-image/models/loras/`, `auto-kirinuki/outputs/`, `auto-kirinuki/models/whisper/` に書き込み |
| 管理者のみ | `ai-image/models/checkpoints/`, `ai-image/models/vae/`, `ai-image/models/embeddings/` 等への書き込み（モデル配置は管理者操作） |

- ゲストアクセス無効、**ユーザー認証必須**
- 書き込み可能な最小範囲のみ割り当てる（誤操作防止）

### 認証情報の管理

- 各 PC の **`.env`** で管理する（`config.yaml` には書かない）
- GitHub パブリックリポジトリ前提のため、`.env` は必ず `.gitignore` 対象であること

`.env` 追記例（Pi 側）:

```dotenv
# NAS (SMB)
NAS_SMB_HOST=192.168.1.xx
NAS_SMB_SHARE=secretary-bot
NAS_SMB_USER=secretary-bot-rw
NAS_SMB_PASSWORD=********
NAS_MOUNT_POINT=/mnt/secretary-bot
```

Windows Agent 側 `windows-agent/config/.env`:
```dotenv
NAS_HOST=192.168.1.xx
NAS_SHARE=secretary-bot
NAS_USER=secretary-bot-rw
NAS_PASS=********
```

---

## 4. マウント手順

### 4.1 Raspberry Pi (Linux, arm64)

#### 要件
```bash
sudo apt update
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/secretary-bot
```

#### 認証ファイル
`/etc/cifs-credentials-secretary-bot`（root 所有・600）:

```
username=secretary-bot-rw
password=********
domain=WORKGROUP
```

```bash
sudo chown root:root /etc/cifs-credentials-secretary-bot
sudo chmod 600 /etc/cifs-credentials-secretary-bot
```

#### `/etc/fstab` エントリ例

```fstab
//192.168.1.xx/secretary-bot  /mnt/secretary-bot  cifs  credentials=/etc/cifs-credentials-secretary-bot,iocharset=utf8,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,vers=3.0,nofail,x-systemd.automount,x-systemd.device-timeout=10  0  0
```

- `iocharset=utf8`: 日本語ファイル名の文字化け防止
- `nofail` + `x-systemd.automount`: NAS 未起動でも Pi のブートを止めない
- `vers=3.0`: SMB プロトコル明示

#### 反映・テスト
```bash
sudo systemctl daemon-reload
sudo mount -a
ls /mnt/secretary-bot
ls /mnt/secretary-bot/ai-image
ls /mnt/secretary-bot/auto-kirinuki
```

### 4.2 Windows Main PC / Sub PC

ドライブレター **`N:`** を推奨（`extra_model_paths.yaml` 設定やスクリプトでの統一のため）。

#### `net use` 方式（バッチ / 手動）

```bat
net use N: \\192.168.1.xx\secretary-bot /user:secretary-bot-rw <password> /persistent:yes
```

#### PowerShell `New-SmbMapping` 方式（推奨）

```powershell
# 資格情報は対話入力（スクリプト内に平文パスワードを書かない）
$cred = Get-Credential -UserName "secretary-bot-rw" -Message "NAS (secretary-bot) credentials"
New-SmbMapping -LocalPath "N:" -RemotePath "\\192.168.1.xx\secretary-bot" `
  -UserName $cred.UserName -Password $cred.GetNetworkCredential().Password `
  -Persistent $true

# 確認
Get-SmbMapping
Get-ChildItem N:\ai-image
Get-ChildItem N:\auto-kirinuki
```

#### 自動再接続のサービス化（任意）
Windows Agent 起動前に必ずマウントされている状態にするため、`start_agent.bat` の先頭で `net use` を叩いて冪等に再接続させる構成が安全。`windows-agent/tools/image_gen/nas_mount.py` が起動時に同 UNC マッピングを検出して再利用するため、image_gen と clip_pipeline で `N:` を共用できる。

---

## 5. SHA256 サイドカー生成

キャッシュ検証のため、モデル系ファイル（`*.safetensors`, `*.ckpt`, `*.pt`, `*.bin` 等）の横に `<filename>.sha256` を配置する（設計書「ハッシュサイドカー」参照）。

対象ディレクトリ:
- `ai-image/models/` 配下（SDXL/LoRA/VAE 等）
- `auto-kirinuki/models/whisper/` 配下（`*.pt`）

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

対象は `ai-image/models/` および `auto-kirinuki/models/whisper/` 配下のモデル拡張子。既にサイドカーがあればスキップ（再計算コストが高いため）。

#### bash（Pi で一括）
```bash
for BASE in /mnt/secretary-bot/ai-image/models /mnt/secretary-bot/auto-kirinuki/models/whisper; do
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
done
```

#### PowerShell（Windows で一括）
```powershell
$Bases = @("N:\ai-image\models", "N:\auto-kirinuki\models\whisper")
$exts = @("*.safetensors", "*.ckpt", "*.pt", "*.bin")

foreach ($Base in $Bases) {
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
}
```

> 注意: 1GbE + HDD では SDXL 本体 1 個のハッシュに数十秒〜1 分かかる。Pi で長時間走らせるなら `nice` / `ionice` 併用。

---

## 6. 保持ポリシーの確認

設計書「保持ポリシー」に準拠。**自動クリーニングは原則行わない**。

| ディレクトリ | ポリシー |
|---|---|
| `ai-image/outputs/` | **削除しない**（無期限）。`YYYY-MM/YYYY-MM-DD/` で日毎フォルダ分け |
| `ai-image/lora_work/` | **基本保持**。WebGUI の手動削除ボタンで対応。NAS 容量逼迫時（例: 共有残り < 50GB）のみ最古プロジェクトから自動削除、`models/loras/` に昇格済みのものを優先対象 |
| `ai-image/lora_datasets/` | 削除しない（明示的な削除操作のみ） |
| `ai-image/models/loras/` | 削除しない（明示的な削除操作のみ） |
| `ai-image/models/` 配下その他 | 管理者による明示削除のみ |
| `ai-image/workflows/` / `ai-image/snapshots/` | バックアップ扱い、削除は明示操作 |
| `auto-kirinuki/outputs/` | **削除しない**（手動削除のみ）。`<video_name>/` 単位で保持 |
| `auto-kirinuki/models/whisper/` | 管理者による明示削除のみ |
| `auto-kirinuki/inputs/` | 手動削除で OK |

---

## 7. 動作確認

初期化完了後、各 PC から読み書きテストを行う。

### 7.1 読み取りテスト（全 PC）

#### Linux (Pi)
```bash
ls /mnt/secretary-bot
stat /mnt/secretary-bot/ai-image/models
stat /mnt/secretary-bot/auto-kirinuki/models/whisper
```

#### Windows (Main/Sub)
```powershell
Get-ChildItem N:\
Get-ChildItem N:\ai-image\models
Get-ChildItem N:\auto-kirinuki\models\whisper
```

### 7.2 書き込みテスト（役割別）

それぞれの PC で「書き込み権限があるはず」のパスと「無いはず」のパスの両方を叩き、期待通り成功/失敗するかを確認。

#### Pi（書き込み可: `ai-image/workflows/`, `ai-image/lora_datasets/`, `ai-image/snapshots/`, `auto-kirinuki/inputs/`）
```bash
echo "test $(date -Is)" > /mnt/secretary-bot/ai-image/workflows/_pi_write_test.txt && \
  echo OK || echo NG
rm /mnt/secretary-bot/ai-image/workflows/_pi_write_test.txt

# 書き込み不可のはず（ai-image/models/checkpoints/）
touch /mnt/secretary-bot/ai-image/models/checkpoints/_should_fail.txt 2>&1 | head -1
```

#### Main PC / Sub PC（書き込み可: `ai-image/outputs/`, `ai-image/lora_work/`, `ai-image/models/loras/`, `auto-kirinuki/outputs/`, `auto-kirinuki/models/whisper/`）
```powershell
"test $(Get-Date -Format o)" | Out-File N:\ai-image\outputs\_win_write_test.txt
if ($?) { "OK" } else { "NG" }
Remove-Item N:\ai-image\outputs\_win_write_test.txt

"test $(Get-Date -Format o)" | Out-File N:\auto-kirinuki\outputs\_win_write_test.txt
if ($?) { "OK" } else { "NG" }
Remove-Item N:\auto-kirinuki\outputs\_win_write_test.txt

# 書き込み不可のはず（ai-image/models/checkpoints/）
try { "x" | Out-File N:\ai-image\models\checkpoints\_should_fail.txt -ErrorAction Stop }
catch { Write-Host "EXPECTED DENY: $($_.Exception.Message)" }
```

### 7.3 クロス可視性テスト
Pi で書いたファイルが Main/Sub から即座に見えるか、逆方向も確認。

```bash
# Pi
echo "hello from pi" > /mnt/secretary-bot/ai-image/workflows/_cross_test.txt
```
```powershell
# Main
Get-Content N:\ai-image\workflows\_cross_test.txt
```

確認後に削除:
```bash
rm /mnt/secretary-bot/ai-image/workflows/_cross_test.txt
```

---

## 8. トラブルシュート

### 8.1 認証失敗（`mount error(13): Permission denied` / `SYSTEM ERROR 1326`）
- `.env` / `/etc/cifs-credentials-secretary-bot` のユーザー名・パスワードを再確認
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
- API 仕様: `docs/image_gen/api.md`
- 切り抜き機能: `docs/auto_kirinuki/design.md`
- 旧 `ai-image` 共有からの移行: `docs/auto_kirinuki/nas_migration.md`
- `.env` 追加項目: `SECRETARY_BOT_ROOT`, `SECRETARY_BOT_CACHE`, `NAS_SMB_HOST` ほか
