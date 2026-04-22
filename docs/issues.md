## 改善案
### image_gen / LoRA 学習 (Phase 4) — 実機疎通
- [ ] Phase 4 LoRA 学習の実機動作テスト（Main/Sub PC でのみ可能）
  - コード実装（A〜H）は 2026-04-20 に完了済: `src/units/lora_train/*` / `src/web/routes/lora_train.py` / `windows-agent/tools/image_gen/{wd14_tagger,kohya_train,lora_sync}.py`
  - 未検証: WebGUI `🎯 LoRA` タブからの 新規プロジェクト作成 → dataset drag-drop → WD14 タグ付け → TOML prepare → Agent sync → kohya 学習 SSE → checkpoint 昇格 の E2E
  - 詳細は `docs/image_gen/todo.md` Phase 4 参照

### auto-kirinuki（配信切り抜き / Phase 1）
- [ ] D8: 実機で `nas_mount.py` が `secretary-bot` 共有を再利用することの確認（Main/Sub PC 再開時）
- [x] G1: ローカル型チェック / import 整合（2026-04-23 完了 — Pi/Agent/agent.py/web.app 全 import OK、Database メソッド揃いを確認）
- [x] G2: ユニットテスト（Pi 側ユニット / Dispatcher ロジック）（2026-04-23 完了 — `tests/units/clip_pipeline/` に 35 テスト追加、全体 pytest 75 passed）
- [ ] G3: 実機疎通（Main/Sub PC 上で Agent 起動 + Pi から enqueue → NAS outputs に EDL/MP4/transcript/highlights が揃うこと）
- [x] G4: 旧リポジトリ `streamarchive-auto-kirinuki` への参考用コメント追加（2026-04-23 完了 — `CLAUDE.md` / `CLAUDE_ja.md` / `clip-pipeline-design.md` / `memo.md` の先頭に移行バナーを追加。旧リポは削除せず読み取り専用で残置、コミットは未実施）
  - コード実装（Phase A〜F）は 2026-04-20 に完了済
  - 詳細は `docs/auto_kirinuki/implementation_plan.md` Phase G セクション参照

### MainPC の Ollama が CPU で動く場合（再発時の対処）
- 詳細は `docs/issues_ollama.md` を参照

### SubPCが遠隔起動できなかった
- [x] SubPCのWoLが効かなかった

#### 2026-04-22 原因調査と対応

**症状**: `2026-04-22 02:42` に power-unit 経由で SubPC をシャットダウン → 次回起動（18:03）まで約 15 時間空き、WoL で起こせず物理電源で起動した形跡。

**診断結果**（SubPC）:
- `HiberbootEnabled=0`（Fast Startup 無効）は前回対応通り維持されていた
- `*WakeOnMagicPacket` / `S5WakeOnLan` / `*WakeOnPattern` はすべて `1`（有効）
- `powercfg /devicequery wake_armed` に Realtek 2.5GbE が含まれている
- **`WolShutdownLinkSpeed=0`（10 Mbps 優先）** ← 本命の容疑者。シャットダウン時に NIC が 10Mbps へネゴシエーションし直す際、スイッチ側が 10Mbps リンクを維持できないとマジックパケットが届かない
- `EnableWakeOnLan` キー値が空（Realtek 独自キー、明示未設定）
- Realtek ドライバが 2019-05-10 版（`rt640x64.sys` v10.35.510.2019）と古い

**適用済み対応**:
1. Sub/Main 両 PC で `WolShutdownLinkSpeed` を `2`（Not Speed Down）に変更
2. `HiberbootEnabled=0` を両 PC で再明示（Main PC 側は今回キー値が 0 でなかったため予防的に修正）
3. `windows-agent/agent.py` に `_ensure_wol_ready()` を追加、lifespan 起動時と `/shutdown` 直前に必須 WoL 設定を自動是正（Windows Update / ドライバ更新による設定ドリフト対策）

**残タスク（手動）**:
- [ ] BIOS/UEFI 側の WoL 設定を両 PC で確認（SubPC / MainPC）
  - `Power > ErP Ready` = **Disabled**（Enabled だと S5 で AC カット相当になり WoL 不可）
  - `Wake On LAN` / `Power On By PCI-E / PCI` = **Enabled**
  - `Deep Sleep` / `Deep Sleep Control` = **Disabled**
- [ ] Realtek NIC ドライバを最新版に更新（任意・影響範囲大）
  - SubPC: RTL8125（2.5GbE）、MainPC: RTL8126 相当（5GbE）
  - https://www.realtek.com/Download/List?cate_id=584 から最新 `Win10 Auto Installation Program` を取得
  - 更新後は `_ensure_wol_ready()` が自動で NIC 設定を復元するが、念のため `Get-NetAdapterAdvancedProperty` で検証
  - 現ドライバ（2019 年版）は `Get-NetAdapterPowerManagement` が System Error 31 を返すため電源管理 API 連携が壊れており、GUI の電源管理タブからの制御が不安定な可能性がある


### （案）AI-ASMR
- [ ] irodoriTTSを使って

### キャッシュ消去モード
- [ ] キャッシュ消去モードの追加
