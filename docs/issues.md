## 改善案

<!-- 2026-04-20: Claude Code 長時間セッション中（power unit / auto-kirinuki Phase C-D-F を自動進行） -->


### リモート開発環境
- [ ] Remote PC → Sub PC の VS Code Remote-SSH 接続（Claude Code を Sub PC 側で動作させる構成）
  - 手順・残作業は `docs/guides/remote_pc_subpc_vscode_access.md` を参照
  - 2026-04-20 (旧): Remote PC 側は対応不要（`~/.ssh/config` の `subpcssh` + `cloudflared` 既設）。Sub PC で OpenSSH Server / 公開鍵登録 / cloudflared Tunnel / VS Code + Node.js + Claude Code の導入が残作業
  - 2026-04-20 (更新): **Cloudflare Tunnel は Pi に集約する構成へ刷新**。Sub PC には cloudflared を入れず、Pi の cloudflared が TCP ingress `subpcssh.iniwach.com → tcp://192.168.1.211:22` で Sub PC sshd に中継する。`docs/guides/remote_pc_subpc_vscode_access.md` を全面書き直し、`scripts/setup_subpc_ssh.ps1` を新設

  **Sub PC 側の残作業**（現状調査結果ベース・ユーザーは `iniwa`（Administrators））
  - [x] OpenSSH Server インストール（サービス `sshd` 存在確認済 / Stopped・Manual）
  - [x] Node.js (v24.14.0) / VS Code (`D:\System\Microsoft VS Code`) / Claude Code (v2.1.114) 導入済
  - [ ] 管理者 PowerShell で `scripts\setup_subpc_ssh.ps1` 実行:
    - sshd を Automatic + 起動（`sshd_config` 自動生成）
    - ファイアウォールルール `OpenSSH-Server-In-TCP` を `RemoteAddress=LocalSubnet` で作成/更新
    - Remote PC 公開鍵（`ssh-ed25519 ... iniwa_remote_access`）を `administrators_authorized_keys` に追記 + ACL 設定（継承無効 / `Administrators:F` / `SYSTEM:F`）
    - `sshd_config` で `PubkeyAuthentication yes` / `PasswordAuthentication no` を保証
    - sshd 再起動
  - [ ] 誤って `cloudflared service install` 済みなら `cloudflared.exe service uninstall` で削除（Pi 経由構成では Sub PC に不要）

  **Pi 側の残作業**
  - [ ] Cloudflare ダッシュボード (`https://one.dash.cloudflare.com/` → Networks → Tunnels → Pi の既存 Tunnel) の **Public Hostnames** に追加:
    - Subdomain: `subpcssh` / Domain: `iniwach.com` / Service: `SSH` / URL: `192.168.1.211:22`
  - [ ] Pi の cloudflared が **config.yml モード**の場合のみ、`/etc/cloudflared/config.yml` の `ingress:` に同等エントリを追記し `sudo systemctl restart cloudflared`（トークン起動モードなら Pi 側作業不要）
  - [ ] Pi → Sub PC の LAN 疎通確認: `ssh iniwapi "nc -zv 192.168.1.211 22"`

  **Remote PC 側の残作業**
  - [ ] `~/.ssh/config` の `subpcssh` エントリの **`User` を `iniwa` に修正**（旧ドキュメントの `iniwaminipc` から）。参考設定:
    ```
    Host subpcssh
      HostName subpcssh.iniwach.com
      User iniwa
      ProxyCommand cloudflared access ssh --hostname %h
      IdentityFile ~/.ssh/id_ed25519
    ```
  - [ ] `cloudflared` 最新確認（v2026.2.0 以上）: `cloudflared --version`
  - [ ] 疎通確認: `cloudflared access ssh --hostname subpcssh.iniwach.com`（TCP が開けば OK・Ctrl+C で抜ける）
  - [ ] SSH 疎通: `ssh -o ConnectTimeout=10 subpcssh "hostname"` で Sub PC のホスト名が返ること
  - [ ] VS Code 拡張 `Remote - SSH` をインストールし `Remote-SSH: Connect to Host... → subpcssh` で接続
  - [ ] 接続先ターミナルで `claude --version` / `node --version` が表示され `claude` が起動すること

### image_gen / LoRA 学習 (Phase 4)
- [ ] LoRA 学習機能の C〜H マイルストーン（WD14 タグ付け / TOML テンプレ / kohya 学習 / 監視 UI / 手動昇格）
  - 進捗・残作業・実装方針は `docs/image_gen/todo.md` の Phase 4 セクション参照
  - B / B+（プロジェクト CRUD + dataset drag-drop upload）は 2026-04-20 に Pi 側コード実装まで完了。実機（Pi + Main/Sub PC + NAS）での疎通確認は未実施
  - C 以降は Windows Agent 側の subprocess 管理（kohya / WD14）と SSE 連携が必要なため、実機環境がある PC（Main/Sub PC）で再開する

### auto-kirinuki（配信切り抜き / Phase 1）
- [ ] 旧 `streamarchive-auto-kirinuki` を secretary-bot に統合し、Pi 司令塔 + Windows Agent 重処理の構成で切り抜きユニットを新設
  - 設計・実装計画・NAS 再編手順は `docs/auto_kirinuki/` 配下を参照
    - `design.md`（アーキテクチャ・DB スキーマ・API 仕様）
    - `implementation_plan.md`（Phase 1 タスク + 進捗トラッキング）
    - `nas_migration.md`（NAS 共有 `ai-image` → `secretary-bot` 再編手順）
  - NAS 方針: 新規 `secretary-bot` 共有を作り、配下に `ai-image/` と `auto-kirinuki/` を並置。image_gen の既存パスも変更が必要
  - Whisper モデルは初回ジョブ投入時に `warming_cache` で自動 NAS→ローカル SSD 同期
  - 1 エージェント 1 ジョブ固定（GPU 1 枚制約）。Sub PC を priority=1、Main PC を priority=2
  - 旧リポジトリ `C:/Users/yamatoishida/Documents/git/streamarchive-auto-kirinuki` は参考用として残置（削除しない）
  - Remote PC 環境では実機疎通不可のため、コード実装まで。疎通確認は Main/Sub PC で再開

### image_gen / プロンプト再現・表示
- [x] ギャラリー「この設定で再現」で、可能な範囲でセクション選択状態（プロンプト断片）も復元してほしい
  - 2026-04-20: クライアント側に `static/js/lib/decompose.js` を新設し、最終 positive/negative と DB の全セクションから「完全一致セクション」を逆算 → `chosen` に復元、部分一致や残余タグは positive/negative 入力欄に流す方針で実装。`gallery.js` の `handleReuse` で `GenerationAPI.listSections()` と `getJob` を並列フェッチして stash に `section_ids` を積み、`generate.js` の `checkStashPrefill` で `chosen` 反映。Extract ページの「🎨 この設定で生成へ」も同じ逆算ルートに統一
- [x] プロンプト表示は最初に「セクション断片」のまとまった状態を出し、ホバー or ボタンで生データ表示にしてほしい
  - 2026-04-20: `common.js` に共通ヘルパ `buildPromptBlock(label, text)` を export。`,\n` 境界で断片カードに分割した表示を既定とし、ヘッダの「📄 生データ」トグルで `<pre>` 全文表示に切替、「📋 コピー」で全文コピー。`openPromptModal`（lightbox 経由のプロンプト表示）と Extract ページの Positive/Negative の両方を同コンポーネントへ統一。CSS は `image_gen.css` に `.imggen-prompt-*` を追加

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

### power unit
- [x] 複数指示の対応
  - 「メインPCとサブPCの両方起動して」というチャットに対してエラー発生
  - LLMログにてリスト形式で出力されている
  - このリスト形式出来た時に、両方へWoLパケットを送ることの実装
  - 複数台に送る時、クールタイムを設ける（余裕を持って5秒ぐらいあけてもよい）
  - 2026-04-20: LLM抽出スキーマを `{"action","targets":[...]}` に拡張。`target` (単数) も後方互換で受理。`_run_sequential` で 5 秒クールタイム付き順次実行。wake/shutdown/restart/cancel/status いずれも複数対応（shutdown/restart は確認プロンプトで 1 回まとめて承認）