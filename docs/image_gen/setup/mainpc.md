# MainPC 画像生成セットアップガイド

> 対象: **Main PC（Windows 11 / Ryzen 7 9800X3D / RTX 4080 / 48GB RAM / IP 192.168.1.210）**
> 役割: secretary-bot AgentPool の priority 最上位。ComfyUI + kohya_ss を常駐させる
> 関連: `docs/image_gen/design.md` / `../api.md` / `../nas_setup.md` / `../preset_compat.md`
>
> 本書は Claude Code がこのドキュメントを直接読みながら MainPC 実機で順番に実行することを想定している。各ステップはチェックボックス `- [ ]` で進捗管理し、検証コマンドの出力を確認してからユーザーに報告すること。
>
> **前提**: NAS 側の共有 `ai-image` は `docs/image_gen/nas_setup.md` の手順で初期化済みであること。未初期化なら先にそちらを完了させる。

---

## 0. 作業方針

- シェルは原則 **PowerShell**（昇格）。cmd/バッチを使う場合は明記。
- パスは C: ドライブ記法（`C:/secretary-bot` / `C:\secretary-bot` どちらも可）。
- コマンドが失敗したら、次ステップに進まずユーザーに状況を報告する。
- `[要ユーザー確認]` タグの付いた項目は必ずユーザーに判断を仰ぐ。

---

## 1. 前提チェック

すべて PowerShell（管理者で開く）。

- [ ] OS / ビルドを確認。Windows 11 であること。

    ```powershell
    [System.Environment]::OSVersion
    (Get-CimInstance Win32_OperatingSystem).Caption
    ```

- [ ] GPU が RTX 4080 として認識され、NVIDIA ドライバが入っていること。`nvidia-smi` が使えない場合はドライバ未導入。

    ```powershell
    nvidia-smi
    ```

    期待値: `GeForce RTX 4080`、CUDA Version 表示あり。

- [ ] Python 3.11 が `python --version` or `py -3.11 --version` で取得できること。なければ python.org から 3.11.x をインストール（PATH 登録）。

    ```powershell
    py -0p
    py -3.11 --version
    ```

- [ ] `git --version` が通ること。無ければ `winget install --id Git.Git -e` で導入。

    ```powershell
    git --version
    ```

- [ ] C: ドライブに 100GB 以上の空きがあること（ComfyUI 本体 + venv + モデルキャッシュで最低ラインの目安）。

    ```powershell
    Get-PSDrive C | Select-Object Used,Free,@{n="FreeGB";e={[math]::Round($_.Free/1GB,1)}}
    ```

    `FreeGB` < 100 の場合は **ユーザーに確認**（モデル配置先をキャッシュから外すなどの対応が必要）。

- [ ] secretary-bot リポジトリが `C:/Git/secretary-bot` に clone 済みであること（Main PC は `C:/Git/` 配下が規約）。

    ```powershell
    Test-Path C:/Git/secretary-bot/.git
    git -C C:/Git/secretary-bot rev-parse --abbrev-ref HEAD
    ```

    未 clone なら `[要ユーザー確認]`（認証情報の扱いを聞く）。

- [ ] `windows-agent/config/agent_config.yaml` が存在すること。無ければ example からコピー。

    ```powershell
    $cfg = "C:/Git/secretary-bot/windows-agent/config/agent_config.yaml"
    if (-not (Test-Path $cfg)) {
      Copy-Item "$cfg.example" $cfg
      Write-Host "agent_config.yaml をコピーしました"
    } else {
      Write-Host "agent_config.yaml は既存です"
    }
    ```

- [ ] `windows-agent/config/.env` に `AGENT_SECRET_TOKEN` と `NAS_HOST / NAS_SHARE / NAS_USER / NAS_PASS` が設定されていること（本エージェントの `tools/image_gen/nas_mount.py` は `NAS_HOST / NAS_SHARE / NAS_USER / NAS_PASS` を読む）。

    ```powershell
    $envFile = "C:/Git/secretary-bot/windows-agent/config/.env"
    if (-not (Test-Path $envFile)) {
      Write-Host "NOT FOUND: $envFile  — 新規作成が必要"
    } else {
      Select-String -Path $envFile -Pattern '^(AGENT_SECRET_TOKEN|NAS_HOST|NAS_SHARE|NAS_USER|NAS_PASS)=' | Select-Object Line
    }
    ```

    未設定項目があれば `[要ユーザー確認]`（NAS 認証情報と `AGENT_SECRET_TOKEN` の値を聞く）。

> **切り分け**: 上記いずれかで失敗した場合、**先に進まずユーザーに報告**。特にドライバ / Python / git / .env は以降の全ステップの前提。

---

## 2. ディレクトリ構成の作成

`image_gen` は `root = C:/secretary-bot`、`cache = C:/secretary-bot-cache` を既定とする（`agent_config.yaml` の `image_gen:` セクション）。

- [ ] 作業ルートとキャッシュ領域を作成。

    ```powershell
    New-Item -ItemType Directory -Force -Path C:/secretary-bot | Out-Null
    New-Item -ItemType Directory -Force -Path C:/secretary-bot-cache | Out-Null
    New-Item -ItemType Directory -Force -Path C:/secretary-bot-cache/checkpoints,C:/secretary-bot-cache/loras,C:/secretary-bot-cache/vae,C:/secretary-bot-cache/embeddings,C:/secretary-bot-cache/upscale_models,C:/secretary-bot-cache/clip | Out-Null
    Get-ChildItem C:/secretary-bot-cache
    ```

    期待値: 空ディレクトリが 6 種（checkpoints / loras / vae / embeddings / upscale_models / clip）作成される。

- [ ] ディスク位置が正しいか一応確認（SSD 想定）。

    ```powershell
    Get-Item C:/secretary-bot,C:/secretary-bot-cache | Format-Table FullName,CreationTime
    ```

> **切り分け**: `New-Item` が Access Denied で落ちるときは PowerShell を管理者で開き直す。

---

## 3. ComfyUI インストール

`windows-agent/tools/image_gen/comfyui_manager.py` は以下を想定:

- `root=C:/secretary-bot` 配下に `comfyui/` ディレクトリ
- 起動 Python は **優先順に** `C:/secretary-bot/venv-comfyui/Scripts/python.exe` → 現行 `python`
- 起動引数: `--listen 127.0.0.1 --port 8188 --extra-model-paths-config <comfy_dir>/extra_model_paths.yaml`

このパス構成を崩さないこと。

- [ ] ComfyUI 本体を clone。

    ```powershell
    git clone https://github.com/comfyanonymous/ComfyUI.git C:/secretary-bot/comfyui
    git -C C:/secretary-bot/comfyui log -1 --oneline
    ```

- [ ] ComfyUI 専用 venv を Python 3.11 で作成し有効化。

    ```powershell
    py -3.11 -m venv C:/secretary-bot/venv-comfyui
    C:/secretary-bot/venv-comfyui/Scripts/Activate.ps1
    python --version
    ```

    期待値: `Python 3.11.x`（`(venv-comfyui)` プロンプト表示）。

- [ ] pip アップグレード + **CUDA 12.x 版 PyTorch** インストール（RTX 4080 / sm_89 向け）。インストール URL は cu124 を既定とする。

    ```powershell
    python -m pip install --upgrade pip wheel setuptools
    python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
    ```

    別バージョンの CUDA を入れたい場合は `[要ユーザー確認]`。

- [ ] ComfyUI の依存をインストール。

    ```powershell
    python -m pip install -r C:/secretary-bot/comfyui/requirements.txt
    ```

- [ ] CUDA 利用可能か確認。

    ```powershell
    python -c "import torch; print('cuda', torch.cuda.is_available()); print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'n/a'); print('capability', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None)"
    ```

    期待値: `cuda True` / `device NVIDIA GeForce RTX 4080` / `capability (8, 9)`。`False` の場合は §9.1 参照。

- [ ] 単独起動確認（前景）。`Ctrl+C` で停止できるようにそのまま実行。

    ```powershell
    cd C:/secretary-bot/comfyui
    python main.py --listen 127.0.0.1 --port 8188
    ```

- [ ] 別 PowerShell を開き、`/system_stats` が 200 を返すこと確認。

    ```powershell
    (Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing).StatusCode
    ```

    期待値: `200`。確認したら前景 ComfyUI を `Ctrl+C` で停止。以降は Agent が遅延起動する。

- [ ] `extra_model_paths.yaml` を配置（Agent はこれを `--extra-model-paths-config` で渡す）。当面は **ローカル SSD キャッシュ** を参照させる（設計書「ComfyUI 参照パス」: NAS マウント直読みは禁止）。

    `C:/secretary-bot/comfyui/extra_model_paths.yaml` を以下の内容で作成:

    ```yaml
    secretary_bot_cache:
      base_path: C:/secretary-bot-cache
      checkpoints: checkpoints
      loras: loras
      vae: vae
      embeddings: embeddings
      upscale_models: upscale_models
      clip: clip
    ```

    ```powershell
    $yaml = @"
secretary_bot_cache:
  base_path: C:/secretary-bot-cache
  checkpoints: checkpoints
  loras: loras
  vae: vae
  embeddings: embeddings
  upscale_models: upscale_models
  clip: clip
"@
    Set-Content -Path C:/secretary-bot/comfyui/extra_model_paths.yaml -Value $yaml -Encoding UTF8
    Get-Content C:/secretary-bot/comfyui/extra_model_paths.yaml
    ```

    ※ Phase 2 で自動生成される想定。当面は手動配置で運用する。

- [ ] venv を deactivate して次のセクションへ。

    ```powershell
    deactivate
    ```

> **切り分け**:
> - `pip install torch` で「No matching distribution」: Python 3.12 以上を使っている可能性。`py -3.11` で再試行。
> - ComfyUI 起動時の `OSError: [WinError 10048]`: 既に 8188 を占有しているプロセスあり。`Get-NetTCPConnection -LocalPort 8188` で特定して停止。

---

## 4. kohya_ss (sd-scripts) インストール

LoRA 学習本体。GUI は使わず、CLI (`sdxl_train_network.py` 等) を Agent から叩く前提。

- [ ] sd-scripts 本体を clone。

    ```powershell
    git clone https://github.com/kohya-ss/sd-scripts.git C:/secretary-bot/kohya
    git -C C:/secretary-bot/kohya log -1 --oneline
    ```

- [ ] 独立 venv を Python 3.11 で作成。

    ```powershell
    py -3.11 -m venv C:/secretary-bot/venv-kohya
    C:/secretary-bot/venv-kohya/Scripts/Activate.ps1
    python --version
    ```

- [ ] CUDA 版 PyTorch と依存を導入。

    ```powershell
    python -m pip install --upgrade pip wheel setuptools
    python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
    python -m pip install -r C:/secretary-bot/kohya/requirements.txt
    ```

    ※ sd-scripts の `requirements.txt` 冒頭に torch が pin されているケースがある。上書きされた場合は `python -m pip install --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124` で入れ直す。

- [ ] accelerate の設定（対話）。以下の選択で進める:

    - This machine
    - No distributed training
    - CPU のみ使う? → **NO**
    - multi-GPU? → **NO**
    - DeepSpeed? → **NO**
    - numa efficient? → No
    - How many GPUs? → `1`
    - dtype → **fp16**

    ```powershell
    accelerate config
    ```

    設定ファイルは `C:\Users\<user>\.cache\huggingface\accelerate\default_config.yaml` に書かれる。

- [ ] 動作確認（ヘルプ出力のみ）。エラー無く表示されれば OK。

    ```powershell
    python C:/secretary-bot/kohya/sdxl_train_network.py --help | Select-Object -First 5
    deactivate
    ```

> **切り分け**: `xformers` が依存で入ると CUDA 版 mismatch で失敗することがある。その場合は `pip install xformers --index-url https://download.pytorch.org/whl/cu124` を明示、あるいは `requirements.txt` から一旦外して後で導入。

---

## 5. モデル配置

**正本は NAS**（`<NAS>/ai-image/models/`）。MainPC は NAS を参照しつつ、実際の生成時はローカル SSD キャッシュ (`C:/secretary-bot-cache`) を参照する。

### 5.1 NAS への配置（まだなら）

- [ ] ChenkinNoob-XL-V0.5 の SDXL チェックポイントを入手。`[要ユーザー確認]`: **配布 URL はユーザーに指定してもらう**（Civitai 等、ログインが必要な配布元が多い）。ダウンロード先は NAS `<NAS>/ai-image/models/checkpoints/chenkinNoobXL_v05.safetensors`（ファイル名固定、`../preset_compat.md` §2.2 の要件）。

- [ ] sha256 サイドカー `<file>.sha256` を配置。配布元が sha を公開していればそれを流用、無ければ NAS 側で計算。

    PowerShell 側で NAS ドライブ（次節でマウント）経由で計算する例:

    ```powershell
    $f = "N:/models/checkpoints/chenkinNoobXL_v05.safetensors"
    $hash = (Get-FileHash -Algorithm SHA256 $f).Hash.ToLower()
    [System.IO.File]::WriteAllText("$f.sha256", $hash)
    Write-Host $hash
    ```

- [ ] VAE の要否を確認。`../preset_compat.md` §2.2 の通り SDXL 同梱 VAE で基本 OK。外部 VAE を使うプリセットを使用する場合は `<NAS>/ai-image/models/vae/` に配置する。`[要ユーザー確認]`: 外部 VAE 利用予定の有無をユーザーに聞く。

### 5.2 MainPC キャッシュへのコピー

- [ ] **原則コピー不要**。初回生成ジョブ投入時に Pi 側 `model_sync` が `/cache/sync` を叩いて NAS → `C:/secretary-bot-cache/checkpoints/` に自動コピーする。手動で事前投入したい場合のみ、マウント済み NAS から直接コピー:

    ```powershell
    Copy-Item "N:/models/checkpoints/chenkinNoobXL_v05.safetensors" "C:/secretary-bot-cache/checkpoints/" -Force
    Copy-Item "N:/models/checkpoints/chenkinNoobXL_v05.safetensors.sha256" "C:/secretary-bot-cache/checkpoints/" -Force
    Get-ChildItem C:/secretary-bot-cache/checkpoints
    ```

> **切り分け**: NAS 経由のコピーで 1GbE + HDD だと SDXL 7GB ≒ 1 分。極端に遅い（5 分以上）場合は NAS 側のディスク負荷 / SMB バージョンを確認（`../nas_setup.md` §8.1）。

---

## 6. Windows Agent 設定

既存 agent.py は `_detect_role()` が `AGENT_ROLE` 環境変数 → IP ベース判定の順で役割を決める。MainPC は `192.168.1.210` なので既定で `main` と判定される（`windows-agent/agent.py` の `AGENT_ROLE_MAP` 既定値）。通常 `AGENT_ROLE` の明示は不要。

### 6.1 `.env`

- [ ] `windows-agent/config/.env` を以下のキーが揃っているように編集する（§1 で確認済み、値が未設定なら `[要ユーザー確認]` で埋める）。

    ```dotenv
    AGENT_SECRET_TOKEN=<共有秘密トークン（Pi 側と同一）>

    # NAS (image_gen / STT 共通)
    NAS_HOST=192.168.1.xx
    NAS_SHARE=secretary-bot
    NAS_USER=<nas-user>
    NAS_PASS=<nas-password>
    ```

    ※ `nas_mount.py` は `NAS_PASS` 優先・`NAS_PASSWORD` フォールバック。どちらでも良いが **`NAS_PASS` 推奨**（`start_agent.bat` が読むのも `NAS_PASS`）。

- [ ] `.env` の権限を絞る（Windows なので主に ACL 任せだが、ユーザー以外が読めないこと）。

    ```powershell
    icacls "C:/Git/secretary-bot/windows-agent/config/.env"
    ```

    不特定の `Users` グループに Read が付いていたら `[要ユーザー確認]`。

### 6.2 `agent_config.yaml`

- [ ] `image_gen:` セクションを以下に合わせる（example から変更すべき箇所のみ列挙）。`windows-agent/config/agent_config.yaml.example` を参照し、**enabled=true / root / cache / kohya.enabled / nas.mount_drive=N:** を確認。

    ```yaml
    image_gen:
      enabled: true
      root: "C:/secretary-bot"
      cache: "C:/secretary-bot-cache"
      comfyui:
        host: "127.0.0.1"
        port: 8188
        startup_timeout_seconds: 60
        health_check_interval_seconds: 30
        crash_restart_max_retries: 3
      kohya:
        enabled: true
      cache_lru:
        max_size_gb: 100
      nas:
        host: ""           # 空のままで OK（.env の NAS_HOST が使われる）
        share: "secretary-bot"
        subpath: "ai-image"
        mount_drive: "N:"
    ```

- [ ] 反映確認:

    ```powershell
    Select-String -Path C:/Git/secretary-bot/windows-agent/config/agent_config.yaml -Pattern 'enabled|root|cache|mount_drive' -Context 0,0 | Select-Object -First 20
    ```

### 6.3 Role の明示（任意）

- [ ] 通常不要。IP が 192.168.1.210 以外で動かす場合のみ、PowerShell から起動前に `$env:AGENT_ROLE = "main"` を設定するか、`start_agent.bat` の先頭に `set AGENT_ROLE=main` を追加。`[要ユーザー確認]`。

---

## 7. NAS SMB マウント

Agent 起動時に `start_agent.bat` と `tools/image_gen/nas_mount.py` の両方がマウントを試みるが、**ドライブレターが異なる**ことに注意:

- `start_agent.bat`: `\\NAS_HOST\NAS_SHARE` を **ドライブレター無し**で接続（認証情報キャッシュ目的）
- `nas_mount.py`: `agent_config.yaml` の `image_gen.nas.mount_drive`（既定 `N:`）に割り当て

本番で使うのは `N:`。先に手動マウントしておくと、初回の Agent 起動がスムーズ。

- [ ] 現状のマッピング確認。

    ```powershell
    net use
    Get-SmbMapping
    ```

- [ ] `N:` に割り当て（NAS 認証情報は `.env` と一致させる）。`[要ユーザー確認]`: 対話で資格入力する場合は `Get-Credential` を使う。

    ```powershell
    # 対話版（推奨）
    $cred = Get-Credential -UserName "<nas-user>" -Message "NAS (secretary-bot) credentials"
    New-SmbMapping -LocalPath "N:" -RemotePath "\\192.168.1.xx\secretary-bot" `
      -UserName $cred.UserName -Password $cred.GetNetworkCredential().Password `
      -Persistent $true

    # 非対話版（スクリプト内平文、非推奨）
    # net use N: \\192.168.1.xx\secretary-bot /user:<user> <pw> /persistent:yes
    ```

- [ ] マウント検証。`ai-image/` と `auto-kirinuki/` が見えること。

    ```powershell
    Get-ChildItem N:\
    Get-ChildItem N:\ai-image
    Get-ChildItem N:\ai-image\outputs -ErrorAction SilentlyContinue
    ```

    期待値: `ai-image/`, `auto-kirinuki/` が並び、`ai-image` 配下に `models`, `outputs`, `lora_datasets`, `lora_work`, `workflows`, `snapshots` が並ぶ。見えない場合は NAS 側の共有初期化が未完了 → `docs/image_gen/nas_setup.md` §2 を実施。

- [ ] 書き込みテスト（MainPC は `outputs/`, `lora_work/`, `models/loras/` に書き込み可のはず）。

    ```powershell
    "test $(Get-Date -Format o)" | Out-File N:\ai-image\outputs\_mainpc_write_test.txt
    Get-Content N:\ai-image\outputs\_mainpc_write_test.txt
    Remove-Item N:\ai-image\outputs\_mainpc_write_test.txt
    ```

> **切り分け**:
> - `システム エラー 1326`（認証失敗）: ユーザー名 / パスワード間違い、もしくは資格情報マネージャーに古いエントリ。`cmdkey /list` で確認し `cmdkey /delete:192.168.1.xx` で削除してからリトライ。
> - `N:` が既に別共有にマッピング済み: `Remove-SmbMapping -LocalPath N: -Force` で解除してから再設定。

---

## 8. 起動

- [ ] `windows-agent/start_agent.bat` をダブルクリック or PowerShell から実行（スクリプトが自己昇格する）。

    ```powershell
    cd C:/Git/secretary-bot/windows-agent
    .\start_agent.bat
    ```

- [ ] 起動ログで以下のキーを確認（順不同）。

    - `NAS connected: \\192.168.1.xx\secretary-bot`（`start_agent.bat` の最初）
    - `[Agent] Role: main`
    - `[Agent] image_gen init failed: ...` が **出ていない** こと（ComfyUI 本体は遅延起動のため、ここで ComfyUI プロセスは立ち上がらない）
    - FastAPI が `Uvicorn running on http://0.0.0.0:7777` を表示

- [ ] 別 PowerShell から `/capability` を叩く。`.env` の `AGENT_SECRET_TOKEN` を渡すこと。

    ```powershell
    $tok = (Select-String -Path C:/Git/secretary-bot/windows-agent/config/.env -Pattern '^AGENT_SECRET_TOKEN=' | ForEach-Object { ($_ -split '=',2)[1] }).Trim()
    Invoke-RestMethod -Uri http://127.0.0.1:7777/tools/image-gen/capability -Headers @{ "X-Agent-Token" = $tok } | ConvertTo-Json -Depth 4
    ```

    期待値: `agent_id: main-pc` / `role: main` / `comfyui_available: false`（ここでは OK）/ `has_kohya: true` / `nas.mounted: true` / `gpu_info.name` に RTX 4080 / `cuda_compute: 8.9` / `cache_usage.limit_gb: 100`。

    ※ 実際のルート URL は `windows-agent/agent.py` の router 取り付け位置に依存する。`/tools/image-gen/...` が 404 の場合は `/capability` 直下を試し、それでもダメなら `agent.py` を確認。

- [ ] ComfyUI を明示起動して `/system_stats` が 200 になることを確認。

    ```powershell
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7777/tools/image-gen/comfyui/restart -Headers @{ "X-Agent-Token" = $tok }
    Invoke-RestMethod -Uri http://127.0.0.1:7777/tools/image-gen/comfyui/status -Headers @{ "X-Agent-Token" = $tok } | ConvertTo-Json -Depth 3
    (Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing).StatusCode
    ```

    期待値: `running: true`, `available: true`, `pid` が整数、`/system_stats` = 200。

- [ ] もう一度 `/capability` を叩き、`comfyui_available: true` になること。

> **切り分け**:
> - `/capability` 応答で `nas.mounted: false`: §7 のマウントに失敗している。Agent を落として手動で `New-SmbMapping` → Agent 再起動。
> - `comfyui/restart` が `ResourceUnavailableError: ComfyUI not installed`: `comfyui_manager.py` の `_resolve_entry` が `C:/secretary-bot/comfyui/main.py` を見つけられていない。パスとディレクトリ名（`comfyui` 小文字）を再確認。
> - `comfyui/restart` が `did not become ready`: ポート競合 / CUDA エラーの可能性。`/tools/image-gen/comfyui/status` の `recent_logs` に ComfyUI の stdout が入るので、そこから判断。

---

## 9. トラブルシュート（局所）

### 9.1 `torch.cuda.is_available() == False`

- NVIDIA ドライバが古い（最低でも 535 系以上を推奨）: `nvidia-smi` の `Driver Version` を確認し、新しすぎる CUDA Toolkit が入っていても通常は問題ない（PyTorch は同梱）。
- 誤って CPU 版 torch が入った: `pip show torch` の Location と Version を確認し、`Version: X.Y.Z+cpu` なら `pip uninstall torch torchvision torchaudio` → cu124 版を再導入。
- 仮想環境を混同: venv-comfyui と venv-kohya は独立。`where python` で現在のパスを確認する。

### 9.2 VRAM 不足（`CUDA out of memory`）

- RTX 4080 (16GB) は SDXL 1024×1024 なら余裕があるが、他アプリで VRAM を消費していると落ちる。
- `nvidia-smi` で占有プロセスを確認し、ブラウザ / ゲームなど停止。
- ComfyUI 側で `--lowvram` / `--medvram` を付けるのは `comfyui_manager.py` の `_resolve_entry` を改修する必要があるため、Phase 2 で対応。当面は他プロセスを止める運用。

### 9.3 NAS マウント失敗

- `../nas_setup.md` §8 を参照。
- `.env` の `NAS_PASS` に `%` や `$` が含まれると cmd の `net use` でエスケープが必要になる。パスワードを英数ベースに変えるか、PowerShell の `New-SmbMapping` を使う（§7）。

---

## 10. 次ステップ

- [ ] Sub PC セットアップ: `docs/image_gen/setup/subpc.md`（Main と差分は priority 値とモデル同期タイミングのみ想定）
- [ ] Pi セットアップ: `docs/image_gen/setup/pi.md`（Pi 側の `image_gen` / `workflow_mgr` ユニット設定、プリセット登録）
- [ ] `docs/image_gen/preset_compat.md` §4 に従い、Sub PC セットアップ完了後に MainPC と `/capability` を付き合わせる。差分があれば ComfyUI Manager Snapshot で同期。
