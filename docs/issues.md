# 改善・追加実装案

詳細は `docs/design/inner_mind_improvements.md` を参照。

## 未実装項目サマリ

### InnerMind 改善
- [ ] Discordアクティビティ検出（Streaming/Game/Spotify/Custom）と収集/思考分離
- [ ] 追加 ContextSource: GitHub活動 / X・Twitter / Webニュース（Tavily）

### 並列 LLM 最適化
- [ ] Ollama インスタンス別の成功率・レイテンシ追跡
- [ ] GPU メモリ使用量に基づく動的ルーティング（`nvidia_gpu_exporter` 前提）
