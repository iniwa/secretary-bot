## 改善案

### リモート開発環境
- [ ] Remote PC → Sub PC の VS Code Remote-SSH 接続（Claude Code を Sub PC 側で動作させる構成）
  - 手順・残作業は `docs/guides/remote_pc_subpc_vscode_access.md` を参照
  - 2026-04-20: Remote PC 側は対応不要（`~/.ssh/config` の `subpcssh` + `cloudflared` 既設）。Sub PC で OpenSSH Server / 公開鍵登録 / cloudflared Tunnel / VS Code + Node.js + Claude Code の導入が残作業

### image_gen / LoRA 学習 (Phase 4)
- [ ] LoRA 学習機能の C〜H マイルストーン（WD14 タグ付け / TOML テンプレ / kohya 学習 / 監視 UI / 手動昇格）
  - 進捗・残作業・実装方針は `docs/image_gen/todo.md` の Phase 4 セクション参照
  - B / B+（プロジェクト CRUD + dataset drag-drop upload）は 2026-04-20 に Pi 側コード実装まで完了。実機（Pi + Main/Sub PC + NAS）での疎通確認は未実施
  - C 以降は Windows Agent 側の subprocess 管理（kohya / WD14）と SSE 連携が必要なため、実機環境がある PC（Main/Sub PC）で再開する

### zzz_disk
- [x] 「オススメステータス･ディスク（メモ）」が妄想エンジェルの3人しか入っていないため、全キャラへ適応してほしい
  - 2026-04-20: 既存 9 キャラ + 追加 39 キャラ = 全 48 キャラで `recommended_notes` を埋めた。codex（`docs/zzz_character_codex.md`）を主ソースとし、未収載だった 3 キャラ（ビビアン・バンシー / イヴリン・シェヴァリエ / アストラ・ヤオ）は codex にも追記した（スターズ・オブ・リラ、モッキンバード 陣営を新設）
- [x] 「推奨サブステ･ディスク」（フィルタリングに使用している方）が未記入のキャラに関して、ネットから取得･要約したデータを元に記入してほしい
  - 2026-04-20: 全 48 キャラの notes から「サブ優先」「ディスクセット」を機械抽出し `recommended_substats_json` / `recommended_disc_sets_json` を埋め直し（古い文字化けデータも修正）。併せて notes 中に残っていた EN 音動機名/機構用語を公式JP or カタカナ音写へ統一（codex line 25 の 別表記「スターオブリリム」も除去）
- [x] 「オススメ編成（メモ）」というセクションを追加してほしい
  - このセクションは自由記入欄。「オススメステータス（メモ）と同じようなイメージ
  - 2026-04-20: `zzz_characters.recommended_team_notes` カラム追加＋ PUT API `/api/characters/{id}/recommended-team-notes` 追加＋ `character_detail.js` に「🧩 オススメ編成（メモ）」セクションを追加。Pi 側は BOT 再起動で schema migration が走って有効化される
- [x] 推奨ディスクメインステ（4/5/6号位）+ 構造化おすすめ編成（複数可）
  - 2026-04-20: `zzz_characters.recommended_main_stats_json`（slot→list[str]）と `recommended_teams_json`（`[{members, note}]`）を追加。PUT API `/recommended-main-stats` と `/recommended-teams` 追加。`character_detail.js` にスロット毎チップ編集と編成編集モーダル、`characters.js` に推奨メインステ絞り込み、スワップモーダルに複数選択式メインステフィルタ（推奨値で初期選択）を実装
  - 全 48 キャラの notes 「■ ディスクメイン」セクションから `recommended_main_stats_json` を機械抽出して反映（EN 略記 `ATK%`/`EN回復`/`炎属性ダメ%` 等は JP 正式名に正規化）。編成データは UI で手動登録運用
  - 備考: notes の号位表記「4番/5番/6番」は内部 disc.slot と一致（schema の `SLOT_ALLOWED_MAIN_STATS` 基準）
