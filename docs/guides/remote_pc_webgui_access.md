# Remote PC から WebGUI / Bot API にアクセスする

Remote PC（社外/外出先 PC など、自宅 LAN 192.168.1.0/24 にいない端末）から
Pi 上で動いている Bot WebGUI（`:8100`）にアクセスする方法。

主な用途は **Playwright MCP / curl などの自動化ツールが Pi の LAN に直接到達できないケース**。
Cloudflare Tunnel が稼働していない構成の Pi（現状の secretary-bot コンテナ）でもこの手順で接続できる。

## 前提

- Pi に SSH できる（`ssh iniwapi` が通る = `~/.ssh/config` のエイリアスが有効）
- Pi 側で WebGUI コンテナが `:8100` を公開している
- ローカル Windows 側で `:8100` を別プロセスが掴んでいない（後述の確認コマンド参照）

## 手順

### 1. SSH ポートフォワードを張る（バックグラウンド常駐）

```bash
ssh -f -N -L 8100:localhost:8100 -o ExitOnForwardFailure=yes iniwapi
```

- `-f` : バックグラウンド実行
- `-N` : リモートでコマンド実行しない（フォワードのみ）
- `-L 8100:localhost:8100` : ローカル `:8100` → Pi の `localhost:8100` に転送
- `-o ExitOnForwardFailure=yes` : ポート競合等で失敗したら即終了

> ⚠️ 既にフォワード中の場合は `bind [127.0.0.1]:8100: Address already in use` が出るが、
> 既存セッションが生きている証なのでそのまま使ってよい。

### 2. 疎通確認

```bash
curl -s -u admin:pass -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8100/health
```

`HTTP 200` が返れば OK。

### 3. ブラウザ / Playwright からアクセス

```
http://admin:pass@localhost:8100/tools/image-gen/
```

Basic 認証は URL に埋め込みできる（chromium 系・Playwright 共に対応）。

## トラブルシュート

| 症状 | 原因 | 対処 |
|------|------|------|
| `bind ... Address already in use` | 既存のフォワードが残っている | 既存を再利用すれば OK。落としたい場合は `pkill -f "ssh.*-L 8100"` |
| `Connection refused` | Pi 側コンテナが落ちている | `ssh iniwapi "docker ps \| grep secretary-bot"` で確認 |
| `502 Bad Gateway` がコンソールに出る | ComfyUI 側 PC（Main / Sub）がオフライン | 無関係。Pi → Windows Agent への到達失敗で、WebGUI 自体は動作中 |
| `net::ERR_CONNECTION_TIMED_OUT` | Pi の LAN IP（192.168.1.205）に直接アクセスしている | `localhost:8100` 経由（フォワード）に切り替える |

## なぜ直接 IP では繋がらないのか

Remote PC は VPN/別ネットワーク配下にあり、自宅 LAN セグメント `192.168.1.0/24` に
ルートを持たない。一方 SSH は外部からも通るため、SSH トンネル経由なら
Pi の `localhost` 名前空間を借りる形でアクセスできる。

## 関連

- 認証情報: Pi の `/home/iniwa/docker/secretary-bot/.env` （`WEBGUI_USERNAME` / `WEBGUI_PASSWORD`）
- 公開ポート: `docker ps` で `secretary-bot` 行の Ports 列を参照
