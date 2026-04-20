# Remote PC から Sub PC の VS Code に接続して Claude Code を使う

Remote PC（外出先 PC 等）から Sub PC（自宅 LAN `192.168.1.211` / RTX 5060 Ti 搭載機）に
VS Code Remote-SSH で接続し、Sub PC 側で Claude Code を動作させる構成の手順。

## 接続経路（Pi 経由構成）

Cloudflare Tunnel は **Pi** に集約する。Sub PC では cloudflared を動かさない。

```
Remote PC
  └ cloudflared access ssh (ProxyCommand)
    └ Cloudflare
      └ Pi の cloudflared（TCP ingress）
        └ tcp://192.168.1.211:22  ← Sub PC sshd（LAN 内で待ち受け）
```

- エイリアス: `subpcssh`（Remote PC の `~/.ssh/config` 済）
- ドメイン: `subpcssh.iniwach.com`
- Sub PC 側ユーザー: **`iniwa`**（管理者グループ所属）
- Pi 側で既存の cloudflared に **TCP ingress を追加**する

> ⚠️ 旧版の本ドキュメントは Sub PC に cloudflared を直接インストールする構成だったが、
> 「Cloudflare Tunnel は Pi に集約したい」という方針に合わせて Pi 経由へ刷新した。
> また Sub PC のユーザー名は `iniwaminipc` ではなく **`iniwa`** が正しい（Remote PC 側 `~/.ssh/config` の `User` 要修正）。

---

## Remote PC 側（作業済み想定）

- `~/.ssh/config` に `subpcssh` エントリあり
  - `User iniwa` に修正（もし `iniwaminipc` になっていたら書き換える）
  - `ProxyCommand cloudflared access ssh --hostname %h` を含める
- `cloudflared` v2026.2.0 以上インストール済み
- 公開鍵:
  ```
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHHt2glWm3ySSj+1tx6GU1rGbjOyqEnwUmDIcjogIsyb iniwa_remote_access
  ```

参考 `~/.ssh/config` エントリ:

```
Host subpcssh
  HostName subpcssh.iniwach.com
  User iniwa
  ProxyCommand cloudflared access ssh --hostname %h
  IdentityFile ~/.ssh/id_ed25519
```

接続コマンド（Sub PC + Pi の準備完了後）:

```bash
ssh subpcssh
```

VS Code からは `Remote-SSH: Connect to Host...` → `subpcssh`。

---

## Sub PC 側の残作業

Sub PC 上で直接（またはリモートデスクトップ等で）実行する。
現状: sshd はインストール済みだが **Stopped/Manual**、公開鍵は未登録、`sshd_config` 未生成。

### 一括セットアップ（推奨）

リポジトリ同梱のスクリプトを **管理者 PowerShell** で実行する:

```powershell
# リポジトリ ルートで
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_subpc_ssh.ps1
```

このスクリプトは以下を冪等に行う:

1. `sshd` を `Automatic` で起動（`sshd_config` が自動生成される）
2. ファイアウォールルール `OpenSSH-Server-In-TCP` を確認/作成（**LAN のみ許可**: RemoteAddress=LocalSubnet）
3. Remote PC の公開鍵を `C:\ProgramData\ssh\administrators_authorized_keys` に追記（重複排除）
4. `administrators_authorized_keys` の ACL を `Administrators:F` / `SYSTEM:F` に設定
5. `sshd_config` で `PubkeyAuthentication yes` を保証し、`PasswordAuthentication no` に設定
6. `sshd` を再起動
7. 最終状態サマリを表示

### 手動で確認したいとき

```powershell
Get-Service sshd
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Format-List DisplayName,Enabled,Profile,Direction,Action
Get-Content C:\ProgramData\ssh\administrators_authorized_keys
```

### ⚠️ Cloudflared は Sub PC に入れない

Pi 経由構成なので、Sub PC 側の `cloudflared service install` は **不要**。
誤って登録済みの場合は `cloudflared.exe service uninstall` で削除する。

---

## Pi 側の残作業（Cloudflare Tunnel に TCP ingress を追加）

Pi の cloudflared に `subpcssh.iniwach.com → tcp://192.168.1.211:22` ingress を追加する。

### 1. Cloudflare ダッシュボード側

`https://one.dash.cloudflare.com/` → **Networks → Tunnels** → Pi の既存 Tunnel を選択:

1. **Public Hostnames** タブで新規エントリを追加:
   - Subdomain: `subpcssh`
   - Domain: `iniwach.com`
   - Service Type: `SSH`
   - URL: `192.168.1.211:22`
2. 保存

これで Cloudflare 側 DNS + ルーティングが整う。

### 2. Pi 側 cloudflared の確認

Pi の cloudflared が **トークン起動モード**（`cloudflared service install <TOKEN>`）の場合、
Public Hostname の設定はダッシュボード側で完結するため **Pi 側で追加作業は不要**。

Pi の cloudflared が **config.yml モード**の場合のみ、Pi 上の
`/etc/cloudflared/config.yml`（または `~/.cloudflared/config.yml`）の `ingress:` に追記する:

```yaml
ingress:
  # ... 既存のルール ...
  - hostname: subpcssh.iniwach.com
    service: tcp://192.168.1.211:22
  - service: http_status:404
```

追記後:

```bash
ssh iniwapi "sudo systemctl restart cloudflared"
ssh iniwapi "sudo systemctl status cloudflared --no-pager"
```

### 3. Pi からの疎通確認（LAN 内）

Pi から Sub PC の sshd に LAN で到達できるか確認:

```bash
ssh iniwapi "nc -zv 192.168.1.211 22"
```

`Connection to 192.168.1.211 22 port [tcp/ssh] succeeded!` が出れば OK。
失敗する場合は Sub PC の Windows ファイアウォールで 22/tcp が Pi の IP からブロックされている
可能性がある。スクリプト既定の `LocalSubnet` ルールで通るはずだが、念のため:

```powershell
# Sub PC 管理者 PowerShell
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Get-NetFirewallAddressFilter
```

---

## 動作確認（Remote PC から）

### 1. cloudflared アクセス疎通

```bash
cloudflared access ssh --hostname subpcssh.iniwach.com
```

（Ctrl+C ですぐ抜けてよい。404 ではなく TCP ストリームが開けば OK。）

### 2. SSH 疎通

```bash
ssh -o ConnectTimeout=10 subpcssh "hostname"
# 期待値: Sub PC のホスト名
```

### 3. VS Code から

1. 拡張機能 `Remote - SSH` をインストール
2. コマンドパレット → `Remote-SSH: Connect to Host...` → `subpcssh`
3. 接続後、ターミナルを開いて:
   ```powershell
   claude --version    # 2.1.114 以上
   node --version      # v24.x
   ```
4. `claude` を起動して開発開始

---

## トラブルシューティング

| 症状 | 原因 / 対処 |
|------|------------|
| `Permission denied (publickey)` | `administrators_authorized_keys` に Remote PC の鍵が入っていない、または ACL 不正。`setup_subpc_ssh.ps1` を再実行 |
| `ssh_exchange_identification: Connection closed by remote host` | Sub PC の sshd が停止中。`Get-Service sshd` で確認し `Start-Service sshd` |
| `cloudflared access ssh` でハング | Pi の cloudflared が落ちている、または Cloudflare 側の Public Hostname 未登録。`ssh iniwapi "systemctl status cloudflared"` |
| `dial tcp 192.168.1.211:22: i/o timeout` | Pi → Sub PC の LAN 到達不可。Sub PC ファイアウォール、または Sub PC のスリープ復帰待ち |
| VS Code 接続後に拡張インストールで止まる | Sub PC のネットワークが遅い／Node.js 未導入。ターミナルから `node -v` 確認 |
| `User iniwaminipc` で認証失敗 | Remote PC 側 `~/.ssh/config` の User 要修正（`iniwa`） |

## セキュリティノート

- Sub PC sshd は **LAN（`LocalSubnet`）からのみ許可**。Cloudflare Tunnel は Pi が終端し、Pi から Sub PC への接続は LAN 内に閉じる。
- `PasswordAuthentication no` で公開鍵のみ。
- Cloudflare Access Application を `subpcssh.iniwach.com` に被せて、Cloudflare 認証（One-time PIN / IdP）を要求する多層防御も可能（任意）。

## 参考

- Pi WebGUI アクセス経路: `docs/guides/remote_pc_webgui_access.md`
- Sub PC セットアップスクリプト: `scripts/setup_subpc_ssh.ps1`
