# Remote PC から Sub PC の VS Code に接続して Claude Code を使う

Remote PC（外出先 PC 等）から Sub PC（自宅 LAN `192.168.1.211` / RTX 5060 Ti 搭載機）に
VS Code Remote-SSH で接続し、Sub PC 側で Claude Code を動作させる構成の手順。

- 接続経路: `Remote PC → Cloudflare Tunnel → Sub PC sshd`
- エイリアス: `subpcssh`（`~/.ssh/config` 設定済み）
- ドメイン: `subpcssh.iniwach.com`
- Sub PC 側ユーザー: `iniwaminipc`

---

## Remote PC 側（作業済み）

- `~/.ssh/config` に `subpcssh` エントリあり
- `cloudflared` v2026.2.0 インストール済み
- 公開鍵: `~/.ssh/id_ed25519.pub`（後述、Sub PC に登録する）

接続コマンド（Sub PC 側の準備完了後に動作する想定）:

```bash
ssh subpcssh
```

VS Code からは `Remote-SSH: Connect to Host...` → `subpcssh` を選択。

---

## Sub PC 側の残作業

以下の作業は **Sub PC 上で直接**（またはリモートデスクトップ等で）行う必要がある。

### 1. OpenSSH Server のインストール・起動

管理者権限 PowerShell:

```powershell
# インストール状況確認
Get-WindowsCapability -Online -Name OpenSSH.Server*

# 未インストールなら追加
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# サービス自動起動 + 開始
Set-Service sshd -StartupType Automatic
Start-Service sshd

# 起動確認
Get-Service sshd
```

Windows ファイアウォール（ローカル LAN のみ開放する形で十分。Tunnel 経由なので外部公開不要）:

```powershell
# 既定で OpenSSH-Server-In-TCP ルールが追加される。未作成なら:
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

### 2. 公開鍵の登録（Remote PC → Sub PC）

Sub PC のユーザー（`iniwaminipc` 想定）でログインし、以下を実行:

```powershell
# .ssh ディレクトリ作成
$sshDir = "$env:USERPROFILE\.ssh"
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null

# authorized_keys に Remote PC の公開鍵を追記
Add-Content -Path "$sshDir\authorized_keys" -Value `
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHHt2glWm3ySSj+1tx6GU1rGbjOyqEnwUmDIcjogIsyb iniwa_remote_access'

# 権限設定（Windows sshd はここが厳格で、緩いと認証拒否される）
icacls "$sshDir\authorized_keys" /inheritance:r
icacls "$sshDir\authorized_keys" /grant "$($env:USERNAME):F"
icacls "$sshDir\authorized_keys" /grant "SYSTEM:F"
```

> ⚠️ **管理者ユーザーの場合**は `authorized_keys` を `C:\ProgramData\ssh\administrators_authorized_keys` に置く必要がある（Windows sshd の仕様）。
> `iniwaminipc` が管理者権限ユーザーなら後者を使う。

管理者用の場合:

```powershell
$adminKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
Add-Content -Path $adminKeys -Value `
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHHt2glWm3ySSj+1tx6GU1rGbjOyqEnwUmDIcjogIsyb iniwa_remote_access'

icacls $adminKeys /inheritance:r
icacls $adminKeys /grant "Administrators:F"
icacls $adminKeys /grant "SYSTEM:F"
```

### 3. sshd 設定の確認

`C:\ProgramData\ssh\sshd_config` を確認し、以下が有効になっていること:

```
PubkeyAuthentication yes
PasswordAuthentication no            # 任意。公開鍵のみで運用するなら no 推奨
```

変更したら再起動:

```powershell
Restart-Service sshd
```

### 4. Cloudflare Tunnel（`cloudflared`）の設定確認

`subpcssh.iniwach.com` が既に Cloudflare 側で定義済みなので、
Sub PC 上で cloudflared が動作し Tunnel がローカル sshd（`localhost:22`）を公開している必要がある。

```powershell
# インストール状況確認
cloudflared --version

# サービス登録状況
Get-Service cloudflared -ErrorAction SilentlyContinue
```

未セットアップなら:

1. https://one.dash.cloudflare.com/ → Networks → Tunnels で `subpcssh` 相当の Tunnel 設定を確認（既存なら Token を取得）
2. `cloudflared.exe` を配置（`C:\Program Files (x86)\cloudflared\` など）
3. サービス登録:

```powershell
cloudflared.exe service install <TUNNEL_TOKEN>
```

4. Tunnel 設定の Public Hostname に `subpcssh.iniwach.com → ssh://localhost:22` が登録されていること

### 5. VS Code + Claude Code のインストール

```powershell
# VS Code（winget 利用）
winget install --id Microsoft.VisualStudioCode -e

# Node.js（Claude Code 実行に必要）
winget install --id OpenJS.NodeJS.LTS -e

# Claude Code（Sub PC 上で、Sub PC のユーザーとしてインストール）
npm install -g @anthropic-ai/claude-code
```

VS Code 拡張は **Remote PC 側の VS Code から接続時に自動的に Sub PC 側へ転送インストール**されるので、Sub PC 側で事前に入れる必要はない（ただし Sub PC 側にローカル VS Code があると Claude Code 拡張が使えるので入れておくと便利）。

### 6. 動作確認（Remote PC から）

```bash
# SSH 疎通
ssh subpcssh "hostname"

# 期待値: Sub PC のホスト名が返る
```

VS Code で:

1. 拡張機能 `Remote - SSH` をインストール
2. コマンドパレット → `Remote-SSH: Connect to Host...` → `subpcssh`
3. 接続後、ターミナルを開いて `claude` を起動

---

## トラブルシューティング

| 症状 | 原因 / 対処 |
|------|------------|
| `Permission denied (publickey)` | `authorized_keys` の配置場所・ACL ミス。管理者ユーザーは `administrators_authorized_keys` を使う |
| `Connection closed by remote host` | sshd 停止中、または Tunnel が localhost:22 を向いていない |
| `cloudflared access ssh` でハング | Cloudflare Tunnel サービスが Sub PC 側で動いていない。`Get-Service cloudflared` で確認 |
| VS Code 接続後に拡張インストールで止まる | Sub PC のネットワークが遅い／Node.js 未導入。ターミナルから `node -v` 確認 |

## 参考

- 既存の類似ガイド: `docs/guides/remote_pc_webgui_access.md`（Pi WebGUI アクセス経路）
- `~/.ssh/config` の `subpcssh` エントリがこの接続の起点
