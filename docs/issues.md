## 改善案

### image_gen / LoRA 学習 (Phase 4) — 実機疎通
- [ ] Phase 4 LoRA 学習の実機動作テスト（Main/Sub PC でのみ可能）
  - コード実装（A〜H）は 2026-04-20 に完了済: `src/units/lora_train/*` / `src/web/routes/lora_train.py` / `windows-agent/tools/image_gen/{wd14_tagger,kohya_train,lora_sync}.py`
  - 未検証: WebGUI `🎯 LoRA` タブからの 新規プロジェクト作成 → dataset drag-drop → WD14 タグ付け → TOML prepare → Agent sync → kohya 学習 SSE → checkpoint 昇格 の E2E
  - 詳細は `docs/image_gen/todo.md` Phase 4 参照

### image_gen / API ドキュメント未確定事項
- [x] `docs/image_gen/api.md` §12 の以下項目を実装時に確定させる（2026-04-21 確定）
  - preview: Phase 1 は送出無効、Phase 2+ で 500ms スロットリング・最大 10 件/ジョブ・JPEG 256px
  - `/system/logs` follow=true: source 別 deque（agent/comfyui 500, kohya 600, setup 400）、接続キュー 1000 件、溢れは `log_dropped` イベント
  - `cache/sync` 並行本数: Phase 1 は直列、Phase 2 で Semaphore(2)（config `image_gen.cache.sync_concurrency` 1〜4）
  - `timeout_sec` 未指定: Pi 側で `workflows.default_timeout_sec` を必ず埋める + Agent 側 300 秒フォールバック（二段構え）
  - Phase 2+ 実装時の反映先: §12.1/12.3 の Phase 2 挙動を router/workflow_runner/config に落とす

### auto-kirinuki（配信切り抜き / Phase 1）
- [ ] D8: 実機で `nas_mount.py` が `secretary-bot` 共有を再利用することの確認（Main/Sub PC 再開時）
- [ ] G1: ローカル型チェック / import 整合
- [ ] G2: ユニットテスト（Pi 側ユニット / Dispatcher ロジック）
- [ ] G3: 実機疎通（Main/Sub PC 上で Agent 起動 + Pi から enqueue → NAS outputs に EDL/MP4/transcript/highlights が揃うこと）
- [ ] G4: 旧リポジトリ `streamarchive-auto-kirinuki` への参考用コメント追加（削除しない）
  - コード実装（Phase A〜F）は 2026-04-20 に完了済
  - 詳細は `docs/auto_kirinuki/implementation_plan.md` Phase G セクション参照

### daily_diary（活動日記）後続タスク
- [x] 旧 STT 要約（people_memory 上）の掃除スクリプト
  - `scripts/cleanup_stt_in_people_memory.py`（dry-run デフォルト、`--apply` で実削除）
  - 2026-04-21 に Pi 上で実行し、`source=stt` の 16 件を削除（people_memory 91 → 75）
  - A案適用後は `stt_summaries` ChromaDB コレクションのみに要約が残る
