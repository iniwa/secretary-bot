## 改善案
### image_gen / LoRA 学習 (Phase 4) — 実機疎通
- [ ] Phase 4 LoRA 学習の実機動作テスト（Main/Sub PC でのみ可能）
  - コード実装（A〜H）は 2026-04-20 に完了済: `src/units/lora_train/*` / `src/web/routes/lora_train.py` / `windows-agent/tools/image_gen/{wd14_tagger,kohya_train,lora_sync}.py`
  - 未検証: WebGUI `🎯 LoRA` タブからの 新規プロジェクト作成 → dataset drag-drop → WD14 タグ付け → TOML prepare → Agent sync → kohya 学習 SSE → checkpoint 昇格 の E2E
  - 詳細は `docs/image_gen/todo.md` Phase 4 参照

### auto-kirinuki（配信切り抜き / Phase 1）
- [ ] D8: 実機で `nas_mount.py` が `secretary-bot` 共有を再利用することの確認（Main/Sub PC 再開時）
- [ ] G1: ローカル型チェック / import 整合
- [ ] G2: ユニットテスト（Pi 側ユニット / Dispatcher ロジック）
- [ ] G3: 実機疎通（Main/Sub PC 上で Agent 起動 + Pi から enqueue → NAS outputs に EDL/MP4/transcript/highlights が揃うこと）
- [ ] G4: 旧リポジトリ `streamarchive-auto-kirinuki` への参考用コメント追加（削除しない）
  - コード実装（Phase A〜F）は 2026-04-20 に完了済
  - 詳細は `docs/auto_kirinuki/implementation_plan.md` Phase G セクション参照

### MainPCのollamaがCPUで動いている → **2026-04-22 解決**
- [x] ログを確認していると、明らかに思考時間が長すぎる

#### 2026-04-22 解決（SSH 作業）

**根本原因**（2つ同時発生）:
1. `start_agent.bat` が `taskkill /IM ollama.exe` しか実行しておらず **`ollama app.exe`（デスクトップ/トレイアプリ）は殺さなかった** → デスクトップアプリが :11434 を握り、start_agent.bat の `set` 環境変数が反映されない状態が継続
2. **Ollama インストールが破損**: `%LOCALAPPDATA%\Programs\Ollama\lib\ollama\` 配下に `mlx_cuda_v13` のみ、`cuda_v12` / `cuda_v13` / `rocm` / `vulkan` が欠落。upgrade.log に 2026-04-18 アップグレード時の `DeleteFile: The existing file appears to be in use` が記録されており、使用中のバイナリ置換が失敗して CUDA バックエンドが消えていた

**実施した対処**:
1. `ollama.exe` / `ollama app.exe` を taskkill
2. OllamaSetup.exe（v0.21.0）をサイレント再インストール → `cuda_v12` / `cuda_v13` / `rocm` / `vulkan` ディレクトリ復活
3. ユーザー Startup フォルダ（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`）から `Ollama.lnk` を削除 → デスクトップアプリの自動起動停止
4. `windows-agent/start_agent.bat` を修正: `taskkill /IM "ollama app.exe" /F` を追加（再発防御）
5. Windows Agent を再起動し、start_agent.bat の `set` 環境変数が ollama に届く状態で起動

**検証結果**:
```
inference compute id=GPU-df4d5b5f-... library=CUDA compute=8.9
name=CUDA0 description="NVIDIA GeForce RTX 4080" libdirs=ollama,cuda_v13
total="16.0 GiB" available="14.2 GiB"
OLLAMA_FLASH_ATTENTION:true / OLLAMA_KV_CACHE_TYPE:q8_0 / OLLAMA_MAX_LOADED_MODELS:1
```
- `gemma4:e2b` が `size_vram=7.21GB`（= モデル全量、100% GPU オフロード）でロード
- 推論速度 約 203 tokens/sec（CPU fallback 時の十数倍）

---

#### 2026-04-22 診断結果（Remote PC から調査）— 参考用（上の解決セクションで対処済み）

**症状**: `ollama ps` の PROCESSOR 列が `100% CPU`。モデル `gemma4:e2b` (7.7GB)、VRAM 空きは 8.5GB あるのに GPU を使わない。

**Ollama server.log（`%LOCALAPPDATA%\Ollama\server.log`）の決定的な行**:
```
inference compute id=cpu library=cpu ... total="47.1 GiB"  ← GPU 検出ゼロ
vram-based default context total_vram="0 B"                 ← VRAM = 0 B と認識
offloaded 0/36 layers to GPU                                ← 全 36 層 CPU
```

さらに `server config` ログに:
```
CUDA_VISIBLE_DEVICES:          (空)
OLLAMA_FLASH_ATTENTION:false
OLLAMA_KV_CACHE_TYPE:          (空)
OLLAMA_MAX_LOADED_MODELS:0
```
→ `start_agent.bat` で `set` した環境変数が **Ollama に届いていない**（後述の原因 1）。

**原因の仮説**:
1. **Ollama が `start_agent.bat` 経由ではなく別ルート（デスクトップアプリ / Windows 自動起動）で動いている**
   - `taskkill /IM ollama.exe /F` は効くが、デスクトップアプリがすぐ再起動して bat 起動のものと入れ替わっている可能性
2. **Ollama のバンドル CUDA ランタイム（`Ollama/lib/ollama/cuda_v12` 配下）が壊れているか不在**
   - `library=cpu` / `total_vram="0 B"` は CUDA DLL 未ロード時に出るパターン

**帰宅後の対処手順**（優先順・実際は SSH で実施）:
1. **タスクマネージャー → スタートアップ**タブで `Ollama` が有効になっていないか確認 → 有効なら無効化
2. システムトレイ（通知領域）のラマアイコンを右クリック → Quit で Ollama デスクトップアプリを完全終了
3. `start_agent.bat` を再実行し、WebGUI の **GPU Status → Ollama Server Log** を確認
   - 環境変数が反映されているか（`OLLAMA_FLASH_ATTENTION:true` など）
   - それでも `library=cpu` のままなら Ollama を最新版で再インストール（<https://ollama.com/download/windows>、既存モデルは保持される）
4. それでも直らない場合は `start_agent.bat` に以下を追加して永続化:
   ```bat
   setx OLLAMA_FLASH_ATTENTION 1 /M >NUL 2>&1
   setx OLLAMA_KV_CACHE_TYPE q8_0 /M >NUL 2>&1
   setx CUDA_VISIBLE_DEVICES 0 /M >NUL 2>&1
   setx OLLAMA_MAX_LOADED_MODELS 1 /M >NUL 2>&1
   ```
   併せて `set OLLAMA_DEBUG=1` を入れると CUDA DLL ロード試行が詳細ログに出るのでデバッグに有効。

**関連 WebGUI 機能**（この診断で追加、既にデプロイ済み）:
- **GPU Status** ページ（System グループ）
  - Live Status: `nvidia-smi` + `ollama ps` リアルタイム取得
  - Ollama Server Log: server.log の GPU 関連行ハイライト表示
  - Boot Logs: `start_agent.bat` が起動時に書く `gpu_status.log`


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
