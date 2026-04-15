# 改善・追加実装案

詳細は `docs/design/inner_mind_improvements.md` を参照。

## 未実装項目サマリ

### 並列 LLM 最適化
- [ ] Ollama インスタンス別の成功率・レイテンシ追跡

## 改善案
### ACtivity
- [ ] Input-Relayから操作状況を読み取り、MainPCとSubPCのどちらを触っているのかを認識
  - SubPCでVS CodeやUnity等を使う機会があるので、それも記録する