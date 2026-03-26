# CLAUDE.md

> 設計の詳細・背景は `plan.md` を参照。

## Communication
- User writes in Japanese; **respond in Japanese**.
- Write lightweight, efficient code. Prefer minimal dependencies.

## Environment
- Host: Raspberry Pi 4 (8GB RAM), `linux/arm64`
- Docker management: Portainer — Stack Web Editor only
- Windows PCs (× 2): Ollama + Windows Agent（Dockerなし・Pythonネイティブ）

## Build & Deploy
- Build target: `linux/arm64`
- Image: `ghcr.io/iniwa/secretary-bot:latest`
- **イメージにはPythonランタイムとライブラリのみ**を含める（コードは含めない）
- GitHub Actionsのトリガーは `Dockerfile` と `requirements.txt` の変更時のみ

```yaml
# .github/workflows/docker-publish.yml
on:
  push:
    branches: [main]
    paths:
      - 'Dockerfile'
      - 'requirements.txt'
```

- コード変更時はWebGUIの「コード更新」ボタン（git pull + Portainer API再起動）で対応
- Dockerfileに `git` のインストールが必要（WebGUIからgit pullを実行するため）

### Dockerfileアウトライン

```dockerfile
FROM python:3.11-slim

# システム依存パッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# 非rootユーザー作成
RUN useradd --create-home --shell /bin/bash botuser

WORKDIR /app

# 依存ライブラリ（キャッシュ効率のため先にコピー）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコード・config.yamlはVolumeマウント（COPYしない）

RUN chown -R botuser:botuser /app
USER botuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')" || exit 1

CMD ["python", "-m", "src.bot"]
```

## Storage
| Data | Path | Backend |
|------|------|---------|
| ソースコード（Volumeマウント） | `/home/iniwa/docker/secretary-bot/src` | SSD |
| 設定ファイル（Volumeマウント） | `/home/iniwa/docker/secretary-bot/config.yaml` | SSD |
| SQLite / ChromaDB | `/home/iniwa/docker/secretary-bot/data` | SSD |

**初回セットアップ:**
```bash
cd /home/iniwa/docker/secretary-bot
git clone https://github.com/iniwa/secretary-bot src
cp src/.env.example .env && nano .env
cp src/config.yaml.example config.yaml && nano config.yaml
```

**docker-compose.yml のVolume設定:**
```yaml
services:
  secretary-bot:
    image: ghcr.io/iniwa/secretary-bot:latest
    volumes:
      - /home/iniwa/docker/secretary-bot/src:/app/src
      - /home/iniwa/docker/secretary-bot/config.yaml:/app/config.yaml
      - /home/iniwa/docker/secretary-bot/data:/app/data
    ports:
      - "8100:8100"
    env_file:
      - /home/iniwa/docker/secretary-bot/.env
    restart: unless-stopped
    environment:
      - TZ=Asia/Tokyo
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

## 環境変数（`.env`）

APIキー・トークン等の機密情報はすべて `.env` で管理する。
`config.yaml` には機密情報を含めない。PortainerのStack Web Editorで `env_file` を指定して注入する。

### `.env.example`

```bash
# === LLM ===
GEMINI_API_KEY=your-gemini-api-key-here

# === Discord ===
DISCORD_BOT_TOKEN=your-discord-bot-token-here
DISCORD_ADMIN_CHANNEL_ID=123456789012345678

# === WebGUI ===
WEBGUI_USERNAME=admin
WEBGUI_PASSWORD=your-password-here
WEBGUI_PORT=8100

# === Portainer ===
PORTAINER_URL=http://192.168.1.1:9000
PORTAINER_API_TOKEN=your-portainer-api-token-here
PORTAINER_STACK_ID=1

# === Windows Agent ===
AGENT_SECRET_TOKEN=your-shared-secret-here
```

### Pythonでの読み込み

```python
import os
# docker-compose の env_file で自動ロードされるため os.environ から直接読む
gemini_key = os.environ["GEMINI_API_KEY"]
discord_token = os.environ["DISCORD_BOT_TOKEN"]
```

## Architecture

```
[Discord] [WebGUI]
    ↓         ↓
[Skill Router]        ← LLMがどのユニットを使うか判断（JSON返却）
    ↓
[Unit Manager]        ← ユニットを自動ロード・管理
    ├── Pi上のUnit    → そのまま実行
    └── DELEGATE_TO="windows" → RemoteUnitProxy → AgentPool → Windows Agent

[Heartbeat]           ← 適応型頻度制御（Ollama有無で間隔切り替え）
[LLM Router]          ← Ollama優先 → Gemini APIフォールバック
[AgentPool]           ← 複数Windows PCをpriority順に管理
[Windows Delegate]    ← VictoriaMetrics APIで負荷確認・委託可否判定
[SQLite]              ← 全データ永続化
[ChromaDB]            ← インプロセス（PersistentClient）・ベクトル記憶
[WebGUI]              ← FastAPI + レスポンシブHTML（PCファースト）
```

## Project Structure

```
secretary-bot/
├── src/
│   ├── bot.py                # エントリーポイント（グレースフルシャットダウン含む）
│   ├── skill_router.py       # 自然言語 → ユニット振り分け
│   ├── heartbeat.py          # ハートビート・コンテキスト圧縮
│   ├── errors.py             # BotError基底クラス・エラー分類
│   ├── circuit_breaker.py    # ユニット単位のサーキットブレーカー
│   ├── logger.py             # 構造化ログ（JSON・trace_id付き）
│   ├── database.py           # SQLite（aiosqlite・WALモード）
│   ├── llm/
│   │   ├── router.py         # Ollama/Gemini切り替え
│   │   ├── ollama_client.py
│   │   └── gemini_client.py
│   ├── memory/
│   │   ├── chroma_client.py  # ChromaDB操作（インプロセス・PersistentClient）
│   │   ├── ai_memory.py      # AI自身の記憶（Ollama専用）
│   │   └── people_memory.py  # 人物記憶（Geminiフォールバック可）
│   ├── units/
│   │   ├── base_unit.py      # BaseUnit（SKILL_NAME, DELEGATE_TO等を定義）
│   │   ├── remote_proxy.py   # 透過的な委託ラッパー
│   │   ├── agent_pool.py     # 複数PC管理・フォールバック
│   │   ├── reminder.py
│   │   ├── memo.py
│   │   ├── timer.py
│   │   ├── status.py
│   │   └── chat.py           # 雑談・相談（フォールバック先）
│   └── web/
│       ├── app.py            # FastAPI（WebGUI + /health エンドポイント）
│       └── static/           # レスポンシブHTML/CSS/JS
├── windows-agent/
│   ├── agent.py              # FastAPI（/health, /version, /update, /units, /execute/{unit}）
│   ├── units/                # Windows側ユニット
│   ├── requirements.txt
│   └── start_agent.bat       # PC起動時: git pull → python agent.py
├── Dockerfile                # arm64向け・非rootユーザー（botuser）
├── config.yaml.example       # 設定テンプレート（ダミー値）
├── .env.example              # 環境変数テンプレート（ダミー値）
├── docker-compose.yml
└── .claudeignore
```

## Key Implementation Rules

### Unit追加方法
1. `src/units/my_unit.py` を作成し `BaseUnit` を継承
2. `config.yaml` の `units:` に追記
3. 自動ロードされる（Unit Manager側の変更不要）

### Discord通知の方針
**[DECISION] 各ユニットが `BaseUnit` のヘルパー経由でDiscordへ直接送信する。「Discordユニット」は作らない。**
- `BaseUnit` は `discord.py` の `Cog` を継承しており、各ユニットはDiscordへの送信能力を内包
- ユニット間の依存関係をなくすため、別ユニットを経由する方式は採用しない
- 各ユニットは `await self.notify("...")` を呼ぶだけでよい（送信の実装は `BaseUnit` に隠蔽）

```python
# BaseUnitが提供するヘルパー（各ユニットはこれだけ使う）
async def notify(self, message: str): ...        # 通常通知
async def notify_error(self, message: str): ...  # エラー通知
```

### Windows委託の指定
```python
class HeavyUnit(BaseUnit):
    DELEGATE_TO = "windows"       # この1行だけでWindows委託になる
    PREFERRED_AGENT = "pc-main"   # 省略可・省略時はpriority順
```

### Windows Agentバージョンチェック

委託時に毎回 `git pull` を実行するのではなく、**バージョンチェック方式**を採用する。

```python
# agent_pool.py での委託前チェック（イメージ）
async def ensure_agent_updated(self, agent):
    remote_version = await agent.get_version()   # GET /version
    local_version = get_local_commit_hash()       # git rev-parse HEAD
    if remote_version != local_version:
        await agent.update()                      # POST /update → git pull
```

Windows Agentのエンドポイント:
- `GET /version` — 現在のコミットハッシュを返却
- `POST /update` — `git pull` を実行して最新化
- PC起動時の `start_agent.bat` でも `git pull` を1回実行（ブートストラップ）

### エラーハンドリング原則
- 全エラーは `BotError` を継承して定義（`errors.py`）
- ユニットはサーキットブレーカーで保護（連続失敗 → 一時停止 → 自動復帰）
- 重大度に応じてDiscord管理チャンネルへ通知
- 構造化ログに `trace_id` を付与して追跡可能にする
- `dry_run: true` でLLM呼び出しをモック化できるようにする

### Skill Router
- LLMにスキル一覧（SKILL_NAME + SKILL_DESCRIPTION）と入力を渡す
- LLMはJSON形式で返却: `{ "skill": "xxx", "parsed": { ... } }`
- JSON以外が返ってきた場合は `chat` ユニットにフォールバック

### ハートビート
- Ollama稼働中: `interval_with_ollama_minutes`（デフォルト15分）
- Ollama停止中: `interval_without_ollama_minutes`（デフォルト180分）
- 実行のたびに次回をスケジュールし直す（固定cronではなく動的スケジュール）
- 会話履歴が `compact_threshold_messages` を超えたらLLMで要約・圧縮

### Windows Agent通信
- Pi → Windows: HTTP REST（ローカルLANのみ・外部公開なし）
- エンドポイント: `GET /health`, `GET /version`, `POST /update`, `GET /units`, `POST /execute/{unit}`（ポート: **7777**）
- 委託前にVictoriaMetrics APIでCPU・メモリ使用率を確認
- 委託前にバージョンチェック（不一致時のみ `/update` で `git pull`）
- 委託モード（許可/拒否/自動）はWebGUIでPC単位に設定

### WebGUI
- **PCファースト**のレスポンシブデザイン
- PCはサイドバー、スマホはボトムナビゲーション（主要機能のみ）
- チャット送信 → Skill Routerを通してDiscordに返答（WebGUIには「送信完了」のみ表示）
- アクセス: ローカルLAN内のみ・Basic認証
- 外部公開時: Cloudflare Tunnel + Cloudflare Access（メールアドレス認証）で自分以外のアクセスを遮断

### ヘルスチェック

Bot本体（FastAPI）に `/health` エンドポイントを実装する。

```python
# web/app.py
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": get_commit_hash(),
        "uptime": get_uptime_seconds()
    }
```

- docker-compose の `healthcheck:` から30秒間隔で呼び出し
- Portainerでコンテナの死活状態を可視化

### グレースフルシャットダウン

Bot停止時（SIGTERM受信・Portainer再起動）にデータを保護する。

```python
# bot.py でのシャットダウンシーケンス
async def graceful_shutdown():
    logger.info("シャットダウン開始...")
    scheduler.shutdown(wait=True)    # APSchedulerのジョブ停止
    # 実行中ユニットの完了待機（タイムアウト10秒）
    await database.close()           # aiosqlite接続クローズ
    await bot.close()                # Discord切断
    logger.info("シャットダウン完了")
```

- SQLiteは **WALモード** で運用（不意のクラッシュでもデータ破損リスク最小化）
- ChromaDB（PersistentClient）は自動的にディスクにフラッシュ
- `signal.SIGTERM` をハンドルし、FastAPIの `on_shutdown` イベントも利用

## 返答ログ閲覧

- 保存先: SQLite `conversation_log` テーブル（送受信のたびにリアルタイム保存）
- ChromaDBは圧縮サマリーのみ・SQLiteが生ログ担当（役割分離）
- 表示項目: タイムスタンプ・発言者・本文・チャンネル（discord/webgui）・使用ユニット
- 省エネモードの返答には「⚡ 省エネ」バッジを表示
- キーワード検索・日付フィルター・50件ページネーション
- システムプロンプト（ペルソナ・記憶注入）は保存しない
- `verbose_logging: true` 時のみLLMの生リクエスト/レスポンスを保存

```sql
-- 会話ログ（生ログ）
CREATE TABLE conversation_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel   TEXT NOT NULL,  -- 'discord' | 'webgui'
    role      TEXT NOT NULL,  -- 'user' | 'assistant'
    content   TEXT NOT NULL,
    mode      TEXT,           -- 'normal' | 'eco'
    unit      TEXT            -- 使用ユニット名
);

-- コンテキスト圧縮済みサマリー（ハートビートで生成）
CREATE TABLE conversation_summary (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    summary    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### コード更新ボタン（メンテナンスページ）
- WebGUIから `git pull` を実行 → Portainer APIでスタック再起動
- 結果（差分あり/なし/エラー）をWebGUI上に表示
- FastAPIから `subprocess` で `git pull` を実行し、Portainer REST APIで再起動をトリガー
- 現在のコミットハッシュ・最終更新日時も表示する

## Security

GitHubパブリックリポジトリのため、以下を**必ず**守ること。

### コミット禁止
- `.env` / `config.yaml`（実体）/ `data/` / `*.key` / `*.pem` を `.gitignore` に含める
- APIキー・トークン・IPアドレス・パスワードを**コードにハードコードしない**
- `config.yaml.example`（ダミー値）と `.env.example`（ダミー値）のみリポジトリに含める

### 機密情報の管理場所
| 情報 | 管理方法 |
|------|---------|
| Gemini APIキー | `.env` + GitHub Secrets |
| Discord Bot Token | `.env` + GitHub Secrets |
| Portainer APIトークン | `.env` |
| WebGUI認証パスワード | `.env` |
| Windows Agent通信トークン | `.env` |

### Windows Agent認証
- Pi → Windows Agent のリクエストに `X-Agent-Token` ヘッダーを付与
- トークンは `.env` で管理・ハードコード禁止

### その他
- コンテナはrootで動かさない（`botuser` ユーザーを作成）
- ログに機密情報を出力しない（verbose時もマスキング）
- GitHub: Secret scanning・Dependabot を有効化
- WebGUI: Basic認証必須
- 外部公開時: Cloudflare Tunnel + Cloudflare Access で保護（自分以外アクセス不可）

## 開発フロー

1. **全機能を一気に実装**してから
2. **ユニット単位で1つずつデバッグ**していく

### デバッグ順序
```
① bot.py起動（Discord接続）
② database.py（SQLite・WALモード）
③ logger.py / errors.py（ログ基盤）
④ llm/router.py（Ollama・Geminiフォールバック）
⑤ memory/（ChromaDB・インプロセス）
⑥ skill_router.py（振り分け）
⑦ 各Unit（reminder → memo → timer → status → chat）
⑧ heartbeat.py
⑨ windows-agent/（バージョンチェック含む）
⑩ web/（WebGUI・ヘルスチェック）
```


## LLM利用方針

### 処理ごとのLLM割り当て

| 処理 | 通常時（Ollama有） | Ollama停止時 |
|------|-----------------|-------------|
| 人格・会話生成 | Ollama | 省エネモード or 縮退（`gemini.conversation` トグル次第） |
| `ai_memory` 記憶形成 | Ollama専用 | スキップ（Gemini不可・絶対） |
| `people_memory` / `conversation_log` 抽出 | Ollama優先 | Gemini（`gemini.memory_extraction` トグル次第） |
| Skill Router | Ollama優先 | Gemini（`gemini.skill_routing` トグル次第） |
| システム通知・タイマー・リマインダー | Ollama優先 | Gemini or テンプレート文 |

### Geminiトグル（WebGUIで設定・全項目デフォルトOFF）

```python
# config.yaml に反映される設定値
gemini:
  conversation: false        # 省エネモードでの会話生成
  memory_extraction: false   # people_memory / conversation_log の抽出
  skill_routing: false       # Skill RouterのLLM判断
  monthly_token_limit: 0     # 0=無制限。超えたら全トグル自動OFF
```

高額課金防止のため**全項目デフォルトOFF**。意識的にONにした処理だけGeminiが有効。
月間トークン上限に達したら全トグル自動OFF + Discord管理チャンネルへ通知。

### 省エネモード（Ollama停止 + `gemini.conversation: true` の場合）

- ペルソナ注入なし・軽量プロンプトでGeminiが返答を生成
- 注入する記憶は `people_memory` のみ（`ai_memory` は除外）
- 返答の冒頭に「現在省エネ稼働中です。」を自動付与
- `gemini.conversation: false` の場合は縮退メッセージを返して終了

## Character & Memory

### キャラクター
- `config.yaml` の `character.persona` にキャラクター定義を記述
- **`ai_memory` への書き込みはOllama必須・Gemini不可**（人格の一貫性を守るため）
- 省エネモードはあくまで折衷案。人格品質はOllama利用時より低下することを許容する

**ペルソナ定義（実装時に `config.yaml` に記述すること）**
- 自認：女性（強調しすぎない）
- 一人称：「僕」固定（「私」「わたし」は使わない）
- 口調：フレンドリーな砕けた敬語（「〜ですよ」「〜じゃないですかね」）
- 性格：理論的・現実主義・ちょっとだけ毒舌
- 禁止：過度な甘やかし・無駄な称賛・感情的な発言

### 記憶システム（ChromaDB — インプロセス）
- SQLite：構造化データ（ToDo・リマインダー）
- ChromaDB：自然言語記憶（AI体験・人物情報・会話ログ）
- **インプロセス（PersistentClient）モード**で稼働（別コンテナ不要）
- ベクトル化はChromaDB内蔵モデルが担う（Ollama/Gemini不要・常時動作）
- 応答生成前に関連記憶をベクトル検索してシステムプロンプトに注入

```python
# chroma_client.py での初期化
import chromadb
client = chromadb.PersistentClient(path="/app/data/chromadb")
```

```
src/memory/
├── chroma_client.py    # ChromaDB操作（インプロセス・PersistentClient）
├── ai_memory.py        # AI自身の記憶（Ollama専用）
└── people_memory.py    # 人物記憶（Geminiフォールバック可）
```

ChromaDBデータ永続化先: `/home/iniwa/docker/secretary-bot/data/chromadb/`

### ChromaDBコレクションとOllama停止時の挙動

| コレクション | 内容 | Ollama停止時の書き込み |
|------------|------|----------------------|
| `ai_memory` | ミミ自身の体験・感情・気づき | ❌ スキップ（Gemini不可） |
| `people_memory` | いにわさんの情報・好み | ✅ Gemini可（トグルON時） |
| `conversation_log` | 会話サマリー | ✅ Gemini可（トグルON時） |

省エネモード時の記憶**読み込み**は `people_memory` のみ注入（`ai_memory` は除外）。

## Tech Stack
| 項目 | 採用 |
|------|------|
| 言語 | Python 3.11 |
| Discord | discord.py v2 |
| Web | FastAPI |
| DB（構造化） | SQLite（aiosqlite・WALモード） |
| DB（記憶） | ChromaDB（インプロセス・PersistentClient） |
| スケジューラ | APScheduler |
| LLM | Ollama API（qwen3）/ Google Generative AI SDK |

## New Tool Checklist
- [ ] arm64-compatible base image (`python:3.11-slim`)
- [ ] Non-root user (`botuser`)
- [ ] `TZ=Asia/Tokyo` in environment
- [ ] `restart: unless-stopped`
- [ ] `healthcheck:` in docker-compose.yml
- [ ] `env_file:` in docker-compose.yml（Portainer Stack Web Editor対応）
- [ ] Image: `ghcr.io/iniwa/secretary-bot:latest`
- [ ] GitHub Actions workflow at `.github/workflows/docker-publish.yml`
- [ ] `.env.example` in project root
- [ ] `config.yaml.example` in project root
- [ ] `.claudeignore` in project root
- [ ] Graceful shutdown（SIGTERM → DB close → Bot close）
- [ ] `/health` endpoint on FastAPI
- [ ] Verify deployment via Portainer Stack
