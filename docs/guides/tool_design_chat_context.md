# ツール設計 Web チャット用コンテキスト

> **目的**: secretary-bot に「新しいツール / ユニット」を追加するときに、外部の Web チャット
> （ChatGPT / Claude.ai / 自分の WebGUI チャット等）に貼り付けて**設計案を対話で組む**ための
> 自己完結コンテキスト。コードそのものではなく、判断基準と制約・既存例をひとまとめにした
> 「設計の前提」を 1 ファイルにまとめている。
>
> 使い方の 1 例（詳細は末尾の「設計セッションの進め方」を参照）:
>
> 1. このファイルを丸ごとチャットにペースト
> 2. 自分がやりたいツールを 1〜3 行で書く
> 3. チャットに「Step 1: 分類」→「Step 2: 設計案」→「Step 3: 実装計画」の順で出してもらう
> 4. 出てきた設計案を `docs/<tool>/design.md` に保存 → 実装 → テスト

---

## 1. プロジェクトの前提

### 1.1 ざっくり一行

Raspberry Pi 4 上で 24 時間稼働する Discord/WebGUI の個人 AI アシスタント（キャラ名「ミミ」）。
Pi が頭脳、Windows PC が手足（重い処理の委託先）。

### 1.2 動作環境

| 場所 | 役割 | 稼働時間 |
|------|------|----------|
| Raspberry Pi 4（8GB / arm64 / Docker） | Bot 本体・全機能・WebGUI | 24h |
| Main PC（Ryzen 7 9800X3D / RTX 4080） | Ollama・ゲーム検出・STT マイク・Input Relay 送信 | 任意 |
| Sub PC（Ryzen 9 5950X / RTX 5060 Ti） | Ollama・OBS 監視 / 整理・kotoba-whisper・Input Relay 受信・ComfyUI | 任意 |

Windows には Docker は不要。Pi から Ollama `:11434` と Windows Agent `:7777` を叩く構造。

### 1.3 主な技術スタック

- Python 3.11 / discord.py v2 / FastAPI / APScheduler
- SQLite（aiosqlite・WAL）/ ChromaDB（PersistentClient）
- LLM: Ollama（既定 `gemma4:e2b`）+ Gemini（フォールバック、既定 OFF）
- STT: kotoba-whisper-v2.0（Sub PC GPU）
- 画像生成: ComfyUI（Windows Agent 配下）
- LoRA 学習: kohya_ss（Windows Agent 配下）
- 切り抜き: Whisper + Ollama ハイライト判定 + ffmpeg（Windows Agent 配下）

### 1.4 アーキテクチャ全体像

```
[Discord] [WebGUI]
    ↓         ↓
[Unit Router]         ← LLMがどのユニットを使うか判断（JSON）
    ↓
[Unit Manager]        ← ユニットを自動ロード・管理
    ├── Pi 上の Unit     → そのまま実行
    └── DELEGATE_TO="windows" → RemoteUnitProxy → AgentPool → Windows Agent

[InnerMind]           ← 自律思考（12種の ContextSource から文脈収集）
[Heartbeat]           ← 適応型頻度制御
[ActivityDetector]    ← ゲーム/OBS/VC で処理抑制判定
[LLM Router]          ← Ollama（least-connections）→ Gemini フォールバック
[AgentPool]           ← 複数 Windows PC を priority 順に管理
[SQLite]              ← 全データ永続化
[ChromaDB]            ← ベクトル記憶（人物・自己）
[WebGUI]              ← FastAPI + レスポンシブ HTML
```

---

## 2. ツールの分類（設計する前に 1 つに絞る）

新規機能を追加するとき、**まずどの枠に入るか**を確定させると設計が速い。

### A. Unit（`src/units/<name>.py`）
- 定義: Discord / WebGUI から **LLM のルーティングで選ばれる**機能。
- 条件: ユーザーが自然言語で指示できる。`UNIT_NAME` / `UNIT_DESCRIPTION` で LLM が判断。
- 例: `reminder`, `memo`, `timer`, `weather`, `rss`, `power`, `calendar`, `web_search`,
  `rakuten_search`, `docker_log_monitor`, `chat`, `status`。
- 追加手順:
  1. `src/units/<name>.py` を作り `BaseUnit` 継承 → `execute(ctx, parsed)` 実装 →
     `async def setup(bot)` を末尾に置く。
  2. `src/units/__init__.py::_UNIT_MODULES` に登録。
  3. `config.yaml` の `units.<name>.enabled: true`。
  4. 必要なら SQLite にテーブル追加（`src/database/_base.py` に migration）+ `src/database/<name>.py`
     にアクセサ。
  5. WebGUI に専用ページが必要なら `src/web/routes/<name>.py` + `src/web/static/js/pages/<name>.js`。
- 規約: `docs/guides/unit-creation-guide.md` 必読。

### B. サブモジュール / ヘルパー（`src/<area>/`）
- 定義: ユーザーの直接指示ではなく、Heartbeat や他ユニットから叩かれる内部機能。
- 例: `rss/`（fetch/processor/recommender）、`stt/`（collector/processor）、
  `activity/`（detector/collector/habit/daily_diary）、`gcal/`、`memory/`（ai/people/sweeper）、
  `inner_mind/context_sources/`（12 種のプラグイン）。
- 追加手順: ディレクトリ新設 → `src/bot.py` か heartbeat / unit から呼び出し。
- ポイント: Unit と違って LLM ルーティングに露出しない。

### C. 同居ツール（`src/tools/<name>/`）
- 定義: **WebGUI と同じ FastAPI に相乗り**するが疎結合な独立ツール。SPA やドメイン特化 UI を
  持つものが多い。Bot 本体と違うライフサイクルで動かせる。
- 例: `zzz_disc/`（ZZZ Codex・HoYoLAB 連携）、`image_gen_console/`（画像生成コンソール SPA）。
- 追加手順:
  1. `src/tools/<name>/__init__.py` に `register(app, bot)` を用意
     （ルート `/tools/<name>/*` と `/api/<name>/*` を app に登録）。
  2. `src/web/app.py` の `create_web_app()` で `register(app, bot)` を呼ぶ。
  3. `config.yaml` の `tools.<name>.enabled` で制御（推奨）。
- ポイント: Unit Router から呼ばれないので `UNIT_NAME` 不要。LLM は使ってもよいが、
  LLM ルーティング経路には乗らない。

### D. Windows Agent ツール（`windows-agent/tools/<name>/`）
- 定義: Windows PC 側のネイティブ処理（GPU・Windows API・OBS・ComfyUI 等）を
  Windows Agent（`:7777`）経由で呼べるようにする。
- 例: `input-relay/`（git submodule）、`image_gen/`、`clip_pipeline/`、`zzz_disc/`。
- 追加手順:
  1. `windows-agent/tools/<name>/` に実装。
  2. `windows-agent/agent.py` に `/api/<name>/*` エンドポイントを追加。
  3. Pi 側から叩くには `src/units/<name>/agent_client.py` のような薄い HTTP クライアント
     を用意し、ユニット（`DELEGATE_TO="windows"`）か同居ツールから呼ぶ。
  4. サブプロセス管理が要るなら `windows-agent/tools/tool_manager.py` を使う
     （`get_version / update / start / stop`）。
- ポイント: 認証ヘッダ `X-Agent-Token`（`.env` の `AGENT_SECRET_TOKEN`）必須。

### E. InnerMind ContextSource（`src/inner_mind/context_sources/<name>.py`）
- 定義: 自律思考が参照する「情報源」。Heartbeat で収集され、salience フィルタを通って
  LLM プロンプトに注入される。
- 例: `conversation / memo / reminder / memory / weather / rss / stt / activity /
  habit / calendar / github / tavily_news`。
- 追加手順:
  1. `Source` 基底クラス（`context_sources/base.py`）を継承して `collect()` / `salience()`
     を実装。
  2. `src/inner_mind/core.py::InnerMind.__init__` で `self.registry.register(...)` に追加。
- ポイント: LLM コストが乗るので、salience が低いソースは `top_n` で落とされる前提。

---

## 3. 設計時に必ず判断すること

以下の質問に **先に答える** と設計がブレない。

### 3.1 機能性
- ユーザーは何を言えば / クリックすればこのツールを使う？（Discord？ WebGUI ボタン？ 自動実行？）
- 1 回の操作で完結する？ 承認待ち（InnerMind Actuator）が必要？
- 応答はテキストだけ？ 画像ファイル？ ストリーム（SSE）？

### 3.2 実行場所
- Pi だけで完結する（軽い）？ → Unit か同居ツール
- GPU / Windows API / ファイルアクセスが必要 → Windows Agent ツール + 薄い Pi 側クライアント
- 両方必要なら、Pi 側にユニット → `DELEGATE_TO="windows"` で透過的に委託

### 3.3 永続化
- 保存するデータは？ → SQLite（構造化）/ ChromaDB（ベクトル）/ ファイル（NAS）
- SQLite を使うなら新テーブル? `_SCHEMA_VERSION` を増やして migration を追加する必要あり。
- NAS を使うなら `config.yaml` の `units.<unit>.nas.*` に base_path を持たせる。

### 3.4 LLM
- LLM 使う？ purpose は何？（`conversation` / `unit_routing` / `inner_mind` / `stt_summary` /
  `rss_summary` / `memory_extraction` / それ以外）
- Ollama 必須（人格・記憶系）？ それとも Gemini フォールバック許可？
- 独立した LLM 呼び出しを複数繰り返すなら `asyncio.gather()` で並列化（原則）。

### 3.5 抑制ルール
- ゲーム配信中でも動く必要がある？
  → `activity.block_rules.obs_streaming` で抑制されると動かない、ことを前提に設計する。
- Heartbeat の頻度は? Ollama 有無で変わる（既定 15 分 / 180 分）。

### 3.6 セキュリティ
- 外部 API キーが必要 → `.env` で管理、`config.yaml` には書かない。
- WebGUI で公開する API → Basic 認証経由（長期 SSE は例外）。
- Windows Agent 呼び出し → `X-Agent-Token` 必須。

### 3.7 UX
- WebGUI 側の UI はどのページに置く？ 既存ページに追加？ 新規 SPA にする？
- レスポンシブ対応（PC ファースト、スマホ許容）。
- 静的ファイルは `app.py` が md5 ベースで `?v=` を自動付与する。自分で手書きで `?v=xxx`
  を入れない。

---

## 4. 命名・レイアウト規約

- ユニット: スネークケース。`UNIT_NAME = "web_search"` のように単語複数は `_` で区切る。
- テーブル: スネークケース。プレフィックスで機能群を示す（`lora_`, `rss_`, `clip_pipeline_`,
  `image_`, `generation_` …）。
- WebGUI ルート: `/api/<domain>/<action>`（例: `/api/clip-pipeline/jobs/{id}/cancel`）。
  ケバブケースでもスネークでも OK だが、**ドメイン名はユニット名と揃える**。
- JS ファイル: `src/web/static/js/pages/<name>.js` + 必要なら `lib/<util>.js`。
- docs: `docs/<unit_or_area>/{README.md,design.md,api.md,…}`。
- 一時資産: `archives/` に退避、`.tmp/` は使い捨て。

---

## 5. 既存ユニット・ツールのかんたんな事例

設計の「こんな感じ」を掴むために、**似ているもの** を 1 つピックして模倣するのが最短。

| やりたい事のタイプ | 参考 | 参考ポイント |
|---|---|---|
| SQLite に CRUD、WebGUI で一覧 | `memo` / `reminder` | BaseUnit + execute + 意図分岐 + routes/units.py |
| 外部 API を叩いて返答 | `weather` / `rakuten_search` / `web_search` | fetch_utils 使用・タイムアウト |
| Discord 通知だけする定期処理 | `rss` / `reminder` の通知系 | Heartbeat + BaseUnit.notify() |
| Windows Agent に重い処理を委託 | `image_gen` / `lora_train` / `clip_pipeline` | `DELEGATE_TO="windows"` + dispatcher + agent_client |
| WebGUI に独立 SPA を持たせる | `src/tools/image_gen_console` | register(app, bot) + 静的 SPA |
| 画面キャプチャ → 構造化 → DB | `src/tools/zzz_disc` | capture_client + extractor + normalizer + job_queue |
| InnerMind の文脈を増やす | `context_sources/github.py` / `tavily_news.py` | Source 基底 + salience |

---

## 6. 設計案テンプレート（Web チャットに投げる）

新しいツールを設計するとき、Web チャットには以下の順で依頼すると結果がまとまりやすい。

### 6.1 「分類」ステップ（Step 1）

```
このプロジェクトに追加したい:
<1〜3 行でやりたいことを書く>

先のコンテキストを踏まえて、まず以下を確定してください（断定で）:
1. 分類: A Unit / B サブモジュール / C 同居ツール / D Windows Agent ツール / E ContextSource のどれか（複合可）
2. 実行場所: Pi / Windows Agent / 両方
3. 永続化: SQLite（テーブル名案）/ ChromaDB / ファイル / なし
4. LLM 利用: purpose 名・Ollama/Gemini のどちら許可
5. UI: Discord / WebGUI 既存ページ / 新規 SPA / なし
6. 抑制ルールで動かない前提があるか
これ以外の提案はまだ不要。
```

### 6.2 「設計案」ステップ（Step 2）

```
Step 1 の分類に従って、最小構成の設計案を出してください:
- ファイルツリー（追加・変更するファイルだけ）
- 各ファイルの責務（2〜3 行）
- 主要クラス / 関数名（シグネチャレベル）
- 新規 SQLite テーブルのスキーマ（必要なら）
- WebGUI エンドポイント一覧（GET/POST/... とパス、200字以内）
- config.yaml の追記例
- .env に追加が要るか
- 既存コード（プロジェクト規約）と重複・衝突がないか要チェック項目
過剰設計は NG。未定部分は「要決定」と明示してください。
```

### 6.3 「実装計画」ステップ（Step 3）

```
Step 2 を実装するタスク分解をお願いします:
- 独立して動く最小単位に分ける（1 タスク = 1 PR が理想）
- 依存関係を矢印で
- 各タスクの見積もり（S / M / L）
- テストの方針（ユニットテスト / 手動 / E2E）
- ドキュメント化場所（docs/<area>/design.md など）
```

### 6.4 注意点（チャットに伝えるべき制約）

常に以下を追記しておくと事故が少ない:
- 「追加の依存パッケージは最小限に。Python 3.11 / arm64 でビルド可能なもの。」
- 「Docker イメージは python:3.11-slim + Python ライブラリのみ。コードはイメージに含めない。」
- 「.env / config.yaml の実体 / data/ / *.key / *.pem は .gitignore 済み。秘匿情報をコードに書かない。」
- 「キャラクター『ミミ』は一人称『僕』固定、砕けた敬語。過度な甘やかしや称賛は NG。」
- 「pre-commit フックはスキップしない（`--no-verify` 禁止）。」

---

## 7. 実装チェックリスト（設計後に使う）

実装フェーズに入る前に、以下がすべて埋まっていれば設計 OK。

- [ ] 分類（A〜E）が 1 つに決まっている、または複合の構造が明確
- [ ] 追加・変更ファイルリストが出ている
- [ ] SQLite を使うなら migration ステップ（old→new version）も書いてある
- [ ] LLM を使うなら purpose と ollama_only / Gemini 許可が決まっている
- [ ] 非同期化で並列にできる呼び出しが `asyncio.gather()` 化されている
- [ ] WebGUI に露出するなら Basic 認証の要否・SSE の要否が決まっている
- [ ] Windows Agent を使うなら `X-Agent-Token` 認証経路が明示されている
- [ ] `.env.example` と `config.yaml.example` への追記項目がリストアップされている
- [ ] 失敗時のサーキットブレーカー（`BaseUnit.breaker`）の使い方が決まっている
- [ ] docs/ に残す設計資料の置き場所が決まっている

---

## 8. 設計セッションの進め方（運用フロー）

1. このファイルをコピー → Web チャットに貼る（ChatGPT / Claude / 自 WebGUI チャットなど）
2. 「§6.1 分類」を投げる → 分類が確定するまで対話
3. 「§6.2 設計案」を投げる → 設計が固まるまで対話（修正は差分で依頼）
4. 「§6.3 実装計画」を投げる → タスク分解を受け取る
5. 受け取った設計を `docs/<area>/design.md` に保存（必要なら `README.md` / `api.md` / `todo.md`
   も並べる。既存の `docs/image_gen/` / `docs/auto_kirinuki/` が参考になる）
6. 実装 → テスト → `docs/CHANGELOG.md` に「何を入れたか」を追記
7. ミミ自身の WebGUI チャット（`/api/chat`）に貼っても良いが、長文はトークン超過しがちなので
   外部 LLM 推奨

---

## 付録 A. 参照しておくと良い既存ドキュメント

- `CLAUDE.md` — Claude Code 向けプロジェクト規約（アーキ概要・ルール）
- `README.md` — ユーザー向けプロジェクト説明
- `docs/guides/unit-creation-guide.md` — BaseUnit 継承実装の完全マニュアル
- `docs/llm-routing.md` — Ollama / Gemini ルーティング仕様
- `docs/webgui-api-reference.md` — 現状の WebGUI API カテゴリ一覧
- `docs/webgui-db-config-reference.md` — DB テーブル一覧・config.yaml セクション
- `docs/webgui-units-reference.md` — 既存ユニットの役割早見表
- `docs/image_gen/design.md` — 大規模ユニット設計事例（委託＋WebGUI＋DB＋ NAS の全部入り）
- `docs/auto_kirinuki/design.md` — ジョブ型ユニット＋ Windows Agent 連携事例
- `docs/CHANGELOG.md` — 主要変更履歴
