## 未実装項目サマリ
### 並列 LLM 最適化
- [ ] Ollama インスタンス別の成功率・レイテンシ追跡

## 改善案
### ACtivity
- [ ] `docs/design/activity_multi_pc_detection.md` を参照

### AI画像生成機能
- [ ] `image_gen_**.md` を参照。
  - `docs/design/` と `docs/setup/` にファイルがある
  - 計画済みなのであとは実装するだけ？


### zzz_disk  
- [ ] 高難易度編成モードの実装
  - 同時に複数部隊使うときのディスク割り当て計算用
  - このモード内で編成を組む時、別の部隊とのディスク･音動機の使い回しが不可能
  - 式輿防衛戦や臨界推演の部隊組のための機能

### LLM
- [ ] MainPCのollamaがCPU稼働してたかも？