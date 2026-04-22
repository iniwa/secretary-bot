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

### MainPCのollamaがCPUで動いている
- [ ] ログを確認していると、明らかに思考時間が長すぎる

#### 2026-04-22 診断結果（Remote PC から調査）

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

**帰宅後の対処手順**（優先順）:
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
- [ ] SubPCのWoLが効かなかった


### （案）AI-ASMR
- [ ] irodoriTTSを使って