# Raspberry Pi 側 画像生成セットアップガイド

secretary-bot の画像生成機能（`image_gen` / `lora_train` / `prompt_session`）を Raspberry Pi 4（bot ホスト）側で有効化する手順書。
**このガイドを Claude Code に「全部やって」と渡すと完走する**ことを想定している。Windows Agent 側（ComfyUI / kohya_ss / NAS マウント）のセットアップ手順は別途。

## 想定環境
- **作業場所**: `ssh iniwapi` でリモート接続、または Pi 上で Claude Code を起動
- **OS**: Raspberry Pi OS (linux/arm64)
- **bot**: Docker + Portainer Stack で既に稼働中
  - コード: `/home/iniwa/docker/secretary-bot/src` (Volume)
  - 設定: `/home/iniwa/docker/secretary-bot/config.yaml`
  - DB/ChromaDB: `/home/iniwa/docker/secretary-bot/data`
  - `.env`: `/home/iniwa/docker/secretary-bot/.env`
- **コマンド**: bash 前提

## 関連ドキュメント
- 設計: `docs/design/image_gen_design.md`
- NAS 初期化: `docs/design/image_gen_nas_setup.md`（**NAS 側と Pi マウント詳細はこちらを必ず読む**）
- WebGUI API: `docs/design/image_gen_api.md`
- 最終確認: `docs/setup/image_gen_verify.md`（次ステップ）

---

## 1. 前提確認

### 1.1 bot コンテナの稼働確認
```bash
# コンテナ稼働
docker ps --filter "name=secretary-bot"

# /health で 200 OK が返ること（<port> は .env の WEBGUI_PORT、既定 8100）
PORT=$(grep -E '^WEBGUI_PORT=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2)
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:${PORT:-8100}/health
```

- [ ] `docker ps` に `secretary-bot` が `Up` で表示される
- [ ] `/health` が `200` を返す

### 1.2 Portainer への疎通
`CLAUDE.md` の `PORTAINER_URL` にブラウザでアクセスし、`secretary-bot` Stack を編集可能なことを確認。
- [ ] Portainer にログイン済み
- [ ] Stack の Web Editor が開ける

### 1.3 Windows Agent 稼働確認
MainPC / SubPC の IP は `config.yaml` の `windows_agents:` を参照。

```bash
# Main PC
curl -sS -m 3 http://192.168.1.101:7777/health
# Sub PC
curl -sS -m 3 http://192.168.1.102:7777/health
```

- [ ] 両 Agent が `200` + JSON を返す（最低でも片方）
- [ ] `[要ユーザー確認]` 片方しか稼働しない場合、`config.yaml` の `priority` を下げるか `enabled` 相当の扱いをユーザーに確認

### 1.4 NAS マウント要件
```bash
# cifs-utils が入っているか
dpkg -s cifs-utils 2>/dev/null | grep -E '^Status:' || echo "NOT INSTALLED"
```

未インストールなら次章で入れる。詳細手順は `docs/design/image_gen_nas_setup.md` §4.1 参照。

---

## 2. NAS SMB マウント（Pi 側）

詳細は `docs/design/image_gen_nas_setup.md` §3〜§4.1 を参照。ここでは要点のみ。

### 2.1 必要パッケージ
```bash
sudo apt update
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/ai-image
```

### 2.2 認証ファイル作成
`[要ユーザー確認]` NAS の IP・共有名・ユーザー名・パスワードをユーザーに確認してから実行。

```bash
# root 所有・600 必須
sudo tee /etc/cifs-credentials-ai-image > /dev/null <<'EOF'
username=ai-image-rw
password=********
domain=WORKGROUP
EOF
sudo chown root:root /etc/cifs-credentials-ai-image
sudo chmod 600 /etc/cifs-credentials-ai-image
```

### 2.3 `/etc/fstab` に永続マウントを追記
```bash
# NAS の IP を確認してから行を追加（既存があればスキップ）
grep -q '/mnt/ai-image' /etc/fstab || sudo tee -a /etc/fstab > /dev/null <<'EOF'
//192.168.1.20/ai-image  /mnt/ai-image  cifs  credentials=/etc/cifs-credentials-ai-image,iocharset=utf8,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,vers=3.0,nofail,x-systemd.automount,x-systemd.device-timeout=10  0  0
EOF

sudo systemctl daemon-reload
sudo mount -a
```

### 2.4 マウント検証
```bash
mountpoint /mnt/ai-image && echo MOUNTED || echo NOT_MOUNTED
ls /mnt/ai-image
ls /mnt/ai-image/outputs
ls /mnt/ai-image/workflows

# 書き込みテスト（Pi は workflows/ に書き込み可）
echo "pi-mount-test $(date -Is)" > /mnt/ai-image/workflows/_pi_test.txt && rm /mnt/ai-image/workflows/_pi_test.txt && echo OK
```

- [ ] `MOUNTED` が出る
- [ ] `outputs/` / `workflows/` が `ls` できる（NAS 初期化が済んでいれば空でOK）
- [ ] 書き込みテスト成功

> **Pi 再起動後もマウントが復活するか**（`nofail` + automount）を後で確認すること。

---

## 3. `.env` 編集

対象: `/home/iniwa/docker/secretary-bot/.env`
既存値は上書きしない。**以下のブロックが無ければ追記**する（`.env.example` の `=== Image Generation (NAS) ===` セクション）。

```bash
# 追記前にバックアップ
cp /home/iniwa/docker/secretary-bot/.env /home/iniwa/docker/secretary-bot/.env.bak.$(date +%Y%m%d-%H%M%S)

# 既存確認
grep -E '^NAS_SMB_' /home/iniwa/docker/secretary-bot/.env || echo "NAS_SMB_ not set"
grep -E '^AGENT_SECRET_TOKEN=' /home/iniwa/docker/secretary-bot/.env || echo "AGENT_SECRET_TOKEN not set"
```

### 追記する値
`[要ユーザー確認]` 値はユーザー/NAS 管理者/Windows Agent 側と共有されているものに合わせる。

```dotenv
# === Image Generation (NAS) ===
NAS_SMB_HOST=192.168.1.20
NAS_SMB_SHARE=ai-image
NAS_SMB_USER=ai-image-rw
NAS_SMB_PASSWORD=********
NAS_MOUNT_POINT=/mnt/ai-image

# === Windows Agent ===
# MainPC / SubPC の start_agent.bat で設定している値と完全一致させる
AGENT_SECRET_TOKEN=********
```

- [ ] `NAS_SMB_*` 5 変数が埋まっている
- [ ] `AGENT_SECRET_TOKEN` が Windows Agent 側と一致（不一致だと認証 401 で全ジョブ失敗）
- [ ] `.env` の権限が 600 以下（`ls -la .env` で確認）

---

## 4. `config.yaml` 編集

対象: `/home/iniwa/docker/secretary-bot/config.yaml`
`config.yaml.example` の該当セクションを丸ごと移植する方針で良い。差分のみで済ませたい場合は下記を参照。

```bash
cp /home/iniwa/docker/secretary-bot/config.yaml /home/iniwa/docker/secretary-bot/config.yaml.bak.$(date +%Y%m%d-%H%M%S)
```

### 4.1 `windows_agents:` の確認
既に `pc-main` / `pc-sub` が登録されている前提。IP・ポート・`priority` が実機と一致するか確認。
```yaml
windows_agents:
  - id: "pc-main"
    host: "192.168.1.101"
    port: 7777
    priority: 1
    # ...
  - id: "pc-sub"
    host: "192.168.1.102"
    port: 7777
    priority: 2
    # ...
```
- [ ] 両 PC の IP が実機と一致
- [ ] `priority` は「優先して使う方」を小さい数字に

### 4.2 `units:` に画像生成 3 ユニットを有効化
`config.yaml.example` の 107〜149 行目を踏襲。既存 `units:` ブロックの末尾付近に追加。

```yaml
units:
  # ...（既存ユニット）...

  # 画像生成（ComfyUI を Windows Agent 側で実行）
  image_gen:
    enabled: true
    discord_output_channel_id: 0              # 0 で Discord 投稿無効
    discord_lora_max_slots: 1
    default_preset: "t2i_base"
    default_sampler: "euler_ancestral"
    default_scheduler: "normal"
    default_cfg: 5.5
    default_steps: 30
    default_size: "1024x1024"
    retry:
      max_retries: 2
      base_backoff_seconds: 30
      max_backoff_seconds: 300
    timeouts:
      dispatching_seconds: 30
      warming_cache_seconds: 600
      running_default_seconds: 300
      queued_seconds: 86400
    dispatcher:
      poll_interval_seconds: 2
      stuck_reaper_interval_seconds: 30
      progress_debounce_seconds: 2
    nas:
      base_path: "/mnt/ai-image"
      outputs_subdir: "outputs"
      workflows_subdir: "workflows"
      lora_datasets_subdir: "lora_datasets"
      lora_work_subdir: "lora_work"
      snapshots_subdir: "snapshots"

  lora_train:
    enabled: true
    default_base_model: "ChenkinNoob-XL-V0.5.safetensors"
    sample_every_n_epochs: 1
    save_every_n_epochs: 2

  prompt_session:
    ttl_days: 7
    history_max_turns: 6
    llm:
      temperature: 0.4
      ollama_only: false
```

- [ ] `units.image_gen.enabled: true`
- [ ] `units.image_gen.nas.base_path` が §2 の `NAS_MOUNT_POINT` と一致
- [ ] `[要ユーザー確認]` `discord_output_channel_id` を実 ID に差し替えるかどうか（0 のままなら Discord 投稿なし・WebGUI のみ）
- [ ] `[要ユーザー確認]` `lora_train.default_base_model` が NAS 上の `models/checkpoints/` に実在するか

### 4.3 YAML 妥当性チェック
```bash
python3 -c "import yaml; yaml.safe_load(open('/home/iniwa/docker/secretary-bot/config.yaml'))" && echo OK
```
- [ ] `OK` が出る（インデント崩れ検知）

---

## 5. コード更新 & 再起動

### 5.1 WebGUI から更新（推奨）
```bash
# Basic 認証の資格情報は .env から取得
USER=$(grep -E '^WEBGUI_USERNAME=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2)
PASS=$(grep -E '^WEBGUI_PASSWORD=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2)
PORT=$(grep -E '^WEBGUI_PORT=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2-)
PORT=${PORT:-8100}

curl -sS -u "$USER:$PASS" -X POST "http://localhost:${PORT}/api/update-code"
```
→ 内部で `git pull` + Portainer API で再起動される。

### 5.2 Portainer から redeploy（代替）
Portainer UI → Stacks → `secretary-bot` → Editor で `Update the stack` ボタン。

### 5.3 起動ログ確認
```bash
docker logs --since 2m -f secretary-bot 2>&1 | tee /tmp/bot-start.log
# Ctrl+C で抜ける（10-30 秒見れば十分）
```

確認項目（いずれもログに出るはず）:
- [ ] `Loaded unit: image_gen`
- [ ] `Loaded unit: lora_train`
- [ ] `Loaded unit: prompt_session`
- [ ] DB migration 完了ログ（`_SCHEMA_VERSION = 26` まで進む）
- [ ] `sync_presets_to_db` 相当の実行痕跡（workflow_mgr のログ）
- [ ] `ERROR` / `Traceback` が新規に増えていない

### 5.4 DB migration v26 と `t2i_base` seed 確認
```bash
DB=/home/iniwa/docker/secretary-bot/data/secretary-bot.db   # 実ファイル名はプロジェクトに合わせる
sudo sqlite3 "$DB" "PRAGMA user_version;"                    # 26 を期待
sudo sqlite3 "$DB" "SELECT name, category, starred FROM workflows;"
```
- [ ] `user_version` が `26`
- [ ] `workflows` に `t2i_base` 行が存在

---

## 6. 動作確認: WebGUI 経由

### 6.1 アクセス
ブラウザで `http://<pi-ip>:<WEBGUI_PORT>/` → Basic 認証（`WEBGUI_USERNAME` / `WEBGUI_PASSWORD`）。

- [ ] ログイン成功
- [ ] サイドバーに `Image Gen` 項目がある
- [ ] クリックで画面遷移（Generate / Jobs / Gallery / Workflows タブ）

### 6.2 最小プロンプト投入
Generate タブで:
- Workflow プルダウンから `t2i_base` を選択
- [ ] プルダウンに `t2i_base` が出る（出ない場合は §5.4 の workflows テーブル確認）
- Prompt 欄に最小例（`1girl, solo, simple background` など）
- 他パラメータは既定値のまま Submit

Jobs タブで状態遷移を目視:
- [ ] `queued` → `dispatching` → `running` → `done`
- [ ] どこかで止まる場合は §8 参照

### 6.3 Gallery 確認
- [ ] Gallery タブに生成画像サムネイルが表示される
- [ ] `/mnt/ai-image/outputs/YYYY-MM/YYYY-MM-DD/` に実ファイルが保存されている

---

## 7. 動作確認: curl 経由

### 7.1 変数準備
```bash
USER=$(grep -E '^WEBGUI_USERNAME=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2)
PASS=$(grep -E '^WEBGUI_PASSWORD=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2)
PORT=$(grep -E '^WEBGUI_PORT=' /home/iniwa/docker/secretary-bot/.env | cut -d= -f2-)
PORT=${PORT:-8100}
BASE="http://localhost:${PORT}"
AUTH="-u $USER:$PASS"
```

### 7.2 生成ジョブ投入
```bash
curl -sS $AUTH -X POST "$BASE/api/image/generate" \
  -H 'Content-Type: application/json' \
  -d '{"workflow": "t2i_base", "prompt": "1girl, solo, simple background"}'
```
レスポンスの `job_id` を控える。
- [ ] `job_id` が返る

### 7.3 ジョブ状態取得
```bash
JOB=<上で得た job_id>
curl -sS $AUTH "$BASE/api/image/jobs/$JOB" | python3 -m json.tool
```
- [ ] `status` が遷移する

### 7.4 SSE ストリーム
```bash
curl -N $AUTH "$BASE/api/image/jobs/stream"
```
- [ ] 何らかの `data:` イベントが流れる（新規ジョブを投入すると進捗が出る）
- 抜けるには Ctrl+C

### 7.5 ジョブ一覧 / ギャラリー
```bash
curl -sS $AUTH "$BASE/api/image/jobs" | python3 -m json.tool | head -40
curl -sS $AUTH "$BASE/api/image/gallery" | python3 -m json.tool | head -40
curl -sS $AUTH "$BASE/api/image/workflows" | python3 -m json.tool
```

---

## 8. トラブルシュート（Pi 側）

### 8.1 Windows Agent に疎通できない
症状: Job が `dispatching` のまま進まない / `running` でタイムアウト。

確認順:
1. `curl -sS -m 3 http://<agent>:7777/health` が Pi から届くか
2. Windows 側ファイアウォール: 7777 の Inbound 許可、同一 LAN
3. `AGENT_SECRET_TOKEN`（Pi `.env`）と Windows Agent 側トークンの完全一致
   - 不一致なら `401 Unauthorized` が bot ログに出る
4. `config.yaml` の `windows_agents[].host` / `.port` が実機と一致

### 8.2 NAS マウントが落ちる
症状: Gallery が空 / ジョブは成功しているのに `outputs/` が見えない。

```bash
mountpoint /mnt/ai-image || sudo mount -a
dmesg | tail -40 | grep -i cifs
```
- ネットワーク断後に復活しないなら `/etc/fstab` の `x-systemd.automount,nofail` を再確認
- 認証エラーは `/etc/cifs-credentials-ai-image` を疑う
- 詳細は `docs/design/image_gen_nas_setup.md` §8

### 8.3 Dispatcher が queued から動かない
考えられる原因:
- Agent 全滅（§8.1）
- capability 照合失敗: ワークフローが要求する capability を満たす Agent がいない
- Agent 側の ComfyUI が未起動 / 別タスクで塞がっている
- `config.yaml` の `windows_agents` が未登録 / 全 `enabled: false`

確認:
```bash
docker logs --since 5m secretary-bot 2>&1 | grep -iE 'dispatch|agent|capability'
```

### 8.4 DB migration エラー
症状: bot 起動ログに `OperationalError` / `migration` 関連エラー、`user_version` が 26 未満で止まる。

1. `/home/iniwa/docker/secretary-bot/data/` の DB ファイルのバックアップを取る
2. 直前の `user_version` を確認: `sudo sqlite3 <db> "PRAGMA user_version;"`
3. bot ログに失敗した SQL 文が出ているはず → 該当ステートメントを特定
4. `src/database.py` の `_migrations[26]` と比較
5. v25 未満から飛ぶ場合は中間バージョンの migration も全部走る前提。バックアップから再起動し、ログを保存してユーザーに報告

### 8.5 `t2i_base` が workflows プルダウンに出ない
- DB を見て存在するか確認（§5.4）
- 存在するのに UI に出ない → bot 再起動 + ブラウザキャッシュクリア
- DB にも無い → `sync_presets_to_db` の初回実行が失敗。ログに `workflow_mgr` 絡みのエラーが出ていないか

---

## 9. 完了チェックリスト

### 必須
- [ ] `/mnt/ai-image` 永続マウント成功
- [ ] `.env` に `NAS_SMB_*` 5 変数 + `AGENT_SECRET_TOKEN` が設定
- [ ] `config.yaml` に `image_gen` / `lora_train` / `prompt_session` が `enabled: true`
- [ ] bot 再起動後 `Loaded unit: image_gen` 等がログに出る
- [ ] DB `user_version = 26`、`workflows` に `t2i_base` が seed 済み
- [ ] WebGUI `/api/image/generate` が 1 枚生成できる
- [ ] NAS の `outputs/YYYY-MM/YYYY-MM-DD/` に画像が保存される

### 冗長性検証
- [ ] MainPC → SubPC に priority を入れ替えて再起動 → 同じく生成できる
- [ ] 入れ替えを元に戻す

### 注意事項（今は手動）
- **削除操作**は Phase 2 実装予定。現状、WebGUI の削除ボタンがあっても NAS 実体は残る（または未実装）想定。`lora_work/` や `outputs/` の整理は **ssh で手動 `rm`** すること。
- `models/checkpoints/` 配下は Pi からは書き込み不可の想定。モデル追加は管理者が Windows から。

---

## 10. 次ステップ

- **E2E 最終確認**: `docs/setup/image_gen_verify.md` を実行
  - ComfyUI 側ワークフローが想定通り動くか
  - LoRA 学習パイプライン（`lora_train`）のスモークテスト
  - プロンプトセッション（`prompt_session`）の対話経由起動
  - Discord 投稿（`discord_output_channel_id` を実 ID に設定した場合）
