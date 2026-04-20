## 改善案

### リモート開発環境
- [ ] Remote PC → Sub PC の VS Code Remote-SSH 接続（Claude Code を Sub PC 側で動作させる構成）
  - 手順・残作業は `docs/guides/remote_pc_subpc_vscode_access.md` を参照
  - 2026-04-20: **Cloudflare Tunnel は Pi に集約する構成へ刷新**。Sub PC には cloudflared を入れず、Pi の cloudflared が TCP ingress `subpcssh.iniwach.com → tcp://192.168.1.211:22` で Sub PC sshd に中継する。Sub PC 側 / Pi 側の初期セットアップは完了済

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
- [ ] Phase G3: 実機疎通テスト（Main/Sub PC でのみ可能）
  - コード実装（Phase C/D/E/F）は 2026-04-20 に完了済
  - 詳細は `docs/auto_kirinuki/implementation_plan.md` の Phase G セクション参照
