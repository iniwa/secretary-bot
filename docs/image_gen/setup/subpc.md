# SubPC 画像生成セットアップガイド

> 対象機: **Sub PC**（Windows 11 / Ryzen 9 5950X / **RTX 5060 Ti 16GB**（CUDA sm_89）/ 64GB RAM / **IP 192.168.1.211**）
> 位置付け: AgentPool の priority 2 側。MainPC と同一プリセットを同一パラメータで処理できる互換性を確保する。
> 前提: **`docs/image_gen/setup/mainpc.md` が完了していること**（本書は差分中心）。共通手順は MainPC ガイド側の節番号を参照する。
> 関連: [`../design.md`](../design.md), [`../preset_compat.md`](../preset_compat.md), [`../nas_setup.md`](../nas_setup.md), [`../api.md`](../api.md)

---

## 1. MainPC との違い要約

| 項目 | MainPC | SubPC（本書） |
|---|---|---|
| GPU | RTX 4080 16GB | **RTX 5060 Ti 16GB** |
| CUDA Compute Capability | sm_89 | **sm_89**（同一） |
| VRAM | 16GB | 16GB（同量） |
| Agent role | `main` | **`sub`** |
| IP | 192.168.1.210 | **192.168.1.211** |
| 既存ワークロード | なし（新規導入） | **OBS 管理 + STT 推論（Whisper）**が常駐 |
| kohya_ss | 任意 | **必須**（両 PC に入れる方針 / `kohya.enabled=true`） |
| ComfyUI Manager snapshot | **正本を出力する側** | **snapshot を適用する側** |

要点:

- sm_89 同士なので capability の差分は基本出ない想定。ただし **PyTorch の sm_89 対応ビルド**を明示的に入れる必要がある（§2）。
- SubPC は `_detect_role()` が IP 192.168.1.211 から `role=sub` を自動判定（`windows-agent/agent.py:88-111`）。`role=sub` では OBS 管理 / Whisper 推論 / NAS マウントが起動する。
- **VRAM 競合**: OBS エンコード（NVENC）+ Whisper 推論 + ComfyUI 生成が重なると VRAM/エンコーダが奪い合う。§11 で緩和策を明記。

---

## 2. 前提チェック

- [ ] Windows 11 が最新で、NVIDIA ドライバも MainPC と**同系列のバージョン**に揃っている（ドライバ版差は `../preset_compat.md` §6 で微小差の原因になる）
- [ ] PowerShell で `nvidia-smi` が通り、`GeForce RTX 5060 Ti` と CUDA 12.x が表示される
- [ ] Python 3.11 が `py -3.11` で起動できる
- [ ] `git`, `ffmpeg` が PATH に通っている
- [ ] `N:` ドライブが未使用、または旧マッピングを `net use N: /delete` で解除済み
- [ ] **SubPC 固有**: OBS / STT が現状動作している（`start_agent.bat` 実行時のログに `[Agent] Role: sub` と OBS 接続ログが出る状態）。破壊しないように本作業を進める

### 2.1 PyTorch の sm_89 対応確認（SubPC 要注意）

RTX 5060 Ti（Blackwell 世代ではなく Ada Lovelace リフレッシュ、sm_89）は PyTorch 2.2 以降の安定版でサポートされているが、**ComfyUI 同梱 venv** や **kohya_ss の `requirements.txt`** が指定するバージョンが古いと `CUDA error: no kernel image is available for execution on the device` が出る。必要なら **nightly（cu124 以降）** を入れる。

- [ ] PowerShell で以下を実行して CUDA が使えることを確認

```powershell
py -3.11 -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_capability(0))"
```

- [ ] 期待出力: `2.x.x True (8, 9)`
- [ ] `(8, 9)` が出ない、もしくは `cuda.is_available()` が `False` の場合:

```powershell
# ComfyUI / kohya の venv をアクティベートしてから
pip install --upgrade --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu124
```

- [ ] Stable 版で sm_89 が通るなら nightly は不要（MainPC と揃えるのが第一優先）

### 2.2 role 判定の確認

- [ ] `windows-agent/agent.py` の `_detect_role()` が IP 192.168.1.211 を `sub` に割り当てる既定マップを持っていることを確認
- [ ] 起動時に `[Agent] Role: sub` が出ることをこの後の §8 で確認

> `AGENT_ROLE_MAP` 環境変数で上書きも可能だが、通常は不要。

---

## 3. ディレクトリ・ComfyUI・kohya_ss

基本手順は **MainPC ガイド §3（ディレクトリ作成〜ComfyUI 導入）および §4（kohya_ss 導入）と同じ**。MainPC ガイドの該当節を上から順に実行すること。

差分のみ:

- [ ] インストール先ドライブ: `C:\secretary-bot\`（MainPC と同じドライブレター規約）
- [ ] ローカルキャッシュ: `C:\secretary-bot-cache\`（MainPC は別 PC の別物理 SSD なのでそのまま `C:` を使う）
- [ ] `kohya.enabled=true` を **必ず**（両 PC 導入方針。SubPC だけ学習役、のような役割分担は現時点で想定していない）
- [ ] ComfyUI venv / kohya venv それぞれで §2.1 の CUDA 確認を **再実行**（venv ごとに入ってる torch が違う）

---

## 4. custom_nodes の snapshot 同期（重要）

SubPC の custom_nodes は **MainPC を正本として追従**する。`../preset_compat.md` §5.2 の方針に従う。

### 4.1 MainPC 側（snapshot 出力）

> MainPC ガイド側にも同じ手順があるはず。未実施なら先にそちらを済ませる。

- [ ] MainPC の ComfyUI を起動 → ComfyUI Manager → **Snapshot Manager** → `Save snapshot`
- [ ] 出力ファイル `ComfyUI/custom_nodes/ComfyUI-Manager/snapshots/<date>_<time>.json` を確認
- [ ] NAS に転送: `copy <file> N:\snapshots\<date>.json`

### 4.2 SubPC 側（snapshot 適用）

- [ ] NAS から snapshot を取得
  ```powershell
  Copy-Item N:\snapshots\<date>.json C:\secretary-bot\comfyui\custom_nodes\ComfyUI-Manager\snapshots\
  ```
- [ ] ComfyUI を起動 → Manager → **Snapshot Manager** → 対象 snapshot を選び `Restore`
- [ ] 適用後、ComfyUI を再起動
- [ ] 反映確認: `GET /capability` を叩き `custom_nodes[].commit` が MainPC と一致することを §9 で照合

### 4.3 Phase 2 で自動化予定

- Pi からの `POST /comfyui/update`（`../api.md` 予定仕様）で NAS 上の最新 snapshot を SubPC が自動取り込みする想定。現状は **手動同期**でよい。

---

## 5. モデル・LoRA 共通化

- [ ] `C:\secretary-bot\comfyui\extra_model_paths.yaml` を MainPC と**同一内容**で配置
  - NAS の `N:\models\` 配下（checkpoints / loras / vae / embeddings / controlnet / upscale_models / clip）を参照
  - 書式は MainPC ガイド §5 の例と同一でコピーして問題ない
- [ ] ローカルキャッシュ先 `C:\secretary-bot-cache\` は SubPC 固有で、MainPC と共有しない（LRU 上限 100GB は `agent_config.yaml` 側で制御）
- [ ] NAS 側 `outputs/` は両 PC とも書き込み可。`YYYY-MM/YYYY-MM-DD/` 規約は MainPC と同じ
- [ ] SHA256 サイドカー（`*.sha256`）は NAS 上の正本に既に存在する前提。新規モデルを SubPC から配置しないこと（管理者操作）

---

## 6. Windows Agent 設定

`windows-agent/config/agent_config.yaml` を編集。

- [ ] `image_gen:` ブロックは `agent_config.yaml.example` の雛形を **MainPC と同じ構造**で転記
  - `root: "C:/secretary-bot"`
  - `cache: "C:/secretary-bot-cache"`
  - `comfyui.host/port: 127.0.0.1 / 8188`
  - `kohya.enabled: true` （SubPC でも明示 true）
  - `cache_lru.max_size_gb: 100`
  - `nas.share: "ai-image"`, `nas.mount_drive: "N:"`（MainPC と同一）
- [ ] `nas.host` は **`.env` 経由**で上書き（`NAS_HOST`）
- [ ] **既存の `obs_file_organizer:` は触らない**（現状動いている設定のまま）
- [ ] **既存の `stt:` は触らない**（SubPC は Whisper 推論担当側の設定のまま）
- [ ] `windows_agents:` の Main PC 参照エントリ（`192.168.1.210` / `role: main`）も維持

### 6.1 `.env` 追記

- [ ] `windows-agent/.env` に以下を追記（MainPC と同キー）:

```dotenv
NAS_HOST=192.168.1.xx
NAS_USER=ai-image-rw
NAS_PASSWORD=********
SECRETARY_BOT_ROOT=C:/secretary-bot
SECRETARY_BOT_CACHE=C:/secretary-bot-cache
```

---

## 7. NAS マウント

手順は MainPC と完全に同一。ドライブレターも `N:` で統一する。詳細は [`../nas_setup.md`](../nas_setup.md) §4.2 を参照。

- [ ] `New-SmbMapping -LocalPath "N:" -RemotePath "\\$NAS_HOST\ai-image" -Persistent $true`
- [ ] `Get-ChildItem N:\` が通ること
- [ ] `Get-ChildItem N:\models\checkpoints\` で MainPC と**同じ一覧**が返ること（NAS 経由なので当然同じになる）
- [ ] `start_agent.bat` 先頭に `net use N: \\...\ai-image /persistent:yes` を冪等に入れる運用は MainPC と揃える

---

## 8. 起動

- [ ] `windows-agent\start_agent.bat` を実行
- [ ] コンソール先頭に `[Agent] Role: sub` が出ることを確認
- [ ] `image_gen init failed: ...` が **出ていない**こと
- [ ] 既存機能が壊れていないことの併行確認:
  - [ ] OBS WebSocket 接続ログが通常どおり出ている（`obs_file_organizer.enabled=true` の想定動作）
  - [ ] STT が `model.unload_after_minutes` の遅延ロード構成で起動できる（`stt.enabled=true` のままであれば）
- [ ] ログに `_mount_nas()` 由来の NAS マウント成功/失敗（`agent.py:124`）が出ること

---

## 9. capability 照合（MainPC ⇄ SubPC）

`../preset_compat.md` §3 の差分チェック表に沿って比較する。

- [ ] 両 PC の Agent Token を `.env` から取得し、Pi もしくは作業用ターミナルから:

```powershell
$env:TOKEN = "<agent_token>"
curl -H "X-Agent-Token: $env:TOKEN" http://192.168.1.210:7777/capability > main.json
curl -H "X-Agent-Token: $env:TOKEN" http://192.168.1.211:7777/capability > sub.json
```

- [ ] 以下 3 点を**厳密一致**で確認:
  - `comfyui_version`
  - `models[*].filename`（NAS 共有なので同一になるはず）
  - `custom_nodes[*].commit`（snapshot 適用後は揃うはず）
- [ ] `gpu_info.cuda_compute == 8.9`（両 PC）
- [ ] `gpu_info.vram_total_mb == 16384`（両 PC）
- [ ] `gpu_info.vram_free_mb` は実行時値なので差分 OK
- [ ] 差分が出た場合の対処:
  - `comfyui_version` 不一致 → 先行側を退行させ揃える。揃うまで該当プリセットを `workflows.main_pc_only=1` で一時隔離
  - `custom_nodes[].commit` 不一致 → §4 の snapshot 同期をやり直す
  - `models[]` 欠損 → NAS 側で配置確認、`N:` マウント再確認。SubPC のローカルキャッシュ問題なら `C:\secretary-bot-cache\` をクリアして再ダウンロード

---

## 10. 動作確認

- [ ] SubPC 単体での疎通

```powershell
curl -H "X-Agent-Token: $env:TOKEN" http://192.168.1.211:7777/health
curl -H "X-Agent-Token: $env:TOKEN" http://192.168.1.211:7777/capability
```

- [ ] Pi 側の `agents:` 設定にこの SubPC（`192.168.1.211`, priority 2 など）が登録済みであること。詳細は **Pi 側セットアップガイド**（別途作成予定）で扱う範囲。本書では「登録されている状態」を確認するに留める
- [ ] Pi から `curl` で SubPC の `/capability` が返ることを確認（Pi → SubPC のファイアウォール / IP 許可の最終チェック）
- [ ] 代表プリセット `t2i_base` を Pi Dispatcher 経由で SubPC に流した際、OOM なく完了することを軽く確認（Phase 1 完了後の知覚的同等性検証は `../preset_compat.md` §4）

---

## 11. トラブルシュート（SubPC 固有）

### 11.1 `CUDA error: no kernel image ... sm_89`

- [ ] §2.1 の PyTorch sm_89 対応チェックを再実行
- [ ] ComfyUI 同梱 venv / kohya venv それぞれで `torch.cuda.get_device_capability(0) == (8,9)` を確認
- [ ] 必要なら nightly（cu124）を入れる

### 11.2 OBS エンコードと生成の VRAM 競合

RTX 5060 Ti 16GB に OBS NVENC + Whisper (float16) + ComfyUI SDXL 推論が同時にぶつかると VRAM が逼迫する。

- [ ] 生成中は OBS のビットレート / 出力解像度を一時的に下げる運用
- [ ] STT の `unload_after_minutes` を短めにして Whisper を早期アンロード
- [ ] ComfyUI の `--lowvram` や tile VAE は最終手段（`../preset_compat.md` §6 のとおり、VAE タイリング差は画素差の原因になる）
- [ ] それでも厳しければ該当プリセットを一時的に `main_pc_only=1` に降格

### 11.3 OBS との NVENC セッション数上限

- [ ] OBS の録画 + 配信 + ComfyUI が NVENC を奪う場合、ドライバの「NVENC セッション数パッチ」有無を確認
- [ ] 純 CUDA 側（ComfyUI）は NVENC とは別枠だが、ドライバ状態が崩れると両方落ちることがある。その場合は Agent 再起動 → NVIDIA ドライバ再起動の順で復旧

### 11.4 Whisper と同時にメモリ不足

- [ ] システム RAM 64GB のうち、Whisper 推論 + ComfyUI モデルロード + ブラウザ等の常駐でスワップが走ると著しく遅くなる
- [ ] `cache_lru.max_size_gb` を下げる、または不要常駐を止める

### 11.5 snapshot 適用後も `custom_nodes[].commit` が揃わない

- [ ] 該当 custom_node を手動で `git checkout <commit>` → `pip install -r requirements.txt` → ComfyUI 再起動
- [ ] それでも合わない（依存衝突）なら当該プリセットを `main_pc_only=1` で隔離（`../preset_compat.md` §5.1）

---

## 12. 次ステップ

- Pi 側ディスパッチャの登録と AgentPool priority 設定 → **Pi セットアップガイド**（別途作成予定、`docs/image_gen/setup/pi.md`）
- 互換性検証（seed 固定・SSIM チェック）は Phase 1 完了後に [`../preset_compat.md`](../preset_compat.md) §4 に従って実施
- Phase 2: snapshot 自動同期（`POST /comfyui/update`）の実装
