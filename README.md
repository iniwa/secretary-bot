# ai-mimich-agent

Raspberry Pi 4 で24時間稼働する、個人用AIアシスタント Bot。

Discord または WebGUI からメッセージを送ると、AIが意図を読み取って適切な処理を実行する。

## ユースケース

- 仕事中に思いついたことを **Discordに送るだけで登録**
- **帰宅後にリマインド**してもらう
- 後で「あのメモどこだっけ」→ **自然言語で検索**
- PCが起動中なら**重い処理はWindowsに委託**（Ollamaなど）
- 設定や状態の確認を**チャットで完結**させる

## 機能一覧

| 機能 | 説明 |
|------|------|
| **リマインダー** | 時刻指定でDiscordに通知。ToDoの管理も |
| **メモ** | テキストを保存し、後からキーワードで検索 |
| **タイマー** | 「N分後に教えて」で時間経過後に通知 |
| **ステータス確認** | Raspberry PiやWindows PCの状態を即答 |
| **雑談・相談** | 上記に該当しない自由な会話 |
| **WebGUI** | ブラウザから操作・設定変更・ログ閲覧・コード更新 |

## アーキテクチャ

```
[Discord] [WebGUI]
    |         |
[Skill Router]        <- LLMがどのユニットを使うか判断（JSON返却）
    |
[Unit Manager]        <- ユニットを自動ロード・管理
    +-- Pi上のUnit    -> そのまま実行
    +-- DELEGATE_TO="windows" -> RemoteUnitProxy -> AgentPool -> Windows Agent

[Heartbeat]           <- 適応型頻度制御（Ollama有無で間隔切り替え）
[LLM Router]          <- Ollama優先 -> Gemini APIフォールバック
[AgentPool]           <- 複数Windows PCをpriority順に管理
[SQLite]              <- 全データ永続化
[ChromaDB]            <- インプロセス（PersistentClient）・ベクトル記憶
[WebGUI]              <- FastAPI + レスポンシブHTML（PCファースト）
```

### 設計の特徴

- **機能 = ユニット** : ファイル1つ追加するだけで機能が増える
- **LLMが振り分け** : どのユニットを使うかはAIが自動判断（Skill Router）
- **2段構えのLLM** : Ollama（ローカル・高品質） → Gemini（クラウド・省エネ）
- **記憶あり** : ベクトルDB（ChromaDB）で過去の会話・人物情報を記憶・検索
- **コード更新もGUIから** : WebGUIの「コード更新」ボタン1つで git pull + 自動再起動

## 動作環境

| 場所 | 役割 | 稼働 |
|------|------|------|
| **Raspberry Pi 4** | Bot本体・全機能・WebGUI（Docker） | 24時間 |
| **Windows PC x 2** | Ollama（ネイティブ）・重い処理の委託先 | 任意 |

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
| LLM | Ollama API（qwen3）/ Google Generative AI SDK |
| インフラ | Docker（arm64）・Portainer |

## プロジェクト構成

```
secretary-bot/
+-- src/
|   +-- bot.py                # エントリーポイント
|   +-- skill_router.py       # 自然言語 -> ユニット振り分け
|   +-- heartbeat.py          # ハートビート・コンテキスト圧縮
|   +-- errors.py             # BotError基底クラス
|   +-- circuit_breaker.py    # サーキットブレーカー
|   +-- logger.py             # 構造化ログ（JSON・trace_id）
|   +-- database.py           # SQLite（aiosqlite・WAL）
|   +-- llm/
|   |   +-- router.py         # Ollama/Gemini切り替え
|   |   +-- ollama_client.py
|   |   +-- gemini_client.py
|   +-- memory/
|   |   +-- chroma_client.py  # ChromaDB（PersistentClient）
|   |   +-- ai_memory.py      # AI自身の記憶（Ollama専用）
|   |   +-- people_memory.py  # 人物記憶（Geminiフォールバック可）
|   +-- units/
|   |   +-- base_unit.py      # BaseUnit（Cog継承）
|   |   +-- remote_proxy.py   # 透過的な委託ラッパー
|   |   +-- agent_pool.py     # 複数PC管理
|   |   +-- reminder.py / memo.py / timer.py / status.py / chat.py
|   +-- web/
|       +-- app.py            # FastAPI（WebGUI + /health）
|       +-- static/
+-- windows-agent/
|   +-- agent.py              # FastAPI（:7777）
|   +-- units/
|   +-- start_agent.bat
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

1. リポジトリを `git clone`
2. `windows-agent/start_agent.bat` をタスクスケジューラに登録（PC起動時に実行）
3. Ollama をインストール・起動

## キャラクター（ミミ）

Bot には「ミミ」という名前のAIキャラクターが設定されている。

- 一人称「僕」固定、フレンドリーな砕けた敬語
- 理論的・現実主義・ちょっとだけ毒舌
- 人格に関わる処理は Ollama 専用（クラウド LLM では行わない）
- 詳細は `config.yaml` の `character.persona` で定義

## セキュリティ

- `.env` で全機密情報を管理（`config.yaml` には含めない）
- コンテナは非root（`botuser`）で実行
- Windows Agent 認証: `X-Agent-Token` ヘッダー
- WebGUI: Basic 認証必須
- 外部公開時: Cloudflare Tunnel + Cloudflare Access

## ライセンス

Private
