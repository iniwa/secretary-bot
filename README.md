# ai-mimich-agent

Raspberry Pi 4 で24時間稼働する、個人用AIアシスタント Bot。

Discord または WebGUI からメッセージを送ると、AIが意図を読み取って適切な処理を実行する。

## ユースケース

- 仕事中に思いついたことを **Discordに送るだけで登録**
- **帰宅後にリマインド**してもらう（自然言語でスヌーズ・完了も可能）
- 後で「あのメモどこだっけ」→ **自然言語で検索**
- PCが起動中なら**重い処理はWindowsに委託**（Ollamaなど）
- 設定や状態の確認を**チャットで完結**させる
- ゲーム配信中は**自動で処理を抑制**、録画ファイルは**ゲーム名フォルダに自動整理**
- **RSSニュースを毎朝ダイジェスト**で通知
- AIが**自律的に思考**し、気まぐれに話しかけてくる

## 機能一覧

| 機能 | 説明 |
|------|------|
| **リマインダー** | 時刻指定でDiscordに通知。自然言語での完了・スヌーズ対応。エスカレーション再通知 |
| **メモ** | テキストを保存し、後からキーワードで検索 |
| **タイマー** | 「N分後に教えて」で時間経過後に通知 |
| **ステータス確認** | Raspberry PiやWindows PCの状態を即答 |
| **雑談・相談** | 上記に該当しない自由な会話 |
| **天気予報** | Open-Meteo APIで天気取得、定期通知・傘リマインド |
| **Web検索** | SearXNG経由のネット検索、ページ本文取得 |
| **楽天市場検索** | 商品検索・詳細取得 |
| **Googleカレンダー** | 予定の確認・追加・削除（`src/gcal/` で Calendar API と同期） |
| **PC電源管理** | シャットダウン・再起動・WoL起動 |
| **RSSフィーダー** | 定期巡回・LLM要約・カテゴリ別ダイジェスト通知 |
| **画像生成** | Windows Agent 経由で ComfyUI にジョブ投入。Wildcard / Dynamic Prompts 展開対応、ギャラリー・イベント pub/sub を WebGUI から操作 |
| **PromptCrafter** | LLM 会話で SDXL 向け positive/negative プロンプトを育成。セッションは SQLite に永続化し `image_gen` から参照 |
| **ModelSync** | 画像生成 Agent の `/capability` をポーリングし、未キャッシュなら `/cache/sync` でモデルを先読み |
| **ZZZ Codex** | HoYoLAB 連携。ゼンレスゾーンゼロのキャラクター/ディスク情報を収集・WebGUI で閲覧（`src/tools/zzz_disc/`） |
| **Docker Log Monitor** | コンテナログを定期巡回し error/warning を DB 保存。WebGUI で閲覧・除外パターン管理、Discord 通知はトグル制御 |
| **Habit Detector** | デイリー習慣ゲームの未プレイや長期ゲームからの離脱を検出し、InnerMind の発言材料にする |
| **アクティビティ検出** | ゲーム・OBS配信/録画・Discord VCの複合判定で処理抑制 |
| **OBSファイル整理** | 録画・リプレイ・スクショをゲーム名フォルダに自動整理 + PNG圧縮 |
| **STT（音声テキスト化）** | マイクキャプチャ → kotoba-whisper → LLM要約 → InnerMind統合 |
| **InnerMind（自律思考）** | 定期的にAIが自律思考。会話・メモ・RSS・カレンダー・ニュース等を材料に独り言・発言 |
| **Input Relay** | キーボード/ゲームパッド入力をOBSオーバーレイに可視化 |
| **WebGUI** | ブラウザから操作・設定変更・ログ閲覧・コード更新 |

## アーキテクチャ

```
[Discord] [WebGUI]
    |         |
[Unit Router]         <- LLMがどのユニットを使うか判断（JSON返却）
    |
[Unit Manager]        <- ユニットを自動ロード・管理
    +-- Pi上のUnit    -> そのまま実行
    +-- DELEGATE_TO="windows" -> RemoteUnitProxy -> AgentPool -> Windows Agent

[InnerMind]           <- 自律思考（ContextSource プラグインで情報収集）
[Heartbeat]           <- 適応型頻度制御（Ollama有無で間隔切り替え）
[ActivityDetector]    <- ゲーム/OBS/VC状態でLLM処理の実行可否を判定
[LLM Router]          <- Ollama優先 -> Gemini APIフォールバック
[AgentPool]           <- 複数Windows PCをpriority順に管理
[SQLite]              <- 全データ永続化
[ChromaDB]            <- インプロセス（PersistentClient）・ベクトル記憶
[WebGUI]              <- FastAPI + レスポンシブHTML（PCファースト）
```

### 設計の特徴

- **機能 = ユニット** : ファイル1つ追加するだけで機能が増える
- **LLMが振り分け** : どのユニットを使うかはAIが自動判断（Unit Router）
- **2段構えのLLM** : Ollama（ローカル・高品質） → Gemini（クラウド・省エネ）
- **記憶あり** : ベクトルDB（ChromaDB）で過去の会話・人物情報を記憶・検索
- **自律思考** : InnerMindが定期的に複数の情報源から文脈を収集し、AIが自発的に思考・発言
- **アクティビティ連動** : ゲーム・配信中は重い処理を自動抑制
- **コード更新もGUIから** : WebGUIの「コード更新」ボタン1つで git pull + 自動再起動

## 動作環境

| 場所 | 役割 | 稼働 |
|------|------|------|
| **Raspberry Pi 4** | Bot本体・全機能・WebGUI（Docker） | 24時間 |
| **Main PC** | Ollama・ゲーム検出・STTマイクキャプチャ・Input Relay送信 | 任意 |
| **Sub PC** | Ollama・OBS監視/ファイル整理・kotoba-whisper推論・Input Relay受信 | 任意 |

Windows に Docker は不要。Ollama の HTTP API（`:11434`）と Windows Agent（`:7777`）を Pi から呼ぶだけ。

## 技術スタック

| 項目 | 採用 |
|------|------|
| 言語 | Python 3.11 |
| Discord | discord.py v2 |
| Web | FastAPI |
| DB（構造化） | SQLite（aiosqlite・WAL） |
| DB（記憶） | ChromaDB（PersistentClient） |
| スケジューラ | APScheduler |
| LLM | Ollama API / Google Generative AI SDK |
| STT | kotoba-whisper-v2.0（transformers + CUDA） |
| RSS | feedparser |
| インフラ | Docker（arm64）・Portainer |

## プロジェクト構成

```
secretary-bot/
+-- src/
|   +-- bot.py                # エントリーポイント
|   +-- unit_router.py        # 自然言語 -> ユニット振り分け
|   +-- heartbeat.py          # ハートビート・コンテキスト圧縮
|   +-- database/             # SQLite（aiosqlite・WAL）
|   |   +-- _base.py          # スキーマ / マイグレーション本体
|   |   +-- conversation.py / generation.py / clip_pipeline.py
|   |   +-- lora.py / monologue.py / pending.py / section.py
|   |   +-- settings.py / wildcard.py
|   +-- errors.py             # BotError基底クラス
|   +-- circuit_breaker.py    # サーキットブレーカー
|   +-- logger.py             # 構造化ログ（JSON・trace_id）
|   +-- fetch_utils.py        # HTTP取得ユーティリティ
|   +-- flow_tracker.py       # フロー追跡
|   +-- status_collector.py   # Pi / Windows の状態収集
|   +-- activity/
|   |   +-- detector.py       # アクティビティ統合判定
|   |   +-- collector.py      # active_pcs / セッション永続化
|   |   +-- agent_monitor.py  # Windows Agent /activity 通信
|   |   +-- discord_monitor.py # Discord VC 検出
|   |   +-- habit_detector.py # 習慣ゲームのプレイ傾向・離脱検出
|   |   +-- daily_diary.py    # 1日の activity ダイジェスト生成
|   +-- gcal/
|   |   +-- service.py        # Google Calendar API サービスファクトリ
|   |   +-- sync.py           # ローカル DB とカレンダーの同期
|   +-- inner_mind/
|   |   +-- core.py           # InnerMind 自律思考エンジン
|   |   +-- prompts.py        # プロンプトテンプレート
|   |   +-- actuator.py       # decision の Tier 判定・承認待ち振り分け
|   |   +-- approval_view.py  # Discord 承認UI（pending_actions）
|   |   +-- discord_activity.py # Discord状態でthink()モード切替
|   |   +-- context_sources/  # プラグイン式情報収集（12種）
|   |       +-- conversation.py / memo.py / reminder.py
|   |       +-- memory.py / weather.py / rss.py / stt.py
|   |       +-- activity.py / calendar.py / github.py
|   |       +-- habit.py / tavily_news.py
|   +-- llm/
|   |   +-- router.py         # Ollama/Gemini切り替え
|   |   +-- ollama_client.py  # 複数インスタンス対応（least-connections）
|   |   +-- gemini_client.py / unit_llm.py
|   |   +-- gpu_monitor.py    # VictoriaMetrics 経由の GPU 占有検出
|   +-- memory/
|   |   +-- chroma_client.py  # ChromaDB（PersistentClient）
|   |   +-- ai_memory.py      # AI自身の記憶（Ollama専用）
|   |   +-- people_memory.py  # 人物記憶（Geminiフォールバック可）
|   |   +-- interest_extractor.py / sweeper.py
|   +-- rss/
|   |   +-- fetcher.py        # feedparserでRSS巡回
|   |   +-- processor.py      # LLM要約・フィルタリング
|   |   +-- recommender.py    # スコアリング・ランキング
|   |   +-- notify.py         # ダイジェスト通知
|   +-- stt/
|   |   +-- collector.py      # Main PC transcript 収集
|   |   +-- processor.py      # LLM要約 + ChromaDB保存
|   +-- units/
|   |   +-- base_unit.py      # BaseUnit（Cog継承）
|   |   +-- remote_proxy.py   # 透過的な委託ラッパー
|   |   +-- agent_pool.py     # 複数PC管理
|   |   +-- reminder.py / memo.py / timer.py / status.py / chat.py
|   |   +-- weather.py / web_search.py / rakuten_search.py
|   |   +-- calendar.py / power.py / rss.py
|   |   +-- docker_log_monitor.py # コンテナログ監視・エラー保存
|   |   +-- prompt_crafter.py # LLM対話でSDXLプロンプトを育成
|   |   +-- model_sync.py     # 画像生成Agentのモデルキャッシュ同期
|   |   +-- image_gen/        # 画像生成ユニット（ComfyUI連携）
|   |   |   +-- unit.py           # ジョブ受付・状態参照・pub/sub
|   |   |   +-- dispatcher.py     # Agent 振り分け・キュー制御
|   |   |   +-- agent_client.py   # Windows Agent HTTPクライアント
|   |   |   +-- workflow_mgr.py   # ComfyUI ワークフロー管理
|   |   |   +-- wildcard_expander.py # Wildcard / Dynamic Prompts
|   |   |   +-- section_composer.py / section_mgr.py
|   |   |   +-- warmup.py / modality.py / models.py
|   |   |   +-- presets/ / section_presets/
|   |   +-- clip_pipeline/    # 配信アーカイブ自動切り抜き（Whisper + Ollama）
|   |   |   +-- unit.py / dispatcher.py / agent_client.py / models.py
|   |   +-- lora_train/       # kohya_ss LoRA 学習
|   |       +-- unit.py / agent_client.py / toml_builder.py / nas_io.py
|   +-- tools/
|   |   +-- zzz_disc/         # ゼンレスゾーンゼロ Codex（HoYoLAB連携）
|   |   |   +-- routes.py / hoyolab_client.py / hoyolab_auth.py
|   |   |   +-- capture_client.py / extractor.py / normalizer.py
|   |   |   +-- job_queue.py / models.py / schema.py
|   |   |   +-- master_data/ / static/
|   |   +-- image_gen_console/ # 画像生成コンソール（WebGUI 同居ツール）
|   +-- web/
|       +-- app.py            # FastAPI（WebGUI + /health）
|       +-- routes/           # API ルート群（system/config/inner_mind
|       |                     #   /image_gen/rss/stt/memory/units/activity
|       |                     #   /docker_monitor/flow/obs/input_relay
|       |                     #   /clip_pipeline/lora_train/core）
|       +-- static/           # index.html / js / css / service-worker.js
+-- windows-agent/
|   +-- agent.py              # FastAPI（:7777）
|   +-- start_agent.bat
|   +-- activity/
|   |   +-- game_detector.py  # ゲームプロセス検出
|   |   +-- obs_manager.py    # OBS監視 + ファイル自動整理
|   +-- stt/
|   |   +-- mic_capture.py    # マイクキャプチャ（VAD付き）
|   |   +-- stt_client.py     # Sub PCへのバッチ送信
|   |   +-- whisper_engine.py # kotoba-whisper ラッパー
|   +-- tools/
|   |   +-- tool_manager.py   # サブプロセス管理（version/update/start/stop）
|   |   +-- input-relay/      # git submodule（OBSオーバーレイ）
|   |   +-- image_gen/        # ComfyUI ラッパー（Agent 側）
|   |   +-- clip_pipeline/    # Whisper + Ollama ハイライト判定（Agent 側）
|   |   +-- zzz_disc/         # ZZZ Codex 画面キャプチャ・抽出（Agent 側）
|   +-- config/
|       +-- agent_config.yaml.example
|       +-- game_processes.json
|       +-- game_groups.json
+-- Dockerfile
+-- config.yaml.example
+-- .env.example
+-- docker-compose.yml
```

## セットアップ

### Raspberry Pi（Bot本体）

```bash
cd /home/iniwa/docker/secretary-bot

# ソースコードをクローン
git clone https://github.com/iniwa/secretary-bot src

# 設定ファイルを作成
cp src/.env.example .env
cp src/config.yaml.example config.yaml
# .env と config.yaml を実際の値に編集

# Portainer でスタックをデプロイ
```

Docker イメージには Python ランタイムとライブラリのみを含め、ソースコードは Volume マウントで読み込む。
コード変更時は WebGUI の「コード更新」ボタン（git pull + Portainer API 再起動）で反映。

### Windows Agent

1. リポジトリを `git clone --recurse-submodules`
2. `pip install -r windows-agent/requirements.txt`
3. `windows-agent/config/agent_config.yaml.example` を `agent_config.yaml` にコピーして編集
4. `windows-agent/start_agent.bat` をタスクスケジューラに登録（PC起動時に実行）
5. Ollama をインストール・起動

## キャラクター（ミミ）

Bot には「ミミ」という名前のAIキャラクターが設定されている。

- 一人称「僕」固定、フレンドリーな砕けた敬語
- 理論的・現実主義・ちょっとだけ毒舌
- 人格に関わる処理は Ollama 専用（クラウド LLM では行わない）
- 定期的に自律思考し、気まぐれにDiscordで話しかけてくる
- 詳細は `config.yaml` の `character.persona` で定義

## セキュリティ

- `.env` で全機密情報を管理（`config.yaml` には含めない）
- コンテナは非root（`botuser`）で実行
- Windows Agent 認証: `X-Agent-Token` ヘッダー
- WebGUI: Basic 認証必須
- 外部公開時: Cloudflare Tunnel + Cloudflare Access

## ライセンス

Private
