# 改善・追加実装案

## メトリクス連携（CPU/メモリ/GPU）

### 解決済み
- [x] `metrics_instance` 名の不一致 → `config.yaml` を VictoriaMetrics ラベル（`windows-gamepc`, `windows-subpc`）に修正済み
- [x] コンテナ内から VictoriaMetrics に到達不可 → `victoria_metrics_url` を `http://192.168.1.205:8428` に修正済み
- [x] Maintenance タブのチェック条件一覧表示を実装済み（CPU/メモリ/GPU/アクティビティを OK/NG で表示）

### 残タスク: GPU メトリクス exporter 未導入
- VictoriaMetrics に `nvidia_*` 系メトリクスが存在しない
- 各 Windows PC に GPU exporter を導入する必要がある

推奨: [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter)
- `agent_pool.py` の既存クエリ（`nvidia_smi_utilization_gpu_ratio`）とそのまま互換
- デフォルトポート `:9835`

導入手順:
1. 各 Windows PC に `nvidia_gpu_exporter` をインストール
2. VictoriaMetrics の scrape config に追加（instance ラベルを `windows-gamepc` / `windows-subpc` に合わせる）
3. Maintenance タブの GPU 行が「exporter未導入」→ 実際の使用率表示に変わることを確認

---

## Ollama マルチインスタンス対応

### 解決済み
- [x] OllamaClient を複数インスタンス対応に改修（least-connections 分配、フェイルオーバー）
- [x] `router.py` が `windows_agents` 設定から Ollama URL を自動構築
- [x] 設計ドキュメント作成（`docs/design/parallel_llm.md`）
- [x] `start_agent.bat` に `OLLAMA_HOST=0.0.0.0` を追加

### 残タスク
- [ ] MainPC の Ollama 動作確認（物理アクセスが必要）
- [ ] Phase 2: アプリケーション層の並列化（`asyncio.gather` による ContextSource 収集の並列化）

---

## その他

- [ ] `docs/design/inner_mind_improvements.md` の「5. 未実装：追加 ContextSource 候補」も参照
