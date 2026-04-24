> **このドキュメントは secretary-bot プロジェクトの機能・コードには直接関係しません。**
>
> Raspberry Pi 上で動作している Cloudflare Tunnel (cloudflared) の
> 自動アップデート運用に関するメモです。

# cloudflared 自動アップデートが効いていなかった件（2026-04-24）

## 症状

Cloudflare Zero Trust ダッシュボードで cloudflared のバージョンが「古い」と表示される。
過去に自動アップデートを仕込んだ記憶があるのに、なぜか更新されていなかった。

## 原因（2段構え）

### 1. `cloudflared-update.timer` が disabled だった

```
cloudflared-update.timer
  Loaded: loaded (...; disabled; preset: enabled)
  Active: inactive (dead)
```

preset は `enabled` なので本来有効であるべきだが、どこかのタイミングで止まっていた。
タイマーが発火しないので、当然 `cloudflared-update.service` も走らない。

### 2. `cloudflared-update.service` の中身が apt 管理と不整合だった

当初の ExecStart:

```
ExecStart=/bin/bash -c '/usr/bin/cloudflared update; code=$?; \
  if [ $code -eq 11 ]; then systemctl restart cloudflared; exit 0; fi; exit $code'
```

これはバイナリ直接インストール時代の作り。現環境は apt 経由インストール
（`/etc/apt/sources.list.d/cloudflared.list` が存在）なので、
`cloudflared update` サブコマンドは以下のエラーで拒否される：

```
ERR cloudflared was installed by a package manager. Please update using the same method.
```

つまり仮にタイマーが有効でも、中で走る update コマンドが毎回失敗する構造だった。

## 対処

### タイマー有効化

```bash
sudo systemctl enable --now cloudflared-update.timer
```

### service を apt 版に書き換え

`/etc/systemd/system/cloudflared-update.service`:

```ini
[Unit]
Description=Update cloudflared (apt)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade -y cloudflared && systemctl try-restart cloudflared'
```

`sudo systemctl daemon-reload` を忘れずに。

## 確認コマンド

```bash
# タイマーの状態と次回発火時刻
systemctl list-timers cloudflared-update.timer --no-pager

# 手動で一度走らせる
sudo systemctl start cloudflared-update.service
journalctl -u cloudflared-update.service -n 30 --no-pager

# インストール済み vs 候補 vs upstream
apt-cache policy cloudflared
curl -s https://api.github.com/repos/cloudflare/cloudflared/releases/latest \
  | grep -E '"(tag_name|published_at)"'
```

## 既知の注意点

- **Cloudflare の apt リポジトリは GitHub リリースより遅れることがある**。
  ダッシュボードが「古い」と言っても、apt 側がまだ出していないなら打てる手はない。
  この場合は apt リポジトリの更新待ち。
- **apt ソースは `bookworm` 指定のまま**（Pi OS は `trixie`）。
  現状動いているが、将来 Cloudflare が trixie 版を出したら切り替えた方が健全。
  ソース: `deb https://pkg.cloudflare.com/cloudflared bookworm main`
- service 本体 (`cloudflared.service`) は `--no-autoupdate` 付きで起動している。
  これは意図通り。更新は update.timer 経由で一元化する。
