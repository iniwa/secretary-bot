## MainPC の Ollama が CPU で動く場合（再発時の対処）

**症状の確認**: WebGUI の **System → GPU Status** で Ollama Server Log を開き、以下のような行が出ていれば CPU fallback 状態:
```
inference compute id=cpu library=cpu ... total_vram="0 B"
offloaded 0/XX layers to GPU
```
また server config で `OLLAMA_FLASH_ATTENTION:false` など `start_agent.bat` の `set` が反映されていないのも典型症状。

**対処手順**（優先順）:
1. **タスクマネージャー → スタートアップ**タブで `Ollama` が有効になっていないか確認 → 有効なら無効化
2. システムトレイのラマアイコンを右クリック → Quit で Ollama デスクトップアプリを完全終了（`ollama app.exe` が :11434 を握っていると `start_agent.bat` の環境変数が届かない）
3. `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk` があれば削除
4. `start_agent.bat` を再実行し、WebGUI の **GPU Status → Ollama Server Log** を確認
   - `library=CUDA` になり環境変数が反映されていれば OK
5. それでも `library=cpu` のままなら Ollama を最新版で再インストール（<https://ollama.com/download/windows>、既存モデルは保持）
   - `%LOCALAPPDATA%\Programs\Ollama\lib\ollama\` 配下に `cuda_v12` / `cuda_v13` / `rocm` / `vulkan` が揃っているかも確認。`mlx_cuda_v13` のみでバックエンドが欠落しているケースあり（upgrade.log で使用中ファイル置換失敗が出ていると該当）
6. それでも直らない場合は `start_agent.bat` に以下を追加して環境変数を永続化:
   ```bat
   setx OLLAMA_FLASH_ATTENTION 1 /M >NUL 2>&1
   setx OLLAMA_KV_CACHE_TYPE q8_0 /M >NUL 2>&1
   setx CUDA_VISIBLE_DEVICES 0 /M >NUL 2>&1
   setx OLLAMA_MAX_LOADED_MODELS 1 /M >NUL 2>&1
   ```
   併せて `set OLLAMA_DEBUG=1` を入れると CUDA DLL ロード試行が詳細ログに出るのでデバッグに有効。
