## 未実装項目サマリ
## 改善案
### ACtivity
- [x] `docs/design/activity_multi_pc_detection.md` を参照
  - Phase A/B + C-1/C-4 は先行実装済み
  - C-2 (WebGUI: Main/Sub FG トグル + 両PC同時操作表示) / C-3 (daily_summary の PC 別集計) も実装完了
  - C-5 (habit_detector) は「大きな変更は不要」のため現状維持

### AI画像生成機能
- [ ] `image_gen_**.md` を参照。
  - `docs/design/` と `docs/setup/` にファイルがある
  - リモート環境下である程度実装済み
  - 計画済みなので、あとは実機で実稼働環境を整えてから


### zzz_disk  
- [x] 音動機の音動機効果の取得･表示
  - HoYoLAB Battle Chronicle から effect_title/description を同期済み
  - 説明文は `<details>` で展開可能な UI に改善
- [x] キャラクターの各スキル説明の取得･表示
  - HoYoLAB API ではスキル情報を返さないため、手動入力フォームを実装
  - `zzz_characters.skills_json` / `skill_summary` カラム + モーダルエディタ
- [x] 編成モードの実装
  - `zzz_team_groups` / `zzz_teams` / `zzz_team_slots` テーブル追加
  - 普段使い/危局: 単独 3 人部隊
  - 高難易度グループ: 1 グループ最大 10 部隊（式輿防衛戦/臨界推演用）
  - ディスク使い回しを自動検知（チーム内 / グループ横断）
- [x] ゲーム画面のキャプチャからディスク情報を抜き出す
  - Windows Agent 側 capture/extract + Pi 側 ジョブキュー SSE UI は実装済み
  - プロンプトのステータス名を DB 制約に合わせて修正（「雷ダメージ%」→「電気属性ダメージ%」等）
  - 実稼働は VLM モデル（`tools.zzz_disc.vlm_model` 設定）の環境構築後

### LLM
- [ ] MainPCのollamaがCPU稼働してたかも？

### 並列 LLM 最適化
- [x] Ollama インスタンス別の成功率・レイテンシ追跡
  - `OllamaClient._instance_stats` にプロセス内メトリクスを記録（成功数・失敗数・平均/直近レイテンシ・最終エラー）
  - `/api/ollama-status` の `instances[].stats` で公開、メンテナンス画面で表示
  - 永続化は未実装（プロセス再起動でリセット）。長期傾向が必要になったら DB 化を検討