## 改善案

### image_gen / LoRA 学習 (Phase 4) — 実機疎通
- [ ] Phase 4 LoRA 学習の実機動作テスト（Main/Sub PC でのみ可能）
  - コード実装（A〜H）は 2026-04-20 に完了済: `src/units/lora_train/*` / `src/web/routes/lora_train.py` / `windows-agent/tools/image_gen/{wd14_tagger,kohya_train,lora_sync}.py`
  - 未検証: WebGUI `🎯 LoRA` タブからの 新規プロジェクト作成 → dataset drag-drop → WD14 タグ付け → TOML prepare → Agent sync → kohya 学習 SSE → checkpoint 昇格 の E2E
  - 詳細は `docs/image_gen/todo.md` Phase 4 参照

### image_gen / API ドキュメント未確定事項
- [ ] `docs/image_gen/api.md` §12 の以下項目を実装時に確定させる
  - `preview` イベント送出頻度（ComfyUI の PreviewImage ノード設定に依存）
  - `/system/logs` の `follow=true` 時のバッファサイズ上限
  - `cache/sync` の NAS 並行読み出し本数（1GbE 上限を踏まえた自動スロットリング）
  - `/image/generate` の `timeout_sec` 未指定時の扱い（Pi 側で必ず埋めるか、Agent が既定値を持つか）

### auto-kirinuki（配信切り抜き / Phase 1）
- [ ] D8: 実機で `nas_mount.py` が `secretary-bot` 共有を再利用することの確認（Main/Sub PC 再開時）
- [ ] G1: ローカル型チェック / import 整合
- [ ] G2: ユニットテスト（Pi 側ユニット / Dispatcher ロジック）
- [ ] G3: 実機疎通（Main/Sub PC 上で Agent 起動 + Pi から enqueue → NAS outputs に EDL/MP4/transcript/highlights が揃うこと）
- [ ] G4: 旧リポジトリ `streamarchive-auto-kirinuki` への参考用コメント追加（削除しない）
  - コード実装（Phase A〜F）は 2026-04-20 に完了済
  - 詳細は `docs/auto_kirinuki/implementation_plan.md` Phase G セクション参照

### daily_diary（活動日記）後続タスク
- [ ] 旧 STT 要約（people_memory 上）の掃除スクリプト（任意）
  - A案適用後、STT 要約は ChromaDB の `stt_summaries` コレクションのみに残る方針
  - 切り替え前に people_memory へ保存された STT 要約は放置（自然減衰）でも実害なし。気になる場合のみ掃除
