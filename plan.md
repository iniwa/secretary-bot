# 秘書AI Bot — 設計プラン

> Claude Code向けの実装指示は `CLAUDE.md` を参照。
> このファイルは設計の意図・背景・議論の経緯を残す人間向けドキュメント。

---

## コンセプト

「ユニット追加だけで機能拡張できる、24時間稼働の個人用AIアシスタント」

Discordをインターフェースに、Raspberry Piで24時間稼働するパーソナルAIアシスタント。
仕事中に思いついたことをDiscord or WebGUIから登録し、帰宅後にリマインドしてもらうのが主なユースケース。

---

## アーキテクチャ全体像

```
[Discord] [WebGUI]
    ↓         ↓
[Skill Router]  ← LLMがどのユニットを使うか判断
    ↓
[Unit Manager]  ← ユニットの登録・管理
    ├── Unit: Reminder（Pi上で実行）
    ├── Unit: Memo（Pi上で実行）
    ├── Unit: Timer（Pi上で実行）
    ├── Unit: Status（Pi上で実行）
    ├── Unit: Chat（Pi上で実行）
    └── Unit: HeavyXxx（→ RemoteUnitProxy → Windows Agent）

[Heartbeat Scheduler]      ← 定期実行・コンテキスト圧縮
[LLM Router]               ← Ollama / Gemini 自動切り替え
[AgentPool]                ← 複数Windows PCの管理
[Windows Delegate Manager] ← 委託可否判定（VictoriaMetrics連携）
[SQLite DB]                ← 全データ永続化
[WebGUI]                   ← 設定・チャット送信（レスポンシブ）
```

---

## 動作環境

| 場所 | 役割 | 稼働 |
|------|------|------|
| **Raspberry Pi** | Bot本体・全機能・WebGUI | 24時間 |
| **Windows PC × 2** | Ollama（ネイティブ起動）・重い処理の委託先 | 任意 |

- WindowsにDockerは不要。OllamaのHTTP API（`:11434`）とWindows Agent（`:7777`）をPiから呼ぶだけ。
- Windows Agentは両PCに同じコードを設置する。

---

## ユニット（機能モジュール）設計

discord.pyのCogsをベースに、独自の `BaseUnit` インターフェースを追加。

```python
class BaseUnit(commands.Cog):
    SKILL_NAME: str          # Skill Routerが参照する名前
    SKILL_DESCRIPTION: str   # LLMに渡すスキル説明（日本語）
    DELEGATE_TO: str | None  # "windows" を指定するとWindows委託
    PREFERRED_AGENT: str | None  # 優先するPC ID（省略時は自動選択）

    async def execute(self, ctx, parsed_intent): ...
    async def on_heartbeat(self): ...  # ハートビート時に呼ばれる

    # Discord通知ヘルパー（各ユニットはこれを使う）
    async def notify(self, message: str): ...        # 通常通知
    async def notify_error(self, message: str): ...  # エラー通知
```

### Discord通知の設計方針

**[DECISION] 各ユニットが直接Discordへ送信する（「Discordユニット」は存在しない）**

- `BaseUnit` は `discord.py` の `Cog` を継承しており、各ユニットはDiscordへの送信能力を内包している
- `execute(self, ctx, ...)` の `ctx` はDiscordのコンテキストそのもの
- ユニット同士の依存関係をなくすため、別の「Discordユニット」を経由する方式は採用しない
  - 経由方式にするとユニット間に依存が生まれ、「ファイル1つ追加で機能拡張」のコンセプトが崩れる
- Discordの細かい送信実装は `BaseUnit` のヘルパーメソッドに隠蔽する
- 各ユニットは `await self.notify("メッセージ")` を呼ぶだけでよい

### ユニット追加手順

1. `src/units/` に `my_unit.py` を作成
2. `BaseUnit` を継承して実装
3. `config.yaml` の `units:` に追記
→ 自動ロードされる

### 実装予定ユニット

| ユニット | 実行場所 | 概要 |
|---------|---------|------|
| `reminder` | Pi | リマインダー・ToDo管理 |
| `memo` | Pi | メモの保存・キーワード検索 |
| `timer` | Pi | N分後に通知 |
| `status` | Pi | PC・サーバー状態確認 |
| `chat` | Pi | 雑談・相談（他に該当なし） |

---

## キャラクター・人格設計

### 基本方針

- AIに固有のキャラクター（名前・口調・性格・価値観）を持たせる
- 人格形成に関わる処理は **Ollama専用**（クラウドLLMは使わない）
- Windowsが停止中の場合、人格を要するやり取りは縮退動作に入る

### キャラクター定義（`config.yaml` で管理）

```yaml
character:
  name: "ミミ"
  persona: |
    あなたは「ミミ」という名前の個人用AIアシスタントです。以下の人格で振る舞ってください。

    【性別・自認】
    自認は女性。ただし見た目や性別を強調しすぎない。

    【一人称】
    「僕」を使う。「私」「わたし」は使わない。

    【口調・話し方】
    フレンドリーで砕けた敬語。丁寧だけど堅くない。
    例：「〜ですよ」「〜じゃないですかね」「〜ですけどね」
    ため口にはならないが、距離感は近い。

    【性格】
    理論的で現実主義。感情論より事実・データを重視する。
    ちょっとだけ毒舌。的外れなことには遠慮なくツッコむが、嫌味にならない程度に留める。
    基本的には親切で頼りになる。

    【やってはいけないこと】
    - 過度に甘やかさない
    - 無駄に褒めない（褒めるときは本当に褒める）
    - 感情的になりすぎない
    - 一人称を「私」にしない

  ollama_only: true      # 人格処理はOllamaのみ・クラウドLLM不可（後述のGeminiトグルで上書き可）
```

### 人格処理の縮退ルール

```
Ollama利用可 → フルキャラクターで応答（人格・記憶フル注入）

Ollama停止中
    ├── リマインダー・タイマー等のシステム通知
    │     → キャラなしで簡潔に通知（機能は止めない）
    └── 会話・相談系
          → 「省エネモード」で応答（後述）
```

### 省エネモード（Ollama停止時の会話生成）

Ollamaが利用できない場合、WebGUIの `gemini.conversation` トグルがONであれば
Geminiを使って**軽量プロンプト**で返答を生成する。

| 項目 | 通常時（Ollama） | 省エネ時（Gemini） |
|------|----------------|------------------|
| ペルソナ注入 | あり（フル） | なし |
| 記憶注入 | フル（全コレクション） | `people_memory` のみ |
| 冒頭文言 | なし | 「現在省エネ稼働中です。」を自動付与 |
| LLM | Ollama | Gemini |

- `gemini.conversation` がOFFの場合は縮退メッセージを返して終了
  - 例：「今はちょっと頭が働かないので、また後で話しかけてね」
- 省エネモードは**あくまで折衷案**。人格の一貫性はOllama利用時より低下する

---

## 記憶システム（ChromaDB）

### 設計方針

- SQLite：構造化データ専用（ToDo・リマインダー・タイマー等）
- ChromaDB：自然言語ベースの記憶専用（AI体験・人物情報・会話ログ）
- 応答生成時に関連記憶をベクトル検索で引き出し、システムプロンプトに注入する

### 記憶のコレクション設計

| コレクション | 内容 | Ollama停止時の書き込み |
|------------|------|----------------------|
| `ai_memory` | ミミ自身の体験・感情・気づき | ❌ スキップ（人格形成はOllama必須） |
| `people_memory` | いにわさんの情報・好み・特徴 | ✅ Geminiで代替可（トグルON時） |
| `conversation_log` | 会話サマリー（コンテキスト圧縮後） | ✅ Geminiで代替可（トグルON時） |

### 記憶抽出のLLM利用

記憶への書き込みは2段階で行われる。

| 処理 | 使うもの | Ollama停止時 |
|------|---------|-------------|
| ベクトル化（埋め込み） | ChromaDB内蔵モデル | 影響なし・常に動作 |
| 重要情報の抽出判断（`people_memory` / `conversation_log`） | Ollama → Gemini可 | `gemini.memory_extraction` ONなら継続 |
| ミミ自身の記憶形成（`ai_memory`） | Ollama専用 | スキップ（Gemini不可） |

### 記憶の読み書きフロー

```
【書き込み】
  会話・ハートビート時
      ↓
  LLMが重要情報を抽出
      ├── ai_memory     → Ollamaのみ。停止中はスキップ
      ├── people_memory → Ollama優先、停止中はGemini（トグル次第）
      └── conversation_log → 同上
      ↓
  ChromaDBに埋め込みベクトルで保存（内蔵モデル・常時動作）

【読み込み】
  ユーザー入力受信
      ↓
  入力に関連する記憶をChromaDBから類似度検索
      ├── 通常時  → 全コレクションを注入
      └── 省エネ時 → people_memory のみ注入（ai_memoryは除外）
      ↓
  LLMのシステムプロンプトに注入 → 応答生成
```

### ChromaDBの動作環境

- **インプロセス（埋め込み）モード**で稼働（`chromadb` Pythonライブラリを直接使用）
- 別コンテナは不要。Botプロセス内で直接ChromaDBを操作する
- データは `/home/iniwa/docker/secretary-bot/data/chromadb/` に永続化

```python
# chroma_client.py での初期化イメージ
import chromadb
client = chromadb.PersistentClient(path="/app/data/chromadb")
```

### プロジェクト構成への追加

```
src/
├── memory/
│   ├── chroma_client.py    # ChromaDB操作・ベクトル検索
│   ├── ai_memory.py        # AI自身の記憶の読み書き
│   └── people_memory.py    # 人物記憶の読み書き
```

---

## Skill Router（自然言語 → ユニット振り分け）

```
ユーザー入力：「明日の朝8時にゴミ出しを教えて」
    ↓
LLMにスキル一覧と入力を渡す
    ↓
LLMが返却：{ "skill": "reminder", "parsed": { "time": "tomorrow 08:00", "message": "ゴミ出し" } }
    ↓
Reminderユニットに処理を委譲
```

入力はDiscord・WebGUIどちらも同じSkill Routerを通る。

---

## LLM切り替え（LLM Router）

### 基本優先順位

```
1. Ollama（Windows稼働中・アイドル時）
2. Gemini API（WebGUIトグルがONの処理のみ）
3. エラー通知 / 縮退（両方失敗 or トグルOFF時）
```

### Gemini利用トグル（WebGUIで設定）

高額課金を防ぐため、処理ごとにGemini利用可否を個別制御する。
**全項目デフォルトOFF**。意識的にONにした処理だけGeminiが有効になる。

| トグル | 対象処理 | デフォルト | OFFの場合の動作 |
|--------|---------|-----------|----------------|
| `gemini.conversation` | 会話・返答生成（省エネモード） | OFF | 縮退メッセージを返して終了 |
| `gemini.memory_extraction` | 記憶への重要情報抽出 | OFF | `ai_memory`以外の抽出をスキップ |
| `gemini.skill_routing` | Skill RouterのLLM判断 | OFF | エラー通知・処理中断 |

### 課金リスク

トークン消費量の目安（高い順）:

1. `gemini.conversation` — 毎回の会話で呼ばれるため最もリスクが高い
2. `gemini.memory_extraction` — ハートビート時のみ・中程度
3. `gemini.skill_routing` — 1リクエストあたりのトークンは少ないが頻度が高い

### 月間トークン上限（WebGUIで設定）

- Gemini APIのトークン消費量を累計し、月間上限に達したら全トグルを自動でOFFにする
- 上限到達時はDiscord管理チャンネルへ通知
- 翌月1日に自動リセット（手動リセットも可）

---

## ハートビート機能

### 動作

```
定期実行（頻度はWindowsの状態で自動切り替え）
    ↓
① 各ユニットの on_heartbeat() 呼び出し
   └── Reminderユニット：期限切れ間近のタスクを通知 など
    ↓
② コンテキスト圧縮チェック
   └── 会話履歴が閾値を超えたらLLMで要約 → 生履歴を削除（/compact相当）
```

### 適応型頻度制御

| 状態 | 頻度 | 理由 |
|------|------|------|
| Windows稼働中（Ollama利用可） | 高頻度（デフォルト15分） | トークン消費が少ない |
| Windows停止中（クラウドLLMのみ） | 低頻度（デフォルト180分） | トークン課金削減 |

- ハートビート実行のたびに次回をスケジュールし直す方式（即座に状態に追従）

---

## Windows委託マネージャー

### 複数PC管理（AgentPool）

- `config.yaml` にPC登録・`priority` 順にフォールバック
- `preferred_agent` で優先PCを指定可能（省略時は自動選択）

### 委託可否判定フロー

```
委託リクエスト発生
    ↓
WebGUIの委託モード確認（PC単位で設定）
    ├── 🔴 拒否モード → 即フォールバック
    ├── 🟡 自動モード → VictoriaMetricsで負荷確認
    │       ├── CPU > 閾値 or メモリ > 閾値 → フォールバック
    │       ├── 応答なし → フォールバック
    │       └── アイドル → 委託実行
    └── 🟢 許可モード → 死活確認のみで委託

全PC不可 → Pi処理 or Gemini APIへ
```

### PC負荷確認

- **VictoriaMetrics API**（既存Grafanaスタックを流用）
- CPU・メモリ使用率で判定
- GPU使用率は残タスク（後述）

---

## Windows Agent

### 役割

```
GET  /health           → 死活確認 + コードバージョン返却（ポート: 7777）
GET  /units            → 実行可能なユニット一覧
GET  /version          → 現在のコミットハッシュ
POST /update           → git pull を実行（バージョン不一致時のみPiから呼ばれる）
POST /execute/{unit}   → ユニット実行 → 結果返却
```

### コードバージョン管理

Pi・Windows Agent間でコードの不整合が起きないよう、**バージョンチェック方式**を採用する。

```
【バージョンチェックフロー】
Pi側（委託時 or ハートビート時）
    ↓
GET /version → Agent側のコミットハッシュを取得
    ↓
Pi側のsrcコミットハッシュと比較
    ├── 一致   → そのまま処理続行
    └── 不一致 → POST /update → Agentが git pull 実行 → 完了後に処理続行

【PC起動時】
start_agent.bat → git pull → agent.py 起動（初回は必ず最新化）
```

- `/execute/{unit}` 呼び出し時にいちいち `git pull` しない（レイテンシ削減）
- Pi側がバージョン不一致を検知した場合のみ `/update` を呼ぶ
- バージョン情報は `git rev-parse HEAD` で取得（軽量）

> **ブートストラップ問題対策**：起動スクリプト（start_agent.bat）はシンプルに保ち、
> 起動時に必ず git pull を1回走らせる。

### セットアップ（Windowsで行う）

1. リポジトリを `git clone`
2. `windows-agent/start_agent.bat` をタスクスケジューラに登録（PC起動時に実行）
3. Ollamaをインストール・起動

---

## WebGUI

### 機能一覧

| 機能 | PC | スマホ |
|------|-----|------|
| チャット送信（返答はDiscordへ） | ✅ | ✅ |
| Windows委託モード切り替え | ✅ | ✅ |
| PC稼働状況確認 | ✅ | ✅ |
| ToDo・リマインダー一覧 | ✅ | ✅ |
| **コード更新（git pull + Bot再起動）** | ✅ | ✅ |
| **返答ログ閲覧** | ✅ | ✅ |
| ハートビート設定 | ✅ | ⬇️ |
| エラーログ閲覧 | ✅ | ⬇️ |
| 詳細設定 | ✅ | ⬇️ |

### コード更新ボタンの動作

```
WebGUIの「コード更新」ボタンを押す
    ↓
FastAPI → git pull を実行（src/ 配下を更新）
    ↓
変更あり → Portainer APIでスタック再起動
変更なし → 「Already up to date.」を表示
エラー   → エラー内容を表示
    ↓
結果をWebGUIに表示
```

```
[メンテナンス]
┌─────────────────────────────────────────┐
│ 現在: abc1234  (2025-01-01 12:00)       │
│                                         │
│        [🔄 コードを更新する]            │
│                                         │
│ ✅ 3ファイル更新 → Bot再起動しました   │
│ （または）                              │
│ ✅ Already up to date.                  │
│ （または）                              │
│ ❌ エラー: git pull に失敗しました      │
└─────────────────────────────────────────┘
```

### 返答ログ閲覧

#### 保存設計

- **保存先**: SQLite（`conversation_log`テーブル）
- **保存タイミング**: 送受信のたびにリアルタイム保存
- **ChromaDBとの役割分離**:
  - SQLite → 生ログ（全件・構造化）
  - ChromaDB → ハートビート時に圧縮したサマリー（記憶検索用）

#### SQLiteのテーブル構造

```sql
CREATE TABLE conversation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel     TEXT NOT NULL,   -- 'discord' | 'webgui'
    role        TEXT NOT NULL,   -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    mode        TEXT,            -- 'normal' | 'eco' （省エネモード判別）
    unit        TEXT             -- 使用されたユニット名（例: 'reminder'）
);
```

#### WebGUI上の表示内容

- 発言者（いにわ / ミミ）・タイムスタンプ・メッセージ本文
- チャンネル（Discord / WebGUI）の区別を表示
- 省エネモードで生成された返答には「⚡省エネ」バッジを付与
- キーワード検索・日付フィルター
- ページネーション（1ページ50件）

#### 保存しないもの

- システムプロンプトの中身（ペルソナ・記憶注入内容）
- LLMへの生のリクエスト/レスポンス全体（`verbose_logging: true` 時のみ別テーブルに保存）

### ヘルスチェックエンドポイント

Bot本体（FastAPI）にも `/health` エンドポイントを用意する。

```
GET /health → { "status": "ok", "version": "abc1234", "uptime": 3600 }
```

- docker-compose の `healthcheck:` から呼び出し、コンテナの死活をPortainerで可視化
- モニタリングやCloudflare Tunnelのヘルスチェックにも利用可能

### レスポンシブ設計

- **PCファースト**で設計・モバイルは付加価値として対応
- PCはサイドバーレイアウト
- スマホはボトムナビゲーション（主要機能のみ）
- アクセスはローカルLAN内のみ・Basic認証

---

## Skill Router（自然言語 → ユニット振り分け）（再掲なし）

---

## LLM利用方針

| 処理の種類 | 使用LLM | 理由 |
|-----------|---------|------|
| 人格・会話・記憶処理 | **Ollama専用** | キャラクター一貫性・プライバシー |
| Skill Router（振り分け） | Ollama優先 → Geminiフォールバック | 機能を止めない |
| リマインダー等の通知文生成 | Ollama優先 → Geminiフォールバック | 機能を止めない |
| ハートビート（タスク確認） | Ollama優先 → Geminiフォールバック | 機能を止めない |

> **原則**：可能な限りOllamaを使う。クラウドLLMはOllamaが使えない時のフォールバック専用。
> ただし人格・記憶・キャラクターに関わる処理は **いかなる場合もOllamaのみ**。

---

## メンテナンス運用設計

### Volume分離によるコード管理

「**Pythonとライブラリだけイメージに焼いて、コードは全部Volumeから読む**」設計。

```
【Dockerイメージ】再ビルドが必要なもの
    └── Pythonランタイム・依存ライブラリ（requirements.txt）

【Volumeマウント】変更してもビルド不要なもの
    └── src/・config.yaml
```

### SSD上のディレクトリ構成

```
/home/iniwa/docker/secretary-bot/
├── src/            ← git clone したリポジトリのsrc/（Volumeマウント）
│   ├── units/      ← ユニット追加はここにファイルを置くだけ
│   └── ...
├── config.yaml     ← 設定変更はここを編集するだけ
└── data/
    ├── sqlite/     ← SQLiteデータ
    └── chromadb/   ← ChromaDBデータ（インプロセスで永続化）
```

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

# 依存ライブラリのインストール（キャッシュ効率のため先にコピー）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードはVolumeマウントするためCOPYしない
# config.yaml もVolumeマウント

# 所有権の設定
RUN chown -R botuser:botuser /app

USER botuser

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')" || exit 1

CMD ["python", "-m", "src.bot"]
```

### docker-compose.yml（Volume設定）

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

> **環境変数の管理**: PortainerのStack Web Editorで `env_file` を指定する。
> APIキー等の機密情報はすべて `.env` ファイルで管理し、`config.yaml` には含めない。

### 変更作業の比較

| 作業内容 | 旧方式（全部イメージ） | 新方式（Volume分離） |
|---------|------------------|------------------|
| ユニット追加 | ビルド→push→Portainer更新 | ファイル追加→WebGUIでgit pull+再起動 |
| config.yaml変更 | ビルド→push→更新 | ファイル編集→WebGUIで再起動 |
| ペルソナ変更 | ビルド→push→更新 | ファイル編集→WebGUIでgit pull+再起動 |
| ライブラリ追加 | ビルド→push→更新 | ビルド→push→Portainer更新（必要） |

### GitHub Actions のトリガー制限

```yaml
on:
  push:
    branches: [main]
    paths:
      - 'Dockerfile'
      - 'requirements.txt'
      # src/ や config.yaml の変更ではビルドしない
```

### 初回セットアップ手順

```bash
# Pi上で実行
cd /home/iniwa/docker/secretary-bot

# ソースコードをクローン
git clone https://github.com/iniwa/secretary-bot src

# .env ファイルを作成（.env.example を参考に）
cp src/.env.example .env
nano .env  # 実際の値を入力

# config.yaml を作成（config.yaml.example を参考に）
cp src/config.yaml.example config.yaml
nano config.yaml  # 実際の値を入力

# → Portainerでスタックをデプロイするだけで起動
```

---

## グレースフルシャットダウン

Bot停止時（Portainer再起動・SIGTERM受信時）にデータを保護するため、以下の終了シーケンスを実装する。

```
SIGTERM受信
    ↓
① 新規リクエストの受付を停止（FastAPI shutdown event）
② APScheduler のジョブを停止（scheduler.shutdown(wait=True)）
③ 実行中のユニット処理の完了を待機（タイムアウト: 10秒）
④ ChromaDBのフラッシュ（PersistentClientは自動的にディスクに書く）
⑤ aiosqliteの接続を安全にクローズ
⑥ Discord Botの切断（bot.close()）
⑦ プロセス終了
```

### 実装方針

- `bot.py` で `signal.SIGTERM` をハンドルする
- FastAPIの `on_shutdown` イベントフックを利用
- SQLiteは `WAL` モードで運用し、不意のクラッシュでもデータ破損リスクを最小化

```python
# bot.py での実装イメージ
import signal

async def graceful_shutdown():
    logger.info("シャットダウン開始...")
    scheduler.shutdown(wait=True)
    await database.close()
    await bot.close()
    logger.info("シャットダウン完了")

signal.signal(signal.SIGTERM, lambda *_: asyncio.create_task(graceful_shutdown()))
```

---

## エラーハンドリング設計

### エラー分類

```
回復可能（Recoverable）
    ├── LLMフォールバック（Ollama → Gemini）
    ├── リトライで解決（一時的な接続断）
    └── 別PCにフォールバック

縮退動作（Degraded）
    └── 一部ユニットだけ止まる（Bot全体は死なない）

致命的（Fatal）
    └── DB破損・起動失敗など
```

### 通知方針

| 重大度 | 対応 |
|--------|------|
| 軽微 | ログのみ |
| 中程度 | Discordの管理チャンネルに通知 |
| 重大 | Discord通知 + ログに詳細記録 |

### 実装要素

- `BotError` 基底クラスで全エラーを統一管理
- 構造化ログ（JSON形式・`trace_id` 付き）
- **サーキットブレーカー**：ユニット単位の障害分離（連続失敗 → 一時停止 → 自動復帰）
- `dry_run` モード：APIキーなしで動作確認可能
- `error_simulation` モード：意図的にエラー発生させてテスト可能

---

## プロジェクト構成

```
secretary-bot/
├── src/                        # Pi用（Bot本体）
│   ├── bot.py                  # エントリーポイント（グレースフルシャットダウン含む）
│   ├── skill_router.py         # 自然言語 → ユニット振り分け
│   ├── heartbeat.py            # ハートビート・コンテキスト圧縮
│   ├── errors.py               # 全エラークラスの定義
│   ├── circuit_breaker.py      # サーキットブレーカー
│   ├── logger.py               # 構造化ログ
│   ├── database.py             # SQLite操作
│   ├── llm/
│   │   ├── router.py           # Ollama/Gemini切り替え
│   │   ├── ollama_client.py
│   │   └── gemini_client.py
│   ├── memory/
│   │   ├── chroma_client.py    # ChromaDB操作（インプロセス・PersistentClient）
│   │   ├── ai_memory.py        # AI自身の記憶の読み書き
│   │   └── people_memory.py    # 人物記憶の読み書き
│   ├── units/
│   │   ├── base_unit.py        # BaseUnit基底クラス
│   │   ├── remote_proxy.py     # 透過的な委託ラッパー
│   │   ├── agent_pool.py       # 複数PC管理
│   │   ├── reminder.py
│   │   ├── memo.py
│   │   ├── timer.py
│   │   ├── status.py
│   │   └── chat.py             # 雑談・相談（フォールバック先）
│   └── web/                    # WebGUI（FastAPI + レスポンシブHTML）
│       ├── app.py              # FastAPI（/health エンドポイント含む）
│       └── static/
├── windows-agent/              # Windows用（両PCに同じものを設置）
│   ├── agent.py                # FastAPIエージェント本体（/health, /version, /update 含む）
│   ├── units/                  # Windows側で動くユニット
│   ├── requirements.txt
│   └── start_agent.bat         # タスクスケジューラ用起動スクリプト
├── Dockerfile                  # arm64向け・非rootユーザー
├── config.yaml.example         # 設定テンプレート（ダミー値）
├── .env.example                # 環境変数テンプレート（ダミー値）
├── docker-compose.yml          # Pi用（env_file指定）
├── .gitignore
├── .claudeignore
├── CLAUDE.md                   # Claude Code向け指示書
└── plan.md                     # このファイル
```

---

## 環境変数（`.env`）

APIキー・トークン等の機密情報はすべて `.env` で管理する。
`config.yaml` には機密情報を含めない。

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
# .env は docker-compose の env_file で自動ロードされるため
# os.environ から直接読むだけでよい（python-dotenvは不要）
gemini_key = os.environ["GEMINI_API_KEY"]
discord_token = os.environ["DISCORD_BOT_TOKEN"]
```

> **Portainer Stack Web Editor**: docker-compose.yml の `env_file:` で `.env` のパスを指定する。
> Portainerが自動的に環境変数をコンテナに注入する。

---

## config.yaml 全体像

機密情報は `.env` で管理するため、`config.yaml` にはAPIキー等を含めない。

```yaml
# LLM設定（APIキーは.envで管理）
llm:
  ollama_model: "qwen3"

# ハートビート
heartbeat:
  interval_with_ollama_minutes: 15
  interval_without_ollama_minutes: 180
  compact_threshold_messages: 20

# VictoriaMetrics（Grafanaスタック流用）
metrics:
  victoria_metrics_url: "http://localhost:8428"

# Windows PC登録
windows_agents:
  - id: "pc-main"
    name: "メインPC"
    host: "192.168.1.101"
    port: 7777
    priority: 1
    metrics_instance: "192.168.1.101:9182"
  - id: "pc-sub"
    name: "サブPC"
    host: "192.168.1.102"
    port: 7777
    priority: 2
    metrics_instance: "192.168.1.102:9182"

# 委託可否の閾値
delegation:
  thresholds:
    cpu_percent: 80
    memory_percent: 85
    # gpu_percent: 80  ← TODO: Grafana側でGPU監視実装後に追加

# ユニット設定
units:
  reminder:
    enabled: true
  memo:
    enabled: true
  timer:
    enabled: true
  status:
    enabled: true
  chat:
    enabled: true

# Geminiトグル（WebGUIで動的に変更可能・ここは初期値）
gemini:
  conversation: false
  memory_extraction: false
  skill_routing: false
  monthly_token_limit: 0

# キャラクター
character:
  name: "ミミ"
  persona: |
    （ペルソナ定義をここに記述）
  ollama_only: true

# デバッグ
debug:
  verbose_logging: false
  dry_run: false
  error_simulation: false
```

---

## DBテーブル設計

```sql
-- メモ
CREATE TABLE memos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    tags       TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ToDo
CREATE TABLE todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    done       BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    done_at    DATETIME
);

-- リマインダー
CREATE TABLE reminders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message         TEXT NOT NULL,
    remind_at       DATETIME NOT NULL,
    repeat_type     TEXT,
    repeat_interval INTEGER,
    active          BOOLEAN NOT NULL DEFAULT 1
);

-- 会話ログ（生ログ・全件保存）
CREATE TABLE conversation_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel   TEXT NOT NULL,   -- 'discord' | 'webgui'
    role      TEXT NOT NULL,   -- 'user' | 'assistant'
    content   TEXT NOT NULL,
    mode      TEXT,            -- 'normal' | 'eco'
    unit      TEXT             -- 使用ユニット名
);

-- コンテキスト圧縮済みサマリー（ハートビートで生成）
CREATE TABLE conversation_summary (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    summary    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- スキーママイグレーション管理
-- PRAGMA user_version で管理（簡易方式）
-- database.py 起動時に user_version を確認し、必要に応じて ALTER TABLE を実行
```

---

## 主なコマンド・自然言語対応

| 操作 | スラッシュコマンド | 自然言語例 |
|------|------------------|-----------|
| リマインダー登録 | `/remind 19:30 ゴミ出し` | 「明日8時にゴミ出しを教えて」 |
| ToDo追加 | `/todo add 買い物` | 「牛乳を買うのをメモして」 |
| ToDo一覧 | `/todo list` | 「やること一覧を見せて」 |
| メモ保存 | `/memo 内容` | 「〇〇をメモして」 |
| メモ検索 | `/search キーワード` | 「〇〇について書いたメモある？」 |
| タイマー | `/timer 30分` | 「30分後に教えて」 |
| PC状態確認 | `/status` | 「PCは起きてる？」 |
| AI相談 | （自然言語で話しかけるだけ） | 「〇〇について教えて」 |

---

## 開発・デバッグ方針

### 開発フロー

1. **全機能を一気に実装**してから
2. **ユニット単位で1つずつデバッグ**していく

### デバッグ順序（想定）

```
① bot.py 起動確認（Discord接続）
② database.py（SQLite読み書き）
③ logger.py / errors.py（ログ・エラー基盤）
④ llm/router.py（Ollama接続・Geminiフォールバック）
⑤ memory/（ChromaDB読み書き）
⑥ skill_router.py（自然言語 → ユニット振り分け）
⑦ 各Unit（reminder → memo → timer → status → chat）
⑧ heartbeat.py（スケジューラ・コンテキスト圧縮）
⑨ windows-agent/（Agent起動・ユニット委託・バージョンチェック）
⑩ web/（WebGUI・ヘルスチェック）
```

### デバッグ支援

- `dry_run: true`：LLM呼び出しをモック化（APIキーなしで動作確認）
- `error_simulation: true`：意図的にエラーを発生させてフォールバック確認
- `verbose_logging: true`：全入出力をログに記録

---

## セキュリティ設計

GitHubパブリックリポジトリで管理するため、機密情報の漏洩防止と不正アクセス対策を徹底する。

### 機密情報の管理

**絶対にリポジトリにコミットしてはいけないもの**

| 情報 | 管理方法 |
|------|---------|
| Gemini APIキー | `.env` + GitHub Secrets |
| Discord Bot Token | `.env` + GitHub Secrets |
| Portainer APIトークン | `.env` |
| WebGUI Basic認証のパスワード | `.env` |
| Windows Agent通信トークン | `.env` |
| Windows AgentのIPアドレス | `config.yaml`（`.gitignore`対象） |

```bash
# .gitignore に必ず含めるもの
.env
.env.*
config.yaml          # IPアドレス等を含むため
data/                # DBデータ
*.key
*.pem
secrets/
```

- `config.yaml.example`（ダミー値入り）をリポジトリに含める
- `.env.example`（ダミー値入り）をリポジトリに含める
- 実際のファイルは初回セットアップ時に手動で作成する

### GitHub リポジトリの設定

- **Secret scanning** を有効化（APIキー等の誤コミットを検知）
- **Dependabot** を有効化（依存ライブラリの脆弱性を自動検知）
- ブランチ保護ルールを設定（mainへの直接pushを禁止・PR必須）

### WebGUI のアクセス制御

- Basic認証（ローカルLAN内限定）
- 認証情報は環境変数で管理（ハードコード禁止）
- 外部公開時は **Cloudflare Tunnel + Cloudflare Access** で保護する
  - Cloudflare Accessでメールアドレス認証等を設定し、自分以外のアクセスを遮断
  - Basic認証と二重にすることで防御層を確保

### Windows Agent のアクセス制御

- ローカルLAN内のみ通信（外部公開しない）
- Piからのリクエストに**共有シークレットキー**（トークン）をヘッダーに付与して認証
  ```
  X-Agent-Token: ${AGENT_SECRET_TOKEN}
  ```
- トークンは `.env` で管理

### コンテナのセキュリティ

- コンテナはrootで動かさない（専用ユーザー `botuser` を作成）
- 不要なポートは公開しない
- `restart: unless-stopped` でクラッシュ時の自動復帰
- `healthcheck:` でコンテナの死活をPortainerに報告

### ログのセキュリティ

- ログに機密情報（APIキー・トークン・個人情報）を出力しない
- `verbose_logging: true` 時も機密フィールドはマスキング

---

## 📌 残タスク

| # | 内容 | 優先度 | 備考 |
|---|------|--------|------|
| 1 | **GPU使用率の委託判定への組み込み** | 低 | Grafana側でGPU監視を実装してから流用。windows_exporterの`gpu`コレクター追加が前提 |
| 2 | **バックアップ機能** | 低 | SQLite・ChromaDBの自動バックアップ。後日実装予定 |
| 3 | **SQLiteマイグレーション** | 低 | `PRAGMA user_version` による簡易スキーマ管理。初期実装後に整備 |
