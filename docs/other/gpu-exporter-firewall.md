> **このドキュメントは secretary-bot プロジェクトの機能・コードには直接関係しません。**
>
> Windows PC 側の nvidia_gpu_exporter と Pi 上の VictoriaMetrics 間の
> ネットワーク疎通に関する運用メモです。

# nvidia_gpu_exporter のメトリクスが VictoriaMetrics に入らなくなった件（2026-04-24）

## 症状

Maintenance → Agent Management で Sub PC の「GPU使用率」が `exporter未導入` 表示になる。

一方で：
- Sub PC ローカルでは `curl http://localhost:9835/metrics` は正常に返る
- Grafana のダッシュボードから過去の GPU 使用率は見える（データ自体は過去分が残っている）
- windows_exporter（`:9182`）の CPU/メモリメトリクスは正常に取得できている

## 原因

**Sub PC の Windows Firewall で `9835/TCP` インバウンドルールが消えていた**。

`:9182`（windows_exporter）の許可ルールは残っていたが `:9835`（nvidia_gpu_exporter）のルールだけ抜けており、
Pi からのスクレイプが `Client.Timeout exceeded` でタイムアウトしていた。

### 調査の流れ

1. `agent_pool.py` の GPU チェックは空結果＝`exporter未導入` と表示する設計（コードに問題なし）
2. Pi の VictoriaMetrics で `nvidia_smi_utilization_gpu_ratio` を直接クエリしても空
3. `/api/v1/targets` を確認したら `gpu_subpc` ターゲットが `down`、エラーは `Client.Timeout exceeded while awaiting headers`
4. Pi → Sub PC への疎通確認で `:9182` は HTTP 200 だが `:9835` はタイムアウト
5. → Windows Firewall のインバウンドルール喪失と特定

### 確認コマンド

Pi から：

```bash
# ターゲットのヘルス確認
ssh iniwapi 'curl -s "http://localhost:8428/api/v1/targets" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); \
  [print(t[\"scrapePool\"], t[\"health\"], t.get(\"lastError\",\"\")[:80]) \
   for t in d[\"data\"][\"activeTargets\"] if \"gpu\" in t[\"scrapePool\"]]"'

# ポート疎通確認
ssh iniwapi 'timeout 5 curl -s -o /dev/null -w "HTTP:%{http_code} Time:%{time_total}s\n" \
  http://192.168.1.211:9835/metrics'
```

## 対応

Sub PC で PowerShell を **管理者** で開いて：

```powershell
New-NetFirewallRule -DisplayName "nvidia_gpu_exporter" `
  -Direction Inbound -LocalPort 9835 -Protocol TCP -Action Allow
```

スクレイプ間隔（1分）後に VictoriaMetrics にメトリクスが入り、`gpu_subpc` ターゲットが UP に戻る。

## 再発防止メモ

- Windows Update や OS リセット等でファイアウォールルールが消える可能性あり
- `:9182`（windows_exporter）だけが動いて `:9835`（nvidia_gpu_exporter）が落ちている時は
  **まずファイアウォールを疑う**
- 既存ルール確認：
  ```powershell
  Get-NetFirewallPortFilter -Protocol TCP | Where-Object LocalPort -eq 9835 | Get-NetFirewallRule
  ```

## ラベル設計メモ

VictoriaMetrics 側で `instance` ラベルは以下のようにリラベルされている：

| ターゲット | `__address__` | 最終 `instance` ラベル |
|---|---|---|
| windows_exporter (Sub) | `192.168.1.211:9182` | `windows-subpc` |
| nvidia_gpu_exporter (Sub) | `192.168.1.211:9835` | `windows-subpc` |
| windows_exporter (Main) | `192.168.1.210:9182` | `windows-gamepc` |
| nvidia_gpu_exporter (Main) | `192.168.1.210:9835` | `windows-gamepc` |

`config.yaml` の `agents[].metrics_instance` は **ホスト名**（`windows-subpc` / `windows-gamepc`）を指定する。
IP:ポート表記ではない点に注意。
