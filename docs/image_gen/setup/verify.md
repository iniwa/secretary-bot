# 画像生成 E2E 動作確認 & トラブルシュート

> 対象: Main PC / Sub PC / Raspberry Pi / NAS のセットアップ完了後の疎通検証と失敗時の切り分け。
> 関連:
> - [`../design.md`](../design.md) — 状態機械・エラー階層
> - [`../api.md`](../api.md) — API 仕様、エラーコード対応表
> - [`../preset_compat.md`](../preset_compat.md) — capability 照合
> - [`../nas_setup.md`](../nas_setup.md) — NAS セットアップ
> - [`mainpc.md`](mainpc.md) / [`subpc.md`](subpc.md) / [`pi.md`](pi.md) — 各ホストのセットアップ
> 版: 2026-04-15（初版・Phase 1 向け）

---

## 0. 前提と使い方

本書は **Claude Code が自律的にチェックリストを順に実行し、詰まったら §3 以降へジャンプして切り分けできる** 粒度で書かれている。ユーザーに確認が必要な値は `[要ユーザー確認]` と明示する。

### 0.1 用意しておく値

以下は本書のコマンド例で使用する変数。各ホストでシェル変数に入れておく。

| 変数 | 例 | 由来 |
|---|---|---|
| `$T` / `$env:T` | Agent シークレット | Pi / PC の `.env` `AGENT_SECRET_TOKEN` |
| `$MAIN` | `192.168.1.210` | Main PC の IP |
| `$SUB` | `192.168.1.211` | Sub PC の IP |
| `$PI` | `192.168.1.xxx` [要ユーザー確認] | Raspberry Pi の IP |
| `$NAS_MNT` | `/mnt/ai-image`（Pi） / `Z:\`（Win） | NAS マウントポイント |
| `$WEB` | `http://<pi>:<port>` [要ユーザー確認] | WebGUI URL（Basic 認証あり） |

### 0.2 シェルの明示

- bash（Pi/Linux）: コードブロックに `bash` と明記
- PowerShell（Main/Sub PC）: `powershell`
- cmd（バッチ経由）: `cmd`

bash と PowerShell で文法が異なるため、**必ずどちら向けか確認してから実行する**。

---

## 1. E2E 正常系チェックリスト

全段階をこの順で上から実行。どこかで失敗したら該当節の「失敗時の参照節」へジャンプし、治ったら次段階に進む。

| # | 段階 | 実行コマンド / 操作 | 合格基準 | 失敗時の参照節 |
|---|---|---|---|---|
| 1 | Windows Agent 疎通（Main/Sub） | bash: `curl -H "X-Agent-Token: $T" http://$MAIN:7777/health` / `http://$SUB:7777/health` | HTTP 200、`{"status":"ok","version":"...","uptime_sec":N}` | §3.1 / §3.8 / §3.9 |
| 2 | Capability 取得（Main/Sub） | bash: `curl -H "X-Agent-Token: $T" http://$MAIN:7777/capability` / 同 Sub | HTTP 200、`comfyui_available=true`、`gpu_info.name`/`vram_total_mb`/`cuda_compute` が埋まる、`models[]` に `chenkinNoobXL_v05.safetensors` が存在 | §3.2 / §3.11 |
| 3 | NAS 共有の相互可視性 | Pi bash: `ls $NAS_MNT/outputs` / Main PowerShell: `Get-ChildItem Z:\outputs` | 双方で同じ `YYYY-MM` フォルダが見える。片方で作ったテストファイルが他方から読める | §3.3 |
| 4 | プリセット seed（Pi） | Pi bash: `sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 "SELECT name, category FROM workflows;"` | 少なくとも `t2i_base` が存在。`PRAGMA user_version;` が `>= 26` | §3.4 |
| 5 | ジョブ投入 & 状態遷移 | WebGUI `Image Gen` から生成、または Pi から `curl -X POST -u <basic> $WEB/api/image/generate -H "Content-Type: application/json" -d '{"positive":"1girl, forest","preset":"t2i_base"}'` | `job_id` が返る。SSE `/api/image/jobs/stream` または polling で `queued → dispatching → (warming_cache →)? running → done` と遷移 | §3.5 |
| 6 | NAS 成果物 | Pi bash: `ls $NAS_MNT/outputs/$(date +%Y-%m)/$(date +%Y-%m-%d)/` | 今の生成に対応する PNG が存在。Main からも `Get-ChildItem Z:\outputs\YYYY-MM\YYYY-MM-DD` で同ファイルが見える | §3.6 |
| 7 | WebGUI ギャラリー表示 | ブラウザで `$WEB` → `Image Gen` → `Gallery` タブ | サムネイルが表示され、クリックで拡大表示。`/api/image/file?path=...` が 200 | §3.7 |

> 進捗を SSE で見る場合: `curl -N -u <basic> $WEB/api/image/jobs/stream`（`-N` で buffering 無効化、ブラウザなら DevTools → Network → EventStream）。

---

## 2. Capability 照合（Main/Sub 差分確認）

`docs/image_gen/preset_compat.md` §3 の表を実行形式に落としたもの。`t2i_base` の前提崩れを検出する。

### 2.1 capability 取得

```bash
# Pi 上で（Pi の .env に合わせて AGENT_SECRET_TOKEN を export しておく）
curl -s -H "X-Agent-Token: $T" http://$MAIN:7777/capability | jq . > /tmp/cap_main.json
curl -s -H "X-Agent-Token: $T" http://$SUB:7777/capability  | jq . > /tmp/cap_sub.json
```

### 2.2 厳密一致すべき項目

| JSONPath | 許容 | 期待 |
|---|---|---|
| `.comfyui_version` | 厳密一致 | 両 PC 同値（最新 stable） |
| `.models[] \| select(.type=="checkpoints")` | 厳密一致 | 両 PC に `chenkinNoobXL_v05.safetensors` が存在 |
| `.custom_nodes[].commit` | 厳密一致 | snapshot 適用で揃える |
| `.gpu_info.cuda_compute` | 厳密一致 | 両 `8.9`（sm_89） |
| `.gpu_info.vram_total_mb` | 厳密一致 | 両 16384 |
| `.comfyui_available` | 厳密一致 | 両 `true` |

差分許容:

- `.gpu_info.name`: Main `RTX 4080` / Sub `RTX 5060 Ti` は仕様通り
- `.gpu_info.vram_free_mb`: 動的値のため差分 OK
- `.agent_id`: `main-pc` / `sub-pc` で異なって正常
- `.cache_usage`: 運用で変動するため差分 OK

### 2.3 diff スクリプト例

```bash
jq -S 'del(.gpu_info.vram_free_mb, .cache_usage, .agent_id, .gpu_info.name, .busy, .updates_available)' /tmp/cap_main.json > /tmp/cap_main.norm.json
jq -S 'del(.gpu_info.vram_free_mb, .cache_usage, .agent_id, .gpu_info.name, .busy, .updates_available)' /tmp/cap_sub.json  > /tmp/cap_sub.norm.json
diff -u /tmp/cap_main.norm.json /tmp/cap_sub.norm.json
```

**空 diff** なら `t2i_base` 前提は満たされる。差分ありなら `docs/image_gen/preset_compat.md` §5 の「ロック or Snapshot 同期」手順へ。

---

## 3. 失敗系トラブルシュート

各項目は **症状 / 原因候補 / 切り分け / 対処** の 4 項で記述。

### 3.1 Windows Agent に 200 が返らない

| 項目 | 内容 |
|---|---|
| 症状 | `curl http://<pc>:7777/health` が `connection refused` / `timeout` / `401` / `403` |
| 原因候補 | (a) Agent プロセス未起動、(b) Windows Defender ファイアウォール、(c) `X-Agent-Token` 不一致、(d) バインドアドレス `127.0.0.1` のみで LAN 非公開 |
| 切り分け | **Main/Sub で**: `powershell: Get-Process -Name python -ErrorAction SilentlyContinue` → Agent プロセスの存在確認。`Test-NetConnection -ComputerName 127.0.0.1 -Port 7777` でローカル疎通。**Pi から**: `curl -v http://<pc>:7777/health` でヘッダ確認。`401/403` は §3.8 へ。`connection refused` は (a)、`timeout` は (b) or (d)、 |
| 対処 | (a) `windows-agent/start_agent.bat` を実行。タスクスケジューラ登録済みなら `schtasks /Query /TN SecretaryBotAgent`。(b) §3.9。(c) §3.8。(d) `agent.py` の `uvicorn.run(host=...)` が `0.0.0.0` になっているか確認 |

### 3.2 `/capability` が 500、GPU 情報欠落、ComfyUI 未インストール検知

| 項目 | 内容 |
|---|---|
| 症状 | `/capability` が 500、または `comfyui_available: false`、`gpu_info` 欠落、`models: []` |
| 原因候補 | (a) ComfyUI が未インストール（`${SECRETARY_BOT_ROOT}/comfyui/` 無い）、(b) ComfyUI プロセスが起動失敗・クラッシュループ、(c) NVIDIA ドライバ未導入で `nvidia-smi` 失敗、(d) `extra_model_paths.yaml` 不正 |
| 切り分け | (a) Main PC で `powershell: Test-Path "$env:SECRETARY_BOT_ROOT\comfyui"`。(b) `curl -H "X-Agent-Token:$T" http://$MAIN:7777/comfyui/status` の `state`/`restart_count`/`recent_logs` を見る。(c) `powershell: nvidia-smi`。(d) ComfyUI のログで `invalid yaml` の有無 |
| 対処 | (a) `POST /comfyui/setup` を叩くか [`mainpc.md`](mainpc.md) の初回セットアップ節へ。(b) §3.10。(c) ドライバ再インストール → 再起動。(d) `extra_model_paths.yaml` の `base_path` が `${SECRETARY_BOT_CACHE}/models/` を指しているか確認 |

### 3.3 NAS マウント不整合

| 項目 | 内容 |
|---|---|
| 症状 | Pi では見えるが Windows からは `Z:\` に何も見えない、片方で書いたものがもう片方で見えない、`Access denied` |
| 原因候補 | (a) Windows 側で `New-SmbMapping` / `net use` がされていない、(b) NAS 側の ACL でユーザー別に読み書き権限が異なる、(c) マウントポイントを別ユーザーが握っている（Windows Agent が SYSTEM 実行で、対話ログイン時のマッピングが見えない） |
| 切り分け | Pi: `ls -la /mnt/ai-image && mount \| grep ai-image`。Main: `powershell: Get-SmbMapping; Get-ChildItem Z:\` / `Get-Acl Z:\outputs \| Format-List`。SYSTEM 実行 Agent からの可視性は `PsExec -s -i cmd.exe` で `net use` を確認 |
| 対処 | [`../nas_setup.md`](../nas_setup.md) §4 を再実行。Agent が SYSTEM で動く場合は「SYSTEM コンテキスト向けのマッピング」を `start_agent.bat` 先頭で `net use Z: \\$NAS_HOST\ai-image ...` として冪等に叩く |

### 3.4 DB migration エラー

| 項目 | 内容 |
|---|---|
| 症状 | Pi 側 Bot 起動時に `no such table: image_jobs` / `workflows` エラー、WebGUI の `/api/image/workflows` が 500 |
| 原因候補 | (a) v26 マイグレーション未適用（旧 `user_version < 26`）、(b) マイグレーション中に SQL エラーで途中停止、(c) DB ファイルが別実行環境のもの |
| 切り分け | Pi bash: `sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 "PRAGMA user_version;"` → 26 未満なら (a)。`.tables` で `image_jobs`/`image_job_events`/`workflows`/`model_cache_manifest` の有無を確認。Bot ログの `Database connected` 前後で例外が出ていないか |
| 対処 | (a) Bot を再起動すれば `_migrate()` が流れる。失敗し続ける場合はログの SQL を目視し、手動で `ALTER TABLE` を補う。DB 破損時は `.backup` から復旧、それも無ければ `workflows` の seed と互換性があるので **`data/bot.sqlite3` を削除して再起動** で初期化可（`outputs/` は NAS 側なので影響なし）[要ユーザー確認] |

### 3.5 状態機械が停滞する

| 症状 | 原因候補 | 切り分け | 対処 |
|---|---|---|---|
| `queued` のまま動かない | (a) Dispatcher worker 停止、(b) AgentPool 全滅（capability 返らず）、(c) `busy=true`（LoRA 学習中） | Pi bot ログで `job_dispatcher` の tick 有無。`/api/image/jobs/{id}` で `last_error` 確認。各 Agent の `/capability` → `busy` フィールド | (a) Bot 再起動。(b) §3.1 / §3.9。(c) 学習完了待ちか、`/lora/train/{id}/cancel` |
| `dispatching` → `queued` に戻り続ける | 利用可能 Agent ゼロ、または `required_models` が NAS に無い | `image_job_events` の `detail_json` を見る。`sqlite3 ... "SELECT from_status,to_status,detail_json FROM image_job_events WHERE job_id=? ORDER BY occurred_at"` | §3.1 / §3.3。モデル未配置なら NAS に `chenkinNoobXL_v05.safetensors` を置く |
| `warming_cache` で停滞 | NAS → Agent SSD コピー失敗・遅延、sha256 不一致 | `curl -N -H "X-Agent-Token:$T" http://<agent>:7777/cache/sync/<sync_id>/stream` を購読。`image_jobs.cache_sync_id` 参照 | sha256 不一致なら NAS 側サイドカー再生成（[`../nas_setup.md`](../nas_setup.md) §5）。10 分超で自動 `failed`、retry 余地があれば `queued` 戻り |
| `running` で進捗が止まる | ComfyUI プロセス停止、OOM、WebSocket 切断 | Agent の `/comfyui/status` と `/system/logs?source=comfyui&lines=200`。`image_jobs.progress` が更新されているか | OOM は §3.5-OOM。停止なら `/comfyui/restart`。復旧しない場合は SSE `error` の `retryable` を待って Pi が retry |
| OOM（CUDA out of memory） | 解像度 / batch / LoRA 枚数過多、他プロセスによる VRAM 占有 | Agent ログ: `CUDA out of memory at KSampler` / `node_id: 6`。`/capability.gpu_info.vram_free_mb` が実行時ほぼゼロ | Pi は `ComfyUIError.OOMError` を受け `retryable=true` で別 Agent retry。両方 OOM なら `failed` 確定 → プリセット切替（解像度 896x1152 等）を提案 |

**SQL トレース**（任意ジョブの遷移を時系列で見る）:

```bash
sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 <<SQL
.mode column
.headers on
SELECT occurred_at, from_status, to_status, agent_id, substr(detail_json,1,80) AS detail
  FROM image_job_events
 WHERE job_id = 'img_01H...'
 ORDER BY occurred_at;
SELECT status, retry_count, last_error, next_attempt_at, timeout_at
  FROM image_jobs WHERE id='img_01H...';
SQL
```

### 3.6 NAS に画像が保存されない

| 項目 | 内容 |
|---|---|
| 症状 | `status=done` なのに `outputs/YYYY-MM/YYYY-MM-DD/` に PNG が無い、または途中で書き込み失敗 |
| 原因候補 | (a) Agent 実行ユーザーに `outputs/` 書き込み権限無し、(b) `extra_model_paths.yaml` の `output_dir` が NAS を指していない、(c) NAS 途中切断、(d) Agent が NAS を別ドライブレターでマウントしており workflow の `output_dir` と不一致 |
| 切り分け | Main PC: `powershell: "test" \| Out-File Z:\outputs\_w.txt`。Agent ログ `/system/logs?source=comfyui` の `SaveImage` 周辺。`image_jobs.result_paths` の値 | (a)(d) [`../nas_setup.md`](../nas_setup.md) §3/§4 で権限・ドライブレターを `Z:` に統一。(b) `extra_model_paths.yaml` で `output_directory` を NAS に向ける。(c) `Get-SmbMapping` で再接続 |

### 3.7 WebGUI Gallery が空 / `/api/image/file` が 403/404

| 項目 | 内容 |
|---|---|
| 症状 | Gallery タブでサムネイルが一切出ない、個別画像が 403 `path outside nas mount` or `only outputs/ is allowed`、または 404 `file not found` |
| 原因候補 | (a) Pi から NAS が見えていない、(b) `config.yaml` の `units.image_gen.nas.base_path` が実マウントと不一致、(c) `outputs_subdir` が実ディレクトリと不一致、(d) path traversal ガードが `..` / シンボリックリンクで弾いている、(e) 画像拡張子が `.png/.jpg/.jpeg/.webp` 以外 |
| 切り分け | Pi bash: `ls /mnt/ai-image/outputs \| head` → 空なら §3.3/§3.6。ブラウザ DevTools で `/api/image/file?path=...` のレスポンスボディ（FastAPI が `detail` に理由を入れる）。`config.yaml` `nas.base_path` と Pi 側 `mount` コマンド結果を比較 | (a)(b) §3.3 または config 修正後 Bot 再起動。(c) `outputs_subdir: "outputs"` を確認。(d) ガードは `mount_point` 配下 + `outputs/` 配下のみ許可。パスに余計なプレフィックスが入っていないか（`workflows/...` 等は意図的に拒否）。(e) サポート拡張子は `.png/.jpg/.jpeg/.webp` のみ（`src/web/app.py` の `_IMG_ALLOWED_EXTS`） |

### 3.8 AGENT_SECRET_TOKEN 不一致（403）

| 項目 | 内容 |
|---|---|
| 症状 | `curl http://<pc>:7777/health` が 401 / 403、Pi ログに `AgentCommunicationError: 401` |
| 原因候補 | Pi `.env` と Agent `.env` の `AGENT_SECRET_TOKEN` が別値 |
| 切り分け | Pi: `grep AGENT_SECRET_TOKEN /home/iniwa/docker/secretary-bot/.env`。Main/Sub: `powershell: Get-Content $env:SECRETARY_BOT_ROOT\.env \| Select-String AGENT_SECRET_TOKEN`。**先頭数文字だけ見て一致確認**（全体を画面に出さない） |
| 対処 | 片方に合わせる。変更後は Agent プロセス再起動（環境変数は起動時読み込み）。Pi は Bot 再起動 |

### 3.9 Windows Defender で :7777 ブロック

| 項目 | 内容 |
|---|---|
| 症状 | Main/Sub 内部からは `localhost:7777` OK、LAN からは `timeout` |
| 切り分け | Pi: `nc -vz $MAIN 7777` / `Test-NetConnection -ComputerName $MAIN -Port 7777`（Win）で到達性。Main: `powershell: Get-NetFirewallRule -DisplayName "*7777*" -ErrorAction SilentlyContinue` |
| 対処 | Main/Sub で管理者 PowerShell: `New-NetFirewallRule -DisplayName "Secretary Bot Agent" -Direction Inbound -LocalPort 7777 -Protocol TCP -Action Allow -Profile Private`。公開プロファイルでは開けない（`Private` のみ、LAN を Private 扱いに） |

### 3.10 ComfyUI クラッシュループ

| 項目 | 内容 |
|---|---|
| 症状 | `/capability.comfyui_available=false` と `true` が往復、`/comfyui/status` の `restart_count` が増え続ける、`crash_restart_max_retries` 到達で `false` 固定 |
| 原因候補 | (a) CUDA ドライバ不整合、(b) 依存ライブラリ壊れ（pip 競合）、(c) `custom_nodes` の 1 つが起動時例外を吐く、(d) OOM 直後のロック解除遅延 |
| 切り分け | `curl -H "X-Agent-Token:$T" http://$MAIN:7777/comfyui/status` → `recent_logs` 最新 50 行。`/system/logs?source=comfyui&lines=500&level=error` で例外スタック |
| 対処 | (a) `nvidia-smi` で確認、ドライバ再導入 + 再起動。(b) `venv-comfyui` を作り直し（`POST /comfyui/setup` with `force=true`）。(c) 問題の custom_node を一時的に除外（フォルダリネーム）→ `/comfyui/restart`。`crash_restart_max_retries` 到達後は手動 `/comfyui/restart` で復帰 |

### 3.11 custom_nodes が Main/Sub で違う（capability 差分）

| 項目 | 内容 |
|---|---|
| 症状 | §2 の diff に `custom_nodes[].commit` 差分、特定プリセットが Sub で失敗・Main で成功 |
| 切り分け | `jq '.custom_nodes' /tmp/cap_*.json` で name/commit 対照表を作る |
| 対処 | [`../preset_compat.md`](../preset_compat.md) §5.2 の Snapshot 同期手順。`t2i_base` のみであれば custom_nodes 非依存なので無視して可。他プリセットは一時的に `workflows.main_pc_only=1` に更新して隔離:<br>`sqlite3 ... "UPDATE workflows SET main_pc_only=1 WHERE name='...';"` |

---

## 4. 状態機械トレース手順

### 4.1 `image_jobs` スナップショット

```bash
sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 <<'SQL'
.mode column
.headers on
SELECT id, status, assigned_agent, progress, retry_count, max_retries,
       substr(last_error,1,60) AS last_error,
       next_attempt_at, timeout_at, started_at, finished_at
  FROM image_jobs
 ORDER BY created_at DESC
 LIMIT 20;
SQL
```

### 4.2 `image_job_events` 時系列

```bash
sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 \
  "SELECT occurred_at, from_status, to_status, agent_id, detail_json
     FROM image_job_events WHERE job_id='<JOB_ID>' ORDER BY occurred_at;"
```

### 4.3 `last_error` の読み方

`image_jobs.last_error` は Agent が返した §1.4 エラー JSON 文字列（そのまま格納）。キーは `error_class` / `message` / `retryable` / `detail`。`error_class` でリトライ方針が決まる（`../api.md` §10）。

例:

- `ComfyUIError.OOMError` + `retryable=true` → 次回は別 Agent に回される想定（AgentPool priority 2 台以上時）
- `ComfyUIError.WorkflowValidationError` + `retryable=false` → 即 `failed` 確定、プリセット JSON / required_nodes の齟齬
- `CacheSyncError` + `retryable=true` → `warming_cache → queued` に戻り backoff

### 4.4 SSE `/api/image/jobs/stream` のイベント種別

Pi 側 WebGUI 向け SSE。クライアント（`src/web/static/js/pages/image-gen.js`）が受け取るイベント:

| event | 内容 |
|---|---|
| `status` | `{job_id, status, progress}` — Pi 側の `image_jobs.status` 変化 |
| `progress` | `{job_id, percent}` — 2 秒デバウンスで更新 |
| `result` | `{job_id, result_paths}` — 完了時 |
| `error` | §1.4 フォーマット + `job_id` |
| `keepalive` | 15 秒ごとのコメント行 |

Agent 側 SSE（`/image/jobs/{id}/stream`）は `progress`/`log`/`preview`/`status`/`result`/`error`/`done` でより詳細（`../api.md` §4.3）。

---

## 5. ロールバック手順

Phase 1 で問題が発生した場合、機能全体を一時無効化する。

### 5.1 機能トグル OFF

Pi 側 `config.yaml` を編集:

```yaml
units:
  image_gen:
    enabled: false
```

Bot を再起動（Portainer Stack の再起動、または WebGUI の「コード更新」ボタン相当の再起動）。

### 5.2 影響範囲

- WebGUI `/api/image/*` は `503 image_gen unit not loaded` を返す（`src/web/app.py` の `_get_image_gen_unit()` ガード）
- DB は温存（`image_jobs` / `workflows` / `image_job_events` / `model_cache_manifest` はそのまま）
- NAS の `outputs/` も温存
- Windows Agent は画像生成以外のユニット（既存 `input-relay` など）に影響なし

### 5.3 部分ロールバック

片方の PC だけ無効化する場合は Pi の `config.yaml` の `agent_pool` から該当 PC を除外（priority を `-1` にするか enabled を false に）[要ユーザー確認・設定キー名を実装で確認]。

---

## 6. 既知の制約（Phase 1 スコープ）

本書は Phase 1（最小 E2E：WebGUI 起点の `t2i_base` 生成）向け。以下は Phase 1 では対象外。

- **Phase 2**: MainPC busy 時の SubPC 自動フォールバック、`retryable=true` での別 Agent への自動振り分け。Phase 1 では retry は同 Agent に戻る前提（`OOMError` のみ別 Agent 想定）
- **キャッシュ LRU**: `model_cache_manifest` の自動エビクトは未実装。SSD 容量逼迫時は Agent 上で手動で `${SECRETARY_BOT_CACHE}/models/` を整理
- **Phase 3**: Discord スラッシュコマンド（`/image` 等）未提供。起動は WebGUI のみ
- **Phase 4**: LoRA 学習系（`/kohya/*`, `/lora/train/*`）は未稼働。API は定義済みだが Phase 1 では使わない
- **Phase 5**: 同条件再投入 UI（過去ジョブの seed / params 再利用）、PNG メタ再現は未実装

---

## 付録 A. よく使うコマンド早見表

```bash
# Agent 疎通
curl -H "X-Agent-Token: $T" http://$MAIN:7777/health
curl -H "X-Agent-Token: $T" http://$MAIN:7777/capability | jq .
curl -H "X-Agent-Token: $T" http://$MAIN:7777/comfyui/status | jq .

# Agent ログ
curl -H "X-Agent-Token: $T" "http://$MAIN:7777/system/logs?source=comfyui&lines=200&level=info" | jq .

# DB 直読み（Pi）
sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 "PRAGMA user_version;"
sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 ".tables"
sqlite3 /home/iniwa/docker/secretary-bot/data/bot.sqlite3 "SELECT name FROM workflows;"

# SSE 購読（Pi 側 WebGUI 経由）
curl -N -u <basic_user>:<basic_pass> $WEB/api/image/jobs/stream
```

```powershell
# Windows Agent プロセス / ポート確認
Get-Process python -ErrorAction SilentlyContinue
Test-NetConnection -ComputerName 127.0.0.1 -Port 7777
Get-NetFirewallRule -DisplayName "*7777*" -ErrorAction SilentlyContinue

# NAS マッピング確認
Get-SmbMapping
Get-ChildItem Z:\outputs
```

---

## 付録 C. セットアップ系エンドポイント（2026-04-16 追加）

未インストール環境に ComfyUI / kohya_ss を HTTP 経由で導入するための API。実装は
`windows-agent/tools/image_gen/setup_manager.py` / `router.py` 参照。

### C.1 ComfyUI 初回セットアップ

```bash
curl -X POST -H "X-Agent-Token: $T" -H "Content-Type: application/json" \
     -d '{}' http://$MAIN:7777/comfyui/setup
# => {"task_id": "setup_...", "status": "running", "progress_url": "/tools/image-gen/setup/setup_..."}
```

body 省略時は `config.image_gen.setup.{comfyui_repo, comfyui_ref, cuda_index_url}` が使われる。
個別に上書きしたい場合は body で `{"repo_url", "ref", "cuda_index_url"}` を渡す。

進捗は:
```bash
curl -H "X-Agent-Token: $T" http://$MAIN:7777/setup/<task_id>
# => {"task_id", "status": "running|done|failed", "current_step", "log_tail": [...]}
```

実処理:
1. `<root>/comfyui` に `git clone`（既存なら `fetch + checkout + pull --ff-only`）
2. `<root>/venv-comfyui` 生成（無ければ）
3. `pip install --upgrade pip wheel`
4. `pip install torch torchvision torchaudio --index-url <cuda_index_url>`
5. `pip install -r comfyui/requirements.txt`

所要時間は 10〜30 分（PyTorch のダウンロードが支配的）。

### C.2 ComfyUI アップデート

```bash
curl -X POST -H "X-Agent-Token: $T" -d '{}' http://$MAIN:7777/comfyui/update
```

稼働中なら自動で `stop()` してから `git pull` → `requirements.txt` 再インストール。再起動は
別途 `POST /comfyui/start` を叩くか、最初のジョブ投入で遅延起動に任せる。

### C.3 kohya_ss セットアップ（Phase 4 向け）

```bash
curl -X POST -H "X-Agent-Token: $T" -d '{}' http://$MAIN:7777/kohya/setup
```

`config.image_gen.kohya.enabled: true` の Agent でのみ 202 が返る。
`<root>/kohya_ss` にクローンし `<root>/venv-kohya` を作る。

### C.4 全 task の状態確認

```bash
curl -H "X-Agent-Token: $T" http://$MAIN:7777/setup
# => {"tasks": [{...snapshot...}, ...]}
```

task は Agent プロセス内のメモリに保持（再起動で消える）。長期記録が必要になったら
ログファイル / DB 化を検討。

---

## 付録 B. 判断分岐の優先順位（Claude Code 向け）

トラブル時の既定の探索順:

1. **Agent 層（§3.1, §3.8, §3.9, §3.2）** — health/capability が返るか
2. **NAS 層（§3.3, §3.6, §3.7）** — 物理的にファイルが往来できるか
3. **DB 層（§3.4）** — `user_version=26` とテーブル存在
4. **Dispatcher 層（§3.5, §4）** — 状態機械とイベントログ
5. **ComfyUI プロセス層（§3.10）** — クラッシュループ
6. **プリセット互換層（§3.11, §2）** — Main/Sub 差分

各層の合格が前提で次層に進む。上層が壊れたまま下層を調べても切り分けに失敗する。
