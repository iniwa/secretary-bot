# CLAUDE.md

## Communication
- User writes in Japanese; **respond in Japanese**.
- Write lightweight, efficient code. Prefer minimal dependencies.

## Work Location Detection
- Working in `D:/Git/` → **Home (Sub PC)**
- Working in `C:/Git/` → **Home (Main PC)**
- Working in `C:/Users/**/Documents/git/` → **Remote PC**
  - Remote PC lacks required environments. Focus on code adjustments only.
- Can SSH into Raspberry Pi via `ssh iniwapi` to read code/logs from the Pi

## Environments

### Raspberry Pi 4 (Bot Host)
- RAM: 8GB, Arch: `linux/arm64`
- Docker management: Portainer — Stack Web Editor only
- SSH: `ssh iniwapi`

### Main PC
| Item | Detail |
|------|--------|
| CPU | Ryzen 7 9800X3D |
| GPU | RTX 4080 — CUDA available |
| RAM | 48GB |
| OS | Windows 11 |
| IP | 192.168.1.210 |

### Sub PC
| Item | Detail |
|------|--------|
| CPU | Ryzen 9 5950X |
| GPU | RTX 5060 Ti — 16GB VRAM, CUDA Compute 8.9 (sm_89) |
| RAM | 64GB |
| OS | Windows 11 |
| IP | 192.168.1.211 |

### Windows PC共通
- Ollama + Windows Agent（Dockerなし・Pythonネイティブ）
- Ollama: `:11434`, Windows Agent: `:7777`

## Build & Deploy
- Build target: `linux/arm64`
- Image: `ghcr.io/iniwa/secretary-bot:latest`
- **イメージにはPythonランタイムとライブラリのみ**を含める（コードは含めない）
- GitHub Actionsのトリガーは `Dockerfile` と `requirements.txt` の変更時のみ
- コード変更時はWebGUIの「コード更新」ボタン（git pull + Portainer API再起動）で対応
- Dockerfileに `git` のインストールが必要（WebGUIからgit pullを実行するため）
- Resource limits: `deploy.resources.limits.memory` を検討（8GB共有）

## Storage
| Data | Path | Backend |
|------|------|---------|
| ソースコード（Volume） | `/home/iniwa/docker/secretary-bot/src` | SSD |
| 設定ファイル（Volume） | `/home/iniwa/docker/secretary-bot/config.yaml` | SSD |
| SQLite / ChromaDB | `/home/iniwa/docker/secretary-bot/data` | SSD |

## 環境変数
- APIキー・トークン等の機密情報はすべて `.env` で管理
- `config.yaml` には機密情報を含めない
- `.env.example` にダミー値のテンプレートを用意

## Architecture

```
[Discord] [WebGUI]
    ↓         ↓
[Unit Router]         ← LLMがどのユニットを使うか判断（JSON返却）
    ↓
[Unit Manager]        ← ユニットを自動ロード・管理
    ├── Pi上のUnit    → そのまま実行
    └── DELEGATE_TO="windows" → RemoteUnitProxy → AgentPool → Windows Agent

[InnerMind]           ← 自律思考（ContextSource プラグインで情報収集）
[Heartbeat]           ← 適応型頻度制御（Ollama有無で間隔切り替え）
[ActivityDetector]    ← ゲーム/OBS/VC 状態でLLM処理抑制
[LLM Router]          ← Ollama優先 → Gemini APIフォールバック
[AgentPool]           ← 複数Windows PCをpriority順に管理
[SQLite]              ← 全データ永続化（src/database/ モジュール群）
[ChromaDB]            ← インプロセス（PersistentClient）・ベクトル記憶
[WebGUI]              ← FastAPI + レスポンシブHTML（PCファースト）
```

## Project Structure

```
secretary-bot/
├── src/
│   ├── bot.py                # エントリーポイント
│   ├── unit_router.py        # 自然言語 → ユニット振り分け
│   ├── heartbeat.py          # ハートビート・コンテキスト圧縮
│   ├── errors.py             # BotError基底クラス
│   ├── circuit_breaker.py    # サーキットブレーカー
│   ├── logger.py             # 構造化ログ（JSON・trace_id）
│   ├── fetch_utils.py        # HTTP取得ユーティリティ
│   ├── flow_tracker.py       # フロー追跡（trace単位の進捗管理）
│   ├── status_collector.py   # Pi / Windows の状態収集
│   ├── database/             # SQLite マイグレーション＋ドメイン別アクセサ
│   │   ├── _base.py          # スキーマ定義・マイグレーション本体
│   │   ├── conversation.py / generation.py / clip_pipeline.py
│   │   ├── lora.py / monologue.py / pending.py / section.py
│   │   ├── settings.py / wildcard.py
│   ├── activity/
│   │   ├── detector.py       # アクティビティ統合判定
│   │   ├── collector.py      # active_pcs / セッション永続化
│   │   ├── agent_monitor.py  # Windows Agent /activity 通信
│   │   ├── discord_monitor.py # Discord VC 検出
│   │   ├── habit_detector.py # 習慣ゲーム離脱検出
│   │   └── daily_diary.py    # 1日の activity ダイジェスト生成
│   ├── gcal/
│   │   ├── service.py / sync.py # Google Calendar API 同期
│   ├── inner_mind/
│   │   ├── core.py / prompts.py / actuator.py / approval_view.py
│   │   ├── discord_activity.py
│   │   └── context_sources/  # プラグイン式情報収集（conversation/memo/
│   │                         #   reminder/memory/weather/rss/stt/
│   │                         #   activity/calendar/github/habit/tavily_news）
│   ├── llm/
│   │   ├── router.py         # Ollama/Gemini切り替え
│   │   ├── ollama_client.py  # 複数インスタンス対応（least-connections）
│   │   ├── gemini_client.py
│   │   ├── gpu_monitor.py    # VictoriaMetrics 経由の GPU 占有検出
│   │   └── unit_llm.py       # purpose別 LLM ラッパー
│   ├── memory/
│   │   ├── chroma_client.py  # ChromaDB（PersistentClient）
│   │   ├── ai_memory.py      # AI自身の記憶（Ollama専用）
│   │   ├── people_memory.py  # 人物記憶（Geminiフォールバック可）
│   │   ├── interest_extractor.py / sweeper.py
│   ├── rss/
│   │   ├── fetcher.py / processor.py / recommender.py / notify.py
│   ├── stt/
│   │   ├── collector.py / processor.py # transcript 収集・要約
│   ├── units/
│   │   ├── base_unit.py      # BaseUnit（Cog継承）
│   │   ├── remote_proxy.py   # 透過的な委託ラッパー
│   │   ├── agent_pool.py     # 複数PC管理
│   │   ├── reminder.py / memo.py / timer.py / status.py / chat.py
│   │   ├── weather.py / web_search.py / rakuten_search.py
│   │   ├── calendar.py / power.py / rss.py
│   │   ├── docker_log_monitor.py # コンテナログ監視
│   │   ├── prompt_crafter.py / model_sync.py
│   │   ├── image_gen/        # 画像生成（ComfyUI 連携）
│   │   ├── clip_pipeline/    # 自動切り抜き（Whisper + Ollama）
│   │   └── lora_train/       # kohya_ss LoRA 学習
│   ├── tools/                # WebGUI 同居ツール（疎結合）
│   │   ├── zzz_disc/         # ZZZ Codex（HoYoLAB連携）
│   │   └── image_gen_console/ # 画像生成コンソール SPA
│   └── web/
│       ├── app.py            # FastAPI（WebGUI + /health）
│       ├── routes/           # ルート群（system/config/inner_mind/image_gen
│       │                     #   /rss/stt/memory/units/activity/docker_monitor
│       │                     #   /flow/obs/input_relay/clip_pipeline/lora_train）
│       └── static/           # index.html / js / css / service-worker.js
├── windows-agent/
│   ├── agent.py              # FastAPI（:7777）
│   ├── activity/             # game_detector / obs_manager
│   ├── stt/                  # mic_capture / whisper_engine / stt_client
│   ├── tools/                # tool_manager + サブツール（input-relay/
│   │                         #   image_gen/clip_pipeline/zzz_disc）
│   ├── config/               # agent_config.yaml / game_processes.json 等
│   └── start_agent.bat
├── docs/                     # 設計判断・運用ノート（必ず巡回）
├── Dockerfile
├── config.yaml.example
├── .env.example
├── docker-compose.yml
└── .claudeignore
```

## Key Rules

### Unit追加
1. `src/units/my_unit.py` を作成し `BaseUnit` を継承（`UNIT_NAME` / `UNIT_DESCRIPTION` / 必要なら `DELEGATE_TO` を定義し、末尾に `async def setup(bot)` を用意）
2. `src/units/__init__.py` の `_UNIT_MODULES` に `"my_unit": "src.units.my_unit"` を追加
3. `config.yaml` の `units.my_unit.enabled: true` を設定
4. 起動時に `UnitManager.load_units()` が `_UNIT_MODULES` を走査し、enabled のものだけ自動ロードする

### Discord通知
- 各ユニットが `BaseUnit` のヘルパー（`notify()` / `notify_error()`）経由で直接送信
- 「Discordユニット」は作らない

### Windows委託
- `DELEGATE_TO = "windows"` の1行で委託化
- バージョンチェック方式（`GET /version` → 不一致時のみ `POST /update`）

### LLM利用方針
- `ai_memory` 書き込みはOllama必須（Gemini不可）
- Geminiトグルは全項目デフォルトOFF（高額課金防止）
- 省エネモード時: ペルソナ注入なし、`people_memory` のみ注入

### LLM並列化ガイドライン
OllamaClientは複数インスタンス対応（least-connections分配）。同時にLLM呼び出しが発生すれば自動的に空きインスタンスへ分配される。

**新しいLLM呼び出しを追加する際の原則:**
- 独立した複数のLLM呼び出しは `asyncio.gather()` で並列実行すること
- ループ内で直列にLLM呼び出しを繰り返さない（`for item in items: await llm_generate(...)` は NG）
  - 代わりに `await asyncio.gather(*[llm_generate(item) for item in items])` を使う
- 依存関係がある場合（前の結果が次の入力になる）は直列のままでよい
- `return_exceptions=True` を使い、1件の失敗が他に波及しないようにする

### エラーハンドリング
- 全エラーは `BotError` 継承
- サーキットブレーカーで保護（連続失敗 → 一時停止 → 自動復帰）
- 構造化ログに `trace_id` 付与

### キャラクター（ミミ）
- 一人称「僕」固定、フレンドリーな砕けた敬語
- 理論的・現実主義・ちょっとだけ毒舌
- 禁止: 過度な甘やかし・無駄な称賛・感情的な発言
- 詳細は `config.yaml` の `character.persona` に定義

## Security

GitHubパブリックリポジトリのため:
- `.env` / `config.yaml`（実体）/ `data/` / `*.key` / `*.pem` を `.gitignore` に含める
- APIキー・トークン・IPアドレスを**コードにハードコードしない**
- コンテナは非root（`botuser`）で実行
- Windows Agent認証: `X-Agent-Token` ヘッダー（トークンは `.env` 管理）
- WebGUI: Basic認証必須
- 外部公開時: Cloudflare Tunnel + Cloudflare Access

## Tech Stack
| 項目 | 採用 |
|------|------|
| 言語 | Python 3.11 |
| Discord | discord.py v2 |
| Web | FastAPI |
| DB（構造化） | SQLite（aiosqlite・WAL） |
| DB（記憶） | ChromaDB（PersistentClient） |
| スケジューラ | APScheduler |
| LLM | Ollama API（既定 `gemma4:e2b`）/ Google Generative AI SDK（Geminiフォールバック・既定OFF） |
| STT | kotoba-whisper-v2.0（Sub PC の Windows Agent 側で実行） |
| 画像生成 | ComfyUI（Windows Agent 側で実行） |
| RSS | feedparser |
| スケジュール | APScheduler + Heartbeat |

## Tooling
- Use **Serena MCP** tools for code navigation and editing to maximize efficiency (symbol search, overview, replace, insert, etc.)

## Knowledge Persistence
- 設計判断・知見は `docs/*.md` に積極的に残す
- 作業開始時に `docs/` の既存コンテキストを確認する

## Checklist
- [ ] arm64-compatible base image (`python:3.11-slim`)
- [ ] Non-root user (`botuser`)
- [ ] `TZ=Asia/Tokyo` in environment
- [ ] `restart: unless-stopped`
- [ ] `healthcheck:` in docker-compose.yml
- [ ] `env_file:` in docker-compose.yml
- [ ] Image: `ghcr.io/iniwa/secretary-bot:latest`
- [ ] GitHub Actions at `.github/workflows/docker-publish.yml`
- [ ] `.env.example` / `config.yaml.example` in project root
- [ ] `.claudeignore` in project root
- [ ] Graceful shutdown（SIGTERM → DB close → Bot close）
- [ ] `/health` endpoint on FastAPI
- [ ] Verify deployment via Portainer Stack
