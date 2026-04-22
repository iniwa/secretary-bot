> **このドキュメントは secretary-bot プロジェクトの機能・コードには直接関係しません。**
>
> secretary-bot と同じ Raspberry Pi 上に同居している監視スタック
> （Grafana + VictoriaMetrics, Docker stack 名: `monitoring`, stack id: 50）
> のトラブルシューティング記録です。Pi の運用メモとしてここに残しています。

# Grafana アラート webhook が `%!f(string=)` / `[no value]` / Resolved を連発した件（2026-04-22）

## 症状

Discord に飛んできた Grafana アラート webhook の内容が崩れていた:

```
Resolved

Value: [no value]
Labels:
  alertname = DatasourceError
  grafana_folder = monitoring
  rulename = NAS - High Temperature
Annotations:
  Error = failed to build query 'A': [sqlstore.max-retries-reached] retry 1:
          database is locked (5) (SQLITE_BUSY)
  description = CPU温度が %!f(string=)°C に達しています
  grafana_state_reason = MissingSeries
  summary = NAS CPU温度が高い
```

- `alertname = DatasourceError` が大量発火
- `description` に `%!f(string=)` という Go fmt のエラーが混入
- `$labels.X` が `[no value]` に置換
- Firing のすぐ後に `grafana_state_reason = MissingSeries` の Resolved が飛ぶ

## 根本原因は 2 層に分かれる

### ① VictoriaMetrics のメモリ逼迫

```
victoriametrics   231.4MiB / 256MiB  (90.4%)
```

Pi 全体としては 8GB 搭載で余裕があるのに、compose 側の
`deploy.resources.limits.memory: 256m` が Pi 1 台ぶん全部のメトリクスを
保持するには小さすぎた。結果として Grafana からのクエリが
`context deadline exceeded` を返すようになり、Grafana が DatasourceError
を発火していた。

### ② Grafana 内部 SQLite (`grafana.db`) の激しいロック競合

Grafana 11 系は従来のダッシュボード/ユーザー系テーブルに加え、
「app platform / unified storage」の `resource_version` テーブルに対して
`concurrent-job-driver` や `job-cleanup-controller` が**高頻度で書き込む**。
さらに unified alerting の `ngalert.state.manager.persist` が同じ DB を使う。

`grafana.db` はデフォルトの `journal_mode=delete` のままだったため、
書き込みが排他的にシリアライズされてしまい、40〜50 秒かかって SQLITE_BUSY で
諦めるログが大量発生していた。

```
logger=ngalert.state.manager.persist ...
  level=error msg="Failed to save alert rule state"
  error="database is locked (5) (SQLITE_BUSY)" duration=49.938097252s
```

### ③ なぜ webhook 内容が崩れたか

①②のどちらでクエリが失敗しても、Grafana はそのルールを一旦
`state=Error` に落とし、次の評価で取れなかったシリーズを
`MissingSeries` として Resolved 化する。このとき:

- `$value` は空文字列扱いになり `printf "%.1f" $value` が
  `%!f(string=)` を返す
- `$labels.X` も同様に `[no value]` に展開される

つまり **webhook のフォーマットは壊れていない**。クエリが失敗した瞬間だけ
そう見えるだけ。根因は ①② のリソース/ロック問題。

## 対応

### A. インフラ層 (Portainer stack `monitoring` を編集)

```yaml
  victoriametrics:
    command:
      - "--storageDataPath=/storage"
      - "--retentionPeriod=12"
      - "--promscrape.config=/scrape_config.yml"
      - "--httpListenAddr=:8428"
      - "--memory.allowedPercent=60"          # 追加
    deploy:
      resources:
        limits:
          memory: 512m                         # 256m から増量

  grafana:
    environment:
      - TZ=Asia/Tokyo
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=${GF_SECURITY_ADMIN_PASSWORD}
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_DATABASE_WAL=true                   # 追加（※下の注意点参照）
      - GF_ANALYTICS_CHECK_FOR_UPDATES=false
      - GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
      - GF_ANALYTICS_REPORTING_ENABLED=false
      - GF_PLUGINS_PLUGIN_ADMIN_EXTERNAL_MANAGE_ENABLED=false
```

Portainer の `monitoring` stack（Stack ID 50）を Editor で貼り替え→
**Update the stack** で再デプロイ。

### B. grafana.db を WAL モードに手動変換（重要な罠）

**`GF_DATABASE_WAL=true` は Grafana 11 では効かない。**
Grafana 11 系のデフォルト SQLite ドライバは mattn/go-sqlite3 ではなく
pure-Go の **`modernc.org/sqlite`** に切り替わっており、このドライバ配下では
環境変数フラグで WAL が適用されない（起動時の journal_mode は `delete` のまま）。

確認コマンド:

```bash
sudo python3 -c "
import sqlite3
conn = sqlite3.connect('file:/home/iniwa/docker/monitoring/grafana-data/grafana.db?mode=ro', uri=True)
print(conn.execute('PRAGMA journal_mode;').fetchone())
# -> ('delete',)  ← 環境変数を設定しても効いていない
"
```

**解決**: SQLite の journal_mode は DB ファイル本体に永続化されるため、
**一度 PRAGMA で書き込めばドライバに関係なく以降はずっと WAL**。

```bash
sudo cp /home/iniwa/docker/monitoring/grafana-data/grafana.db \
        /home/iniwa/docker/monitoring/grafana-data/grafana.db.bak.$(date +%Y%m%d-%H%M%S)
docker stop grafana
sudo python3 -c "
import sqlite3
conn = sqlite3.connect('/home/iniwa/docker/monitoring/grafana-data/grafana.db', timeout=10)
print(conn.execute('PRAGMA journal_mode=WAL;').fetchone())
conn.close()
"
docker start grafana
# 起動後 PRAGMA journal_mode を問い合わせると ('wal',) になっている
# grafana-data/ に grafana.db-wal と grafana.db-shm が生成される
```

### C. アラートルールの noDataState / execErrState / テンプレート修正

Grafana Provisioning API 経由で 12 ルール / 34 箇所を一括更新。

- `noDataState`: `NoData` → `OK`
- `execErrState`: `Error` → `OK`
  → DatasourceError が webhook に飛ばなくなる
- annotations.description のテンプレートを安全形に:
  - `{{ $value | printf "%.1f" }}`
    → `{{ if $value }}{{ $value | printf "%.1f" }}{{ else }}N/A{{ end }}`
  - `{{ $labels.xxx }}`
    → `{{ with $labels.xxx }}{{ . }}{{ else }}N/A{{ end }}`

クエリが失敗しても `[no value]` や `%!f(string=)` ではなく `N/A` になる
保険としての修正（noData/execErr を OK にしている時点で基本的には発火しない）。

一括更新スクリプトは `.tmp/update_alert_rules.py` に保管済み。
再発時は同じスクリプトで dry-run → apply できる。

## 結果

修正前後の比較（再起動から 90 秒時点）:

| 指標 | 修正前 | 修正後 |
|---|---|---|
| `SQLITE_BUSY` 発生数 | 数十件/分 | 0 件/90s |
| `context deadline exceeded` / DatasourceError | 多発 | 0 件/90s |
| VictoriaMetrics メモリ | 231 / 256 MiB (90.4%) | 217 / 512 MiB (42.4%) |
| Grafana メモリ | 270 / 512 MiB (52.8%) | 181 / 512 MiB (35.3%) |
| `grafana.db` journal_mode | `delete` | `wal` |

## 追加対応: 再デプロイ直後の `Docker - Container Down` 誤発火

上記の修正後、Portainer で stack を再デプロイした直後に
`Docker - Container Down` アラートが grafana / victoriametrics / node-exporter 等
に対して一斉に Firing してしまった。

### 原因
`Docker - Container Down` ルールのクエリが **コンテナ ID (`id` ラベル) ごとのシリーズ**
を見ていた。

```promql
time() - container_last_seen{job="cadvisor", name=~".+"}
```

stack 再デプロイでコンテナが作り直されると ID が変わるが、cadvisor のキャッシュには
**古い ID のシリーズが数分間残る**。古いシリーズの `container_last_seen` は更新されない
ので `time() - container_last_seen` が単調増加し、閾値 120 秒を超えた瞬間に `for: 2m`
を経過して Firing する。新しい ID 側は正常な値なのに、古い ID 側の「亡霊シリーズ」が
誤通知の原因。

webhook には `id = /system.slice/docker-<old_hash>.scope` が載っていたので、それが
stale な古い ID だと分かる。

### 修正
クエリを **name 単位で最新 last_seen を集約** する形に変更:

```promql
time() - max by(name) (container_last_seen{job="cadvisor", name=~".+"})
```

Grafana Provisioning API 経由で適用（ルール UID: `cfg99m9v28000a`）:

```bash
# 取得 → expr を書き換え → PUT
curl -s -u admin:PASS \
  http://localhost:3003/api/v1/provisioning/alert-rules/cfg99m9v28000a > rule.json
# rule.json の data[0].model.expr を上記 max by(name) 版に置換
curl -X PUT -u admin:PASS \
  -H 'Content-Type: application/json' \
  -H 'X-Disable-Provenance: true' \
  http://localhost:3003/api/v1/provisioning/alert-rules/cfg99m9v28000a \
  --data @rule.json
```

### トレードオフ
- 同じ name のコンテナが生きていれば最新 last_seen が採用されるので誤発火しない
- 本当にコンテナが消えたケース（name ラベルのシリーズ自体が無くなる）は
  `noDataState=OK` のため Firing しない。name ベースの「過去存在したのに今ない」検知は
  別クエリ（`present_over_time` / `absent_over_time`）で追加実装する余地あり
- 同一 name で高速に再起動ループしているケースは検知が鈍る可能性あり

### 結果（再デプロイ後の検証）
- VictoriaMetrics でクエリ検証 → 全 29 コンテナが 21 秒（閾値 120 秒を大幅に下回る）
- Grafana ルール state: `inactive` / health: `ok`
- 全コンテナ `Normal`、Firing なし

## 学び（次回再発時に即参照すべき点）

1. **Grafana 11 で `GF_DATABASE_WAL=true` は効かない**。modernc.org/sqlite
   ドライバでは `PRAGMA journal_mode=WAL` を DB ファイル側に直接書き込む
   必要がある。一度書けば永続。
2. **webhook が `%!f(string=)` や `[no value]` で崩れて見えても、通知テンプレート
   自体は壊れていない**。クエリが失敗した瞬間の表示であって、根因は
   データソース or Grafana 内 DB 側にある。
3. DatasourceError は `noDataState=OK` / `execErrState=OK` にするだけで
   抑えられる。「通知が来るべきエラーなのか」をルール単位で考えるべき。
4. 次回 Grafana のバージョンアップで unified storage がもっと重くなり、
   WAL でも捌けなくなった場合は **SQLite → PostgreSQL 移行** が次の選択肢。
   Pi ARM64 でも postgres:16-alpine で 100〜150MiB 程度で運用可能。
5. **cadvisor の `container_last_seen` は `id` ラベルで一意**。コンテナ再作成で
   古い ID のシリーズが数分間 stale に残るので、`time() - container_last_seen` を
   そのまま閾値比較するとデプロイのたびに誤発火する。**`max by(name)` で集約**する
   のが定石。

## 関連ファイル

- Portainer stack 実体: `/var/lib/docker/volumes/portainer_data/_data/compose/50/docker-compose.yml`
- grafana.db: `/home/iniwa/docker/monitoring/grafana-data/grafana.db`
- アラート更新スクリプト（Windows 側）: `.tmp/update_alert_rules.py`
- バックアップ: `/home/iniwa/docker/monitoring/grafana-data/grafana.db.bak.20260422-103418`
