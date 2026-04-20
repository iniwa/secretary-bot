# NAS 共有再編手順（ai-image → secretary-bot）

既存 `ai-image` 共有を親共有 `secretary-bot` 配下へ格納し、`auto-kirinuki/` を並置する作業手順。**新規共有を作成して中身をコピーし、旧共有を切り離す方式**で停止時間を最小化する。

## 0. 前提と目標

### 移行前
```
\\NAS\ai-image\
├── models\
├── outputs\
├── lora_datasets\
├── lora_work\
├── workflows\
└── snapshots\
```

### 移行後
```
\\NAS\secretary-bot\
├── ai-image\              （旧 ai-image 共有の中身）
│   ├── models\
│   ├── outputs\
│   ├── lora_datasets\
│   ├── lora_work\
│   ├── workflows\
│   └── snapshots\
└── auto-kirinuki\
    ├── inputs\
    ├── outputs\
    └── models\
        └── whisper\
```

### 影響範囲
- Pi: `/etc/fstab` / `/etc/cifs-credentials-*` / `.env` / `config.yaml`
- Main/Sub PC: `net use N:` のマッピング先 / `windows-agent/config/agent_config.yaml` / `windows-agent/config/.env`
- secretary-bot コード: 設定ファイル値のみ（実装は subpath 駆動で変更不要）

## 1. 事前準備

### 1.1 NAS 側容量確認
`secretary-bot/ai-image/` へのコピー用に旧 `ai-image` と同等以上の空き容量が必要。モデル（SDXL本体 7GB + LoRA多数）+ outputs 累計を合算して見積もる。

```bash
# 現在の使用量
ssh iniwapi
df -h /mnt/ai-image
du -sh /mnt/ai-image/*
```

### 1.2 ダウンタイム計画
- コピー中は読み書き可能（旧共有はそのまま）
- 切り替え時のみ全 PC で数分間停止
- 推奨: ComfyUI / kohya が空いている時間帯（夜間）

### 1.3 バックアップ方針
- `models/` は NAS が正本なので、移行中の同時書き込みを避けるためコピー前に ComfyUI/kohya を全停止
- `lora_work/` / `outputs/` は作業中のものが無いことを確認

## 2. NAS 側作業

### 2.1 新規共有 `secretary-bot` を作成
NAS 管理 UI（Synology DSM / TrueNAS / 自作 Samba 等）で以下を設定:

- 共有名: `secretary-bot`
- 物理パス: NAS の任意プールに新規ディレクトリ
- ACL: 旧 `ai-image` 共有と同じユーザー・権限を付与
  - `ai-image-rw` ユーザーに読み書き
  - ゲストアクセス無効
- SMB バージョン: 3.0 以上
- 文字コード: UTF-8

### 2.2 ディレクトリ初期作成
```bash
# NAS マウント済み Pi で実行
ssh iniwapi
sudo mkdir -p /mnt/secretary-bot
sudo mount -t cifs //192.168.1.xx/secretary-bot /mnt/secretary-bot \
  -o credentials=/etc/cifs-credentials-ai-image,iocharset=utf8,vers=3.0

mkdir -p /mnt/secretary-bot/ai-image
mkdir -p /mnt/secretary-bot/auto-kirinuki/{inputs,outputs,models/whisper}
```

## 3. データコピー

### 3.1 ai-image の中身をコピー
Pi 上で rsync でコピー（HDD 負荷分散のため `nice` 併用）:

```bash
# 事前: ComfyUI / kohya を全停止
nice -n 10 ionice -c 3 \
  rsync -aH --info=progress2 \
    /mnt/ai-image/ \
    /mnt/secretary-bot/ai-image/

# モデルは数十 GB あるため数時間かかる可能性あり
# tmux / screen 推奨
```

### 3.2 整合性確認
```bash
# ファイル数一致確認
find /mnt/ai-image -type f | wc -l
find /mnt/secretary-bot/ai-image -type f | wc -l

# sha256 サイドカーの検証（任意、重い）
diff -rq /mnt/ai-image/models /mnt/secretary-bot/ai-image/models | head -20
```

### 3.3 auto-kirinuki 用 Whisper モデル配置（任意、初回ジョブで自動同期可）
```bash
# 手動配置する場合
# Whisper モデルは openai-whisper のキャッシュ形式（.pt）を想定
# ローカル PC で事前 DL → NAS へコピー
```

## 4. 切り替え

### 4.1 全 PC の関連サービス停止

```bash
# Pi
ssh iniwapi
cd ~/docker/secretary-bot
docker compose stop bot  # ComfyUI アクセスを止める

# Main PC / Sub PC
# Windows Agent / ComfyUI / kohya 停止（start_agent.bat のコンソールを Ctrl+C）
```

### 4.2 Pi 側マウント変更

`/etc/cifs-credentials-secretary-bot` を新規作成:
```
username=ai-image-rw
password=********
domain=WORKGROUP
```
```bash
sudo chmod 600 /etc/cifs-credentials-secretary-bot
```

`/etc/fstab` を書き換え:
```fstab
# 旧行（コメントアウト）
#//192.168.1.xx/ai-image  /mnt/ai-image  cifs  credentials=/etc/cifs-credentials-ai-image,...

# 新行
//192.168.1.xx/secretary-bot  /mnt/secretary-bot  cifs  credentials=/etc/cifs-credentials-secretary-bot,iocharset=utf8,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,vers=3.0,nofail,x-systemd.automount,x-systemd.device-timeout=10  0  0
```

旧マウント解除 + 新マウント:
```bash
sudo umount /mnt/ai-image
sudo rmdir /mnt/ai-image
sudo mkdir -p /mnt/secretary-bot
sudo systemctl daemon-reload
sudo mount -a
ls /mnt/secretary-bot/ai-image
ls /mnt/secretary-bot/auto-kirinuki
```

### 4.3 Pi 側設定ファイル更新

`.env`:
```dotenv
# 旧
# NAS_SHARE=ai-image
# NAS_MOUNT_POINT=/mnt/ai-image

# 新
NAS_SHARE=secretary-bot
NAS_MOUNT_POINT=/mnt/secretary-bot
```

`config.yaml`:
```yaml
units:
  image_gen:
    nas:
      base_path: "/mnt/secretary-bot/ai-image"   # 変更
  clip_pipeline:                                  # 新規
    nas:
      base_path: "/mnt/secretary-bot/auto-kirinuki"
```

### 4.4 Main PC / Sub PC のマウント変更

PowerShell:
```powershell
# 旧マッピング解除
net use N: /delete /y
# 万一資格情報が残っていれば削除
cmdkey /delete:192.168.1.xx

# 新マッピング（secretary-bot 共有へ）
# Windows 側では subpath を使うため、共有ルート（secretary-bot）を N: に貼り、
# 実効パスは N:\ai-image\ / N:\auto-kirinuki\ となる
net use N: \\192.168.1.xx\secretary-bot /user:ai-image-rw <password> /persistent:yes

# 確認
Get-ChildItem N:\
Get-ChildItem N:\ai-image
```

### 4.5 Main PC / Sub PC 設定ファイル更新

`windows-agent/config/.env`:
```dotenv
NAS_SHARE=secretary-bot
```

`windows-agent/config/agent_config.yaml`:
```yaml
image_gen:
  nas:
    share: "secretary-bot"         # 変更
    subpath: "ai-image"             # 変更
    mount_drive: "N:"

clip_pipeline:                      # 新規
  enabled: true
  root: "C:/secretary-bot/clip-pipeline"
  cache: "C:/secretary-bot-cache/whisper"
  nas:
    share: "secretary-bot"
    subpath: "auto-kirinuki"
    mount_drive: "N:"
```

### 4.6 サービス起動 / 疎通確認

```bash
# Pi
docker compose up -d bot
docker logs -f secretary-bot | head -100
# /health が 200 を返すこと、image_gen のモデル一覧が取得できること
```

```powershell
# Main/Sub PC
cd C:\path\to\secretary-bot\windows-agent
.\start_agent.bat

# 別ターミナルで
curl http://localhost:7777/health -H "X-Agent-Token: <token>"
curl http://localhost:7777/capability -H "X-Agent-Token: <token>"
curl http://localhost:7777/clip-pipeline/capability -H "X-Agent-Token: <token>"
```

### 4.7 テスト生成 / テスト切り抜き

1. Pi WebGUI の image_gen ページで 1 枚生成 → `N:\ai-image\outputs\` に出力されること
2. WebGUI の clip_pipeline ページで短い動画を投入 → 各ステップが進行し `N:\auto-kirinuki\outputs\<video>\` に結果出力

## 5. 旧共有の停止

疎通確認が完了したら:

```
# NAS 管理 UI で
- 旧 ai-image 共有を停止（まだ削除はしない、念のため 1〜2 週間保留）
- その後問題なければ ai-image 共有を削除
```

## 6. ロールバック手順（問題発生時）

1. 全 PC のサービス停止
2. 設定ファイルを元に戻す（`.env` / `config.yaml` / `agent_config.yaml`）
3. マウントを旧 `ai-image` 共有へ戻す（`/etc/fstab` 復旧 + `net use N: \\...\ai-image`）
4. サービス再起動
5. 原因調査

`secretary-bot` 共有に書き込まれた新規データ（`auto-kirinuki/outputs/*` 等）は旧共有にマージ必要な場合のみ手動対応。

## 7. チェックリスト

### 事前
- [ ] NAS 空き容量確認（旧 ai-image 使用量 × 2 以上推奨）
- [ ] 全 PC の ComfyUI / kohya / Agent 停止可能な時間帯か
- [ ] 作業中の lora_work ジョブが無いこと

### コピー
- [ ] `secretary-bot` 共有作成
- [ ] `ai-image/` サブディレクトリ作成
- [ ] rsync でコピー完了
- [ ] ファイル数一致

### 切り替え
- [ ] Pi: `/etc/fstab` 書き換え、新マウント OK
- [ ] Pi: `.env` / `config.yaml` 更新
- [ ] Main PC: `net use N:` 貼り直し
- [ ] Sub PC: `net use N:` 貼り直し
- [ ] Main PC: `agent_config.yaml` / `.env` 更新
- [ ] Sub PC: `agent_config.yaml` / `.env` 更新

### 疎通
- [ ] Pi /health 200
- [ ] Main PC `/capability` 正常
- [ ] Sub PC `/capability` 正常
- [ ] image_gen テスト生成成功（出力が新パスへ）
- [ ] clip_pipeline テスト切り抜き成功（出力が新パスへ）

### 事後
- [ ] 旧 `ai-image` 共有を NAS 側で停止（削除は保留）
- [ ] 1〜2 週間様子見の後、旧共有を削除
