# 改善・追加実装案

## 設計済み（設計書あり）

- [ ] **RSSフィーダー + ニュース機能** → `docs/design/rss_feeder_design.md`
  - 定期巡回・LLM要約・カテゴリ別ダイジェスト通知
  - ニュース機能はRSSフィードとして統合

- [ ] **アクティビティ判定** → `docs/design/activity_detection_design.md`
  - OBS・ゲームプロセス・Discord VCの複合判定
  - 重い処理（LLM要約等）の実行可否を制御

- [ ] **OBSディレクトリ管理** → `docs/design/obs_dir_manage_design.md`
  - 録画・リプレイ・スクショをゲーム名フォルダに自動整理
  - 2pc-obs プロジェクトからの移植・統合

- [ ] **いにわボイスのSTT** → `docs/design/stt_design.md`
  - Main PCマイク直接キャプチャ + kotoba-whisper（Sub PC）でバッチSTT
  - LLM要約 → ChromaDB保存、InnerMind ContextSourceとして活用

- [ ] **リマインダーの自然言語操作** → `docs/design/reminder_nlp_design.md`
  - エスカレーション間隔スヌーズ（30分→1時間→3時間→6時間）
  - LLMによる会話文脈解決 + 聞き返しフォールバック

- [x] **Input Relay 統合** → `docs/design/input_relay_integration_design.md`
  - git submodule + Windows Agent プロセス管理 + WebGUI統合
  - 実装済み（ロール判定、自動起動、死活監視、ログ表示）

## 実装のみ（設計不要）

- [ ] **WebGUI: モノローグログの追加**
  - モノローグ関連の各データ表示
  - LLMへ渡すデータをセクション別に閲覧（モノローグ毎に確認可）
  - キャッシュ中の情報の表示
  - モノローグの動作可否の判定表示（アクティビティ判定連携）

- [ ] **WebGUI / データ: Discordチャンネル情報の付与**
  - チャットログ・Logsなど各所にチャンネル名を表示
  - ミミへ渡すデータにもチャンネル情報を含める
  - `chat for text` は通話中の情報共有チャンネルになりがちなので区別が有用

- [ ] **天気機能: 地域指定の対応**
  - 定期通知で地域を指定できない（東京固定）問題の修正

## その他

- `docs/design/inner_mind_improvements.md` の「5. 未実装：追加 ContextSource 候補」も参照
