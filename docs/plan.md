# Inner Mind 実装計画

## 概要

`docs/autonomous_design.md` に基づき、ミミの自律行動（Inner Mind）を実装する。
思考サイクルで内的モノローグを生成し、条件を満たした時のみDiscordに自発発言する。

**拡張性の核心:** コンテキストソースをプラグイン方式で管理し、
RSS・STT・天気など将来のインプットを `inner_mind.py` を変更せずに追加できる設計とする。

---

## 現状把握

| 項目 | 状態 |
|------|------|
| `src/inner_mind.py` | **未作成** |
| DB テーブル（monologue / self_model） | **未作成**（現在 schema version 8） |
| `heartbeat.py` | 存在。各ユニットの `on_heartbeat()` を呼び出す仕組みあり |
| `llm/router.py` | Ollama優先 + Geminiフォールバック。`purpose` ベースで制御 |
| `memory/` | ChromaDB（ai_memory, people_memory）動作済み |
| `web/app.py` | FastAPI。モノローグ関連エンドポイントなし |
| `config.yaml` | `inner_mind` セクションなし |

---

## コンテキストソース設計（拡張性の要）

### 設計思想

ミミの思考に影響を与える情報源は **すべて同じ仕組み（ContextSource）** で扱う。
既存機能（会話・メモ・リマインダー・天気）も、将来機能（RSS・STT）も区別しない。

InnerMind 本体が知るのは「ソースのリストがある」ということだけ。
何がどう集まるかはソース側の責務であり、InnerMind は関与しない。

### アーキテクチャ

```
[InnerMind.think()]
    │
    ├── _collect_context()
    │       │
    │       ├── 固定情報（InnerMind自身が取得）
    │       │   ├── datetime（現在時刻・曜日）
    │       │   ├── discord_status（オンライン状態）
    │       │   ├── last_monologue（前回の思考）
    │       │   └── self_model（自己モデル）
    │       │
    │       └── ContextSourceRegistry.collect_all()  ← 全ソース統一
    │               │
    │               │  ── 初期実装 ──
    │               ├── ConversationSource  → 直近の会話履歴
    │               ├── MemoSource          → 未処理メモ
    │               ├── ReminderSource      → 直近のリマインダー
    │               ├── MemorySource        → ai_memory / people_memory
    │               ├── WeatherSource       → 天気サブスク地域の天気情報
    │               │
    │               │  ── 将来追加 ──
    │               ├── RSSSource           → RSSフィード要約
    │               └── STTSource           → 音声認識ログ
    │
    ├── _think_phase()   ← 全コンテキストを統合してLLMへ
    └── _speak_phase()
```

> **固定情報**は InnerMind の動作に不可欠なもの（時刻・前回思考・自己モデル）のみ。
> それ以外は既存・将来問わずすべて ContextSource。

### ContextSource 基底クラス

```python
# src/inner_mind/context_sources/base.py

class ContextSource:
    """InnerMind に情報を供給するソースの基底クラス。"""

    name: str = ""          # プロンプトに埋め込む際のセクション名
    priority: int = 100     # 収集順序（小さいほど先 → プロンプト上部に配置）
    enabled: bool = True    # False にするとスキップ（WebGUI から個別制御可能）

    def __init__(self, bot):
        self.bot = bot

    async def collect(self, shared: dict) -> dict | None:
        """コンテキストを収集して返す。データなしなら None。
        
        shared: 他のソースと共有するコンテキスト（前回モノローグ・直近会話要約等）。
                MemorySource 等が「何について検索するか」の手がかりに使う。
        """
        raise NotImplementedError

    def format_for_prompt(self, data: dict) -> str:
        """収集データをLLMプロンプト用テキストに変換。"""
        raise NotImplementedError
```

**`shared` パラメータの意図:**

MemorySource は ChromaDB のベクトル検索に「検索クエリ」が必要。
固定文字列（`"最近の関心事"`）では毎回同じ記憶しか引けない。
`shared` に直近会話や前回モノローグの要約を入れることで、
今の文脈に関連する記憶を動的に引っ張れる。

```python
# MemorySource での活用例
async def collect(self, shared: dict) -> dict | None:
    # shared から検索のヒントを取得
    query = shared.get("last_monologue", "") or shared.get("recent_summary", "最近の出来事")
    ai = await self.bot.chroma.search("ai_memory", query=query, n_results=5)
    people = await self.bot.chroma.search("people_memory", query=query, n_results=5)
    ...
```

### ContextSourceRegistry

```python
# src/inner_mind/context_sources/registry.py

class ContextSourceRegistry:
    """コンテキストソースの登録・一括収集を管理。"""

    def __init__(self):
        self._sources: list[ContextSource] = []

    def register(self, source: ContextSource):
        self._sources.append(source)
        self._sources.sort(key=lambda s: s.priority)

    async def collect_all(self, shared: dict) -> list[dict]:
        """全ソースから収集。失敗/無効なソースはスキップ。

        shared: 固定情報（last_monologue等）を各ソースに渡す。
        """
        results = []
        for source in self._sources:
            if not source.enabled:
                continue
            try:
                data = await source.collect(shared)
                if data is not None:
                    results.append({
                        "name": source.name,
                        "data": data,
                        "text": source.format_for_prompt(data),
                    })
            except Exception:
                log.warning("ContextSource %s failed, skipping", source.name)
        return results
```

**shared の流れ:**
```
InnerMind._collect_context()
  │
  ├── 固定情報を取得（datetime, discord_status, last_monologue, self_model）
  │
  ├── shared = {"last_monologue": "...", "recent_summary": "...", "now": "..."}
  │
  └── registry.collect_all(shared)
        ├── ConversationSource.collect(shared)  → shared 不使用（DB直読み）
        ├── MemoSource.collect(shared)          → shared 不使用（DB直読み）
        ├── ReminderSource.collect(shared)      → shared 不使用（DB直読み）
        ├── MemorySource.collect(shared)        → shared["last_monologue"] を検索クエリに利用
        └── WeatherSource.collect(shared)       → shared 不使用（API/キャッシュ）
```

### 初期実装するソース（5つ）

| ソース | name | priority | データ元 | 役割 |
|--------|------|----------|----------|------|
| `ConversationSource` | "最近の会話" | 10 | `conversation_log` | 直近N件の会話履歴 |
| `MemoSource` | "メモ" | 20 | `memos` | 未処理のメモ一覧 |
| `ReminderSource` | "リマインダー" | 30 | `reminders` | 直近の予定・リマインダー |
| `MemorySource` | "記憶" | 40 | ChromaDB | ai_memory + people_memory |
| `WeatherSource` | "天気" | 50 | weather_subscriptions + API | 登録地域の天気情報 |

各ソースは既存の DB テーブル / Unit のデータを **読むだけ**。書き込みはしない。

### 各ソースの具体的な収集内容

**ConversationSource** — 直近の会話から「今何の話をしていたか」を把握
```python
async def collect(self):
    messages = await self.bot.db.get_recent_messages(limit=20)
    return {"messages": messages} if messages else None
```

**MemoSource** — ユーザーが残したメモ。思い出して話しかけるきっかけになる
```python
async def collect(self):
    memos = await self.bot.db.fetchall("SELECT * FROM memos ORDER BY created_at DESC LIMIT 10")
    return {"memos": memos} if memos else None
```

**ReminderSource** — 近い予定があれば「もうすぐ○○だよ」と声をかけられる
```python
async def collect(self):
    upcoming = await self.bot.db.fetchall(
        "SELECT * FROM reminders WHERE active=1 AND remind_at > ? ORDER BY remind_at LIMIT 5",
        (jst_now(),)
    )
    return {"reminders": upcoming} if upcoming else None
```

**MemorySource** — 過去の記憶。ユーザーの興味・習慣と現在のコンテキストを結びつける
```python
async def collect(self, shared):
    # shared から動的に検索クエリを生成（前回の思考や直近会話に関連する記憶を引く）
    query = shared.get("last_monologue") or shared.get("recent_summary") or "最近の出来事"
    ai = await self.bot.chroma.search("ai_memory", query=query, n_results=5)
    people = await self.bot.chroma.search("people_memory", query=query, n_results=5)
    return {"ai_memory": ai, "people_memory": people}
```

**WeatherSource** — 登録地域の天気。場所への関心から話題を広げるきっかけ
```python
async def collect(self):
    subs = await self.bot.db.fetchall("SELECT * FROM weather_subscriptions WHERE active=1")
    if not subs:
        return None
    # 各地域の天気情報を取得（キャッシュ利用）
    weather_data = []
    for sub in subs:
        weather = await self._get_cached_weather(sub["location"], sub["latitude"], sub["longitude"])
        if weather:
            weather_data.append({"location": sub["location"], **weather})
    return {"weather": weather_data} if weather_data else None
```

### 将来追加の例

```python
# src/inner_mind/context_sources/rss_source.py（将来）
class RSSSource(ContextSource):
    name = "RSSフィード"
    priority = 60

    async def collect(self, shared) -> dict | None:
        # shared["last_think_time"] で前回思考以降の新着のみ取得
        since = shared.get("last_think_time", jst_now())
        feeds = await self.bot.db.fetchall(
            "SELECT * FROM rss_entries WHERE published_at > ? ORDER BY published_at DESC LIMIT 10",
            (since,)
        )
        return {"entries": feeds} if feeds else None

    def format_for_prompt(self, data: dict) -> str:
        lines = [f"- {e['title']}（{e['source']}）" for e in data["entries"]]
        return "\n".join(lines)
```

```python
# src/inner_mind/context_sources/stt_source.py（将来）
class STTSource(ContextSource):
    name = "音声メモ"
    priority = 55

    async def collect(self, shared) -> dict | None:
        since = shared.get("last_think_time", jst_now())
        entries = await self.bot.db.fetchall(
            "SELECT * FROM stt_log WHERE created_at > ? ORDER BY created_at DESC LIMIT 5",
            (since,)
        )
        return {"entries": entries} if entries else None
```

> `last_think_time` は InnerMind が `shared` に含めて渡す（前回 monologue の `created_at`）。
> 各ソースが自前で管理する必要がないため、基底クラスにメソッドは不要。

### 自律行動シナリオ集

**シナリオ1: RSS × ユーザーの関心**
```
RSSSource:   「○○の新DLCが発表」
MemorySource: people_memory に「○○が気になる」
→ 思考: "あ、前に気になるって言ってたゲームの新情報出てる"
→ 発言: 「前気になるって言ってた○○、新DLC出るみたいだよ！」
```

**シナリオ2: 天気 × リマインダー**
```
WeatherSource:  「東京 明日は雨」
ReminderSource: 「明日 14:00 外出予定」
→ 思考: "明日雨なのに外出の予定入ってるな"
→ 発言: 「明日雨っぽいけど、14時の外出大丈夫？傘忘れないでね」
```

**シナリオ3: メモ × 時間経過**
```
MemoSource: 「3日前のメモ: APIの設計案を考える」
→ 思考: "3日前のメモ、その後進んでないっぽいな"
→ 発言: 「そういえばAPIの設計案、進んだ？」
```

**シナリオ4: STT × 記憶**
```
STTSource:   ユーザーの独り言「あー疲れた」（音声認識）
MemorySource: people_memory に「最近残業が続いている」
→ 思考: "また疲れたって言ってる。残業続きだもんな"
→ 発言: 「最近ずっと疲れてない？ちゃんと休んでる？」
```

**ポイント:** すべてのシナリオは「複数のソースを LLM が見て判断する」だけ。
InnerMind 本体のロジックは一切変わらない。ソースを増やす＝ミミの世界が広がる。

---

## ファイル構成

```
src/
├── inner_mind/
│   ├── __init__.py
│   ├── core.py                    # InnerMind クラス本体
│   ├── prompts.py                 # 思考・発言プロンプトテンプレート
│   └── context_sources/
│       ├── __init__.py
│       ├── base.py                # ContextSource 基底クラス
│       ├── registry.py            # ContextSourceRegistry
│       ├── conversation.py        # ConversationSource（直近会話）
│       ├── memo.py                # MemoSource（メモ）
│       ├── reminder.py            # ReminderSource（リマインダー）
│       ├── memory.py              # MemorySource（ai_memory / people_memory）
│       └── weather.py             # WeatherSource（天気サブスクリプション）
```

> 設計書では `src/inner_mind.py` 単一ファイルだったが、
> コンテキストソースの拡張性を考慮してパッケージ構成に変更。
> 各ソースは1ファイル1クラス。将来の RSS / STT も同じ場所にファイルを追加するだけ。

---

## 実装ステップ

### Step 1: DB スキーマ追加

**ファイル:** `src/database.py`

- スキーマバージョンを 8 → 9 へ
- 2テーブル追加:
  - `mimi_monologue` — 内的モノローグ履歴
  - `mimi_self_model` — 自己モデル（key-value）
- マイグレーション関数に v8→v9 を追加
- ヘルパーメソッド追加:
  - `save_monologue(monologue, mood, did_notify, notified_message)`
  - `get_monologues(limit)`
  - `upsert_self_model(key, value)`
  - `get_self_model()`
  - `get_last_monologue()`

```sql
CREATE TABLE IF NOT EXISTS mimi_monologue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    monologue        TEXT NOT NULL,
    mood             TEXT,
    did_notify       BOOLEAN DEFAULT 0,
    notified_message TEXT,
    created_at       DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS mimi_self_model (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at DATETIME NOT NULL
);
```

---

### Step 2: ContextSource 基盤 + 初期ソース作成

**ファイル:** `src/inner_mind/context_sources/`

1. `base.py` — `ContextSource` 基底クラス
2. `registry.py` — `ContextSourceRegistry`
3. `conversation.py` — `ConversationSource`（直近会話）
4. `memo.py` — `MemoSource`（メモ）
5. `reminder.py` — `ReminderSource`（リマインダー）
6. `memory.py` — `MemorySource`（ai_memory / people_memory）
7. `weather.py` — `WeatherSource`（天気サブスクリプション地域の天気）

---

### Step 3: InnerMind 本体作成

**ファイル:** `src/inner_mind/core.py`

**クラス:** `InnerMind`

```
InnerMind(bot)
  ├── __init__(bot)
  │   ├── ContextSourceRegistry 初期化
  │   └── 初期ソース5つを登録
  │       (Conversation, Memo, Reminder, Memory, Weather)
  │
  ├── think()                       # メインエントリ（heartbeatから呼ばれる）
  │   ├── _collect_context()        # 固定情報 + Registry.collect_all(shared_ctx)
  │   ├── _build_think_prompt()     # コンテキスト → プロンプト組み立て
  │   ├── _think_phase()            # LLM呼び出し① → monologue/mood/memory_update
  │   ├── _save_thought()           # DB保存（monologue + self_model + ai_memory）
  │   └── _speak_phase()            # 条件付き発言
  │       ├── _check_speak_conditions()
  │       ├── _generate_message()   # LLM呼び出し②
  │       └── _send_to_discord()    # Discord送信 + conversation_log 記録
  │
  ├── register_source(source)       # 外部からソース追加（Unit等から呼べる）
  └── _get_settings()               # DB settings 優先 → config フォールバック
```

**`_think_phase` の JSON パース戦略:**

ローカル LLM（qwen3, gemma4）は JSON 出力を壊すことがある。段階的にフォールバック:

```python
def _parse_think_response(self, raw: str) -> dict:
    """LLM応答から JSON を抽出する。段階的フォールバック。"""
    # 1. そのまま json.loads
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. ```json ... ``` ブロック抽出
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 最初の { から最後の } を抽出
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # 4. すべて失敗 → レスポンス全文をモノローグとして保存（思考ログは残す）
    log.warning("Failed to parse InnerMind JSON, saving raw response as monologue")
    return {"monologue": raw, "mood": "unknown", "memory_update": None}
```

**`_save_thought()` の詳細:**
- `mimi_monologue` テーブルに monologue / mood を保存
- `mood` → `mimi_self_model` の key='mood' を更新
- `memory_update` が非null → `ai_memory`（ChromaDB）に保存

**`_check_speak_conditions` の Discord ステータス取得:**

`discord.User` には `.status` がない。`Guild.get_member()` で `discord.Member` を取得する必要がある。
また **Presences Intent（特権インテント）** が必要。

```python
async def _get_user_status(self) -> str:
    """Discordユーザーのオンライン状態を取得。取得不能時は 'online' 扱い。"""
    user_id = int(self.bot.config.get("inner_mind", {}).get("target_user_id", 0))
    if not user_id:
        return "online"  # 未設定時はデフォルトで通過
    for guild in self.bot.guilds:
        member = guild.get_member(user_id)
        if member:
            return str(member.status)  # "online" | "idle" | "dnd" | "offline"
    return "online"  # 取得できない場合は通過（安全側）
```

> **前提:** `discord.Intents.presences` と `discord.Intents.members` が有効であること。
> bot.py の Intents 設定を確認し、必要なら追加する。

**`_generate_message` の重複発言防止:**

直近の自発発言（`did_notify=1`）を発言プロンプトに含めて、同じ話題の繰り返しを避ける。

```python
# 発言プロンプトに注入
recent_speaks = await self.bot.database.get_monologues(limit=5, did_notify_only=True)
if recent_speaks:
    prompt += "\n\n[最近の自発発言（同じ話題を繰り返さないこと）]\n"
    for s in recent_speaks:
        prompt += f"- {s['notified_message']}\n"
```

**`_send_to_discord()` の詳細:**
- Discord チャンネルに送信
- `mimi_monologue` の `did_notify` / `notified_message` を更新
- **`conversation_log` にも記録**（role='assistant', unit='inner_mind'）
  → 次回の ConversationSource で自発発言も会話文脈として拾えるようにする

**プロンプト組み立て（`_build_think_prompt`）:**

```
[状況]
現在時刻: 2025-04-07 14:32（月曜日）
ユーザーのDiscord状態: online

[最近の会話]          ← ConversationSource
ユーザー: 今日は○○の作業するよ
ミミ: がんばって！

[メモ]                ← MemoSource
- APIの設計案を考える（3日前）

[リマインダー]        ← ReminderSource
- 明日 14:00 外出

[記憶]                ← MemorySource
AI記憶: 先週ユーザーは疲れ気味だった
ユーザー記憶: ○○というゲームが気になっている

[天気]                ← WeatherSource
東京: 明日は雨、最高気温18℃

[RSSフィード]         ← (将来) RSSSource
- ○○の新DLCが発表

[前回の思考]
「特に何もないか。平和。」

[自己モデル]
mood: calm
interest: ユーザーの最近の疲労

[指示]
あなた（ミミ）は今この状況を見て何を考えていますか？...
```

各ソースの `format_for_prompt()` 出力がセクションとして自動挿入される。

---

### Step 4: heartbeat.py 統合

**ファイル:** `src/heartbeat.py`

- `InnerMind` インスタンスを `Heartbeat.__init__` で生成
- tick カウンターを追加（`self._think_tick`）
- `_tick()` 内で `thinking_interval_ticks` ごとに `inner_mind.think()` を呼び出し

```python
# _tick() 内に追加
self._think_tick += 1
im_cfg = self.bot.config.get("inner_mind", {})
if im_cfg.get("enabled", False):
    interval = im_cfg.get("thinking_interval_ticks", 2)
    if self._think_tick % interval == 0:
        if not self._think_running:
            self._think_running = True
            asyncio.create_task(self._run_think())

async def _run_think(self):
    """inner_mind.think() をバックグラウンドで実行。ハートビートをブロックしない。"""
    try:
        await self.inner_mind.think()
    except Exception as e:
        log.error("InnerMind think failed: %s", e)
    finally:
        self._think_running = False
```

> 現在のハートビートは15分間隔のため `2`（15分 × 2 = 30分）が設計書の30分間隔に合致。

**バックグラウンド実行の理由:**
inner_mind.think() は LLM を最大2回呼び出す（各タイムアウト300秒）。
直接 `await` するとハートビート全体（リマインダーチェック・コンパクション等）が
数分間ブロックされる。`asyncio.create_task` で非同期化し、`_think_running`
フラグで二重実行を防止する。

---

### Step 5: config.yaml 更新

**ファイル:** `config.yaml.example`

```yaml
inner_mind:
  enabled: true
  thinking_interval_ticks: 2      # ハートビート何回に1回思考するか（15分×2=30分）
  speak_probability: 0.20         # 発言確率（0.0〜1.0）
  min_speak_interval_minutes: 0   # 最低発言インターバル（分）。0=制限なし
  speak_channel_id: ""            # 自発発言を送るDiscordチャンネルID
  target_user_id: ""              # Discordステータスを監視するユーザーID
```

---

### Step 6: LLM Router 拡張

**ファイル:** `src/llm/router.py`

- `purpose="inner_mind"` を追加
- Inner Mind は **Ollama専用**（Geminiフォールバックなし）
- `_PURPOSE_TO_TOGGLE` には追加しない（= Gemini不可）

---

### Step 7: WebGUI エンドポイント追加

**ファイル:** `src/web/app.py`

```
GET  /api/monologue?limit=50       → モノローグ履歴
GET  /api/inner-mind/settings      → 現在の設定値
POST /api/inner-mind/settings      → 設定変更（enabled, speak_probability, min_speak_interval_minutes）
```

- 設定値は `settings` テーブル（key-value）に保存
- `inner_mind.py` 側で DB settings を優先読み込み → なければ config.yaml フォールバック

---

### Step 8: WebGUI フロントエンド

**ファイル:** `src/web/static/` 配下

- モノローグ閲覧ページ（タイムライン形式）
- 設定パネル（enabled, speak_probability, min_speak_interval_minutes）
- 表示例:
  ```
  14:32  [curious]  「そういえば昨日の話、気になるな...」  ✉ 送信済
  12:01  [calm]     「特に何もないか。平和。」
  09:15  [talkative] 「朝だし何か話しかけようかな」        ✉ 送信済
  ```

---

## 実装順序

```
Step 1 (DB)  →  Step 2 (ContextSource基盤)  →  Step 3 (InnerMind本体)
                                               →  Step 4 (heartbeat統合)
                                               →  Step 5 (config)
                                               →  Step 6 (LLM Router)
                                               →  Step 7 (API)
                                               →  Step 8 (フロントエンド)
```

Step 4〜6 は Step 3 完了後に並行可能。Step 7→8 は順序依存。

---

## 将来のソース追加手順（開発者向け）

新しいコンテキストソースを追加する場合:

1. `src/inner_mind/context_sources/xxx_source.py` を作成
2. `ContextSource` を継承し `collect()` と `format_for_prompt()` を実装
3. 登録方法を選択:
   - **a) InnerMind 初期化時に登録**（常に有効なソース）
     ```python
     # inner_mind/core.py の __init__ に追加
     self.registry.register(RSSSource(bot))
     ```
   - **b) Unit から動的に登録**（Unit有効時のみ）
     ```python
     # units/rss.py の setup 時
     bot.inner_mind.register_source(RSSSource(bot))
     ```

InnerMind 本体のコード変更は不要。

---

## 設計判断メモ

| 判断 | 理由 |
|------|------|
| 既存データも全て ContextSource 化 | メモ・リマインダー・天気を特別扱いしない。統一設計で将来のRSS・STTと同列に扱える |
| パッケージ構成（`inner_mind/`） | ソース追加でファイルが増えるため単一ファイルでは管理困難 |
| ソースの `format_for_prompt()` | 各ソースが自身のデータを最もよく知っている。プロンプト構築を分散 |
| `priority` による収集順序 | プロンプト内のセクション順序を制御（重要度順に並べられる） |
| 失敗ソースのスキップ | 1つのソースが壊れても思考サイクル全体を止めない |
| Unit からの動的登録 | Unit の enabled/disabled に連動してソースも自動管理 |
| ソースは「読むだけ」 | 各ソースは既存DBを参照するだけ。データの書き込み・更新は各Unitの責務 |
| Ollama専用（Gemini不可） | 設計書の方針。自律思考は内部処理でありAPI課金を避ける |
| `thinking_interval_ticks: 2` | 現行HB=15分 × 2 = 30分。設計書の30分間隔に合致 |
| `min_speak_interval: 0` | 初期は制限なしで動作確認。後からWebGUIで調整 |
| 設定は DB settings 優先 | WebGUIから動的変更可能にするため。config.yaml はデフォルト値 |
| JSON パース失敗はスキップ | 自律思考の失敗でBot全体を止めない |
| quiet_hours は実装しない | 設計書にあるが、Discordステータス（offline/dnd）で同等の制御が可能。重複する仕組みを避ける |
| ソース個別の enabled フラグ | ソースが増えた際に WebGUI から個別に ON/OFF 制御できる。DB settings で `source.{name}.enabled` を管理 |
| shared パラメータで動的検索 | MemorySource の検索クエリを固定文字列にしない。前回モノローグや直近会話を手がかりに関連記憶を引く |
| 自発発言を conversation_log に記録 | 次回の思考サイクルで自分の発言も文脈に入る。会話の連続性を保つ |
| think() はバックグラウンド実行 | LLM 2回呼び出しで数分かかる可能性がある。ハートビートのリマインダーチェック等をブロックしない |
| JSON パース 4段階フォールバック | ローカル LLM は JSON を壊しやすい。最悪でもレスポンス全文をモノローグとして保存し、思考ログを失わない |
| 発言プロンプトに直近自発発言を注入 | 同じ RSS エントリ等で繰り返し同じ話題を話さないよう LLM に文脈を与える |
| Discord ステータスは Guild.get_member() | discord.User には .status がない。Presences Intent が必要 |

---

## リスク・注意点

### 対処済み（設計に反映）

| リスク | 対処 |
|--------|------|
| **ハートビートブロック** | `asyncio.create_task` でバックグラウンド実行 + `_think_running` で二重実行防止（Step 4） |
| **JSON パース失敗** | 4段階フォールバック: 直接パース → コードブロック抽出 → 括弧抽出 → 全文をモノローグ保存（Step 3） |
| **Discord ステータス取得** | `Guild.get_member()` を使用。`Intents.presences` + `Intents.members` が必要。取得不能時は online 扱い（Step 3） |
| **自発発言の重複** | 直近の自発発言履歴を発言プロンプトに注入し「同じ話題を繰り返さない」と指示（Step 3） |

### 未対処（実装時に注意）

- **Ollama 同時リクエスト競合**: inner_mind の LLM 呼び出し中にユーザーのチャットが来ると、Ollama 側でキューイングされ後発がタイムアウトする可能性。問題が顕在化したら `asyncio.Lock` で排他制御を追加する
- **コンテキスト長**: ソースが増えるとプロンプトが肥大化。各ソースの `format_for_prompt()` で簡潔に要約する設計で対処。将来的にトークン上限管理が必要になる可能性
- **天気APIコスト**: WeatherSource は毎思考サイクルでAPI叩かないよう、キャッシュ戦略が必要（天気Unitの既存キャッシュを流用）
- **Intents 設定**: bot.py で `Intents.presences` と `Intents.members` が有効か確認が必要。Discord Developer Portal で特権インテントの許可も必要
