# ミミ自律行動設計書

## 設計方針

ミミは「プログラムへの応答」ではなく「自分で考えて行動する」存在として振る舞う。
その核となるのが **思考サイクル（Inner Mind）** である。

### 基本原則

- 思考は **Ollama 専用**（Ollama 未起動時はスキップ、Gemini代替なし）
- 自発発言は **Discordオンライン状態に依存**（深夜・オフライン時は話しかけない）
- 思考ログ（内的モノローグ）は **WebGUI で閲覧可能**
- 「何もしない」が正常。たまに何かするから生きてる感が出る

---

## アーキテクチャ概要

```
[Heartbeat（既存）]
    │
    ├─ 既存処理（リマインダーチェック等）
    │
    └─ [Inner Mind] ← 一定間隔で起動
           │
           ├─【思考フェーズ】毎回必ず実行
           │    ├─ 1. コンテキスト収集
           │    ├─ 2. Ollama に思考を問う（LLM呼び出し①）
           │    └─ 3. モノローグ・自己モデル・記憶を DB 保存
           │
           └─【発言フェーズ】条件を満たした時のみ実行
                ├─ 4. 発言判定（インターバル × オンライン状態 × 確率）
                ├─ 5. Ollama に「何か言いたいことある？」を問う（LLM呼び出し②）
                └─ 6. message あり → Discord に送信
```

---

## Inner Mind 設計

### ファイル

> **注:** 実装計画（`docs/plan.md`）にて、拡張性のためパッケージ構成に変更済み。
> コンテキストソースのプラグイン設計により、RSS・STT等を無改修で追加可能。
> 最新のファイル構成は `docs/plan.md` を参照。

```
src/
└── inner_mind/          # パッケージ（plan.md で設計変更）
    ├── core.py          # InnerMind 本体
    ├── prompts.py       # プロンプトテンプレート
    └── context_sources/ # コンテキストソース（プラグイン方式）
```

### 思考サイクルの頻度

| 設定 | 初期値 | 備考 |
|------|----|------|
| 思考サイクル間隔 | 30分 | ハートビートN回に1回で制御 |
| 最低発言インターバル | **0分（制限なし）** | WebGUI で変更可能。DB で最終発言時刻を管理 |
| 発言確率 | 20% / 思考サイクル | 期待値: 2〜3時間に1回 |

> 初期実装時はインターバル制限なしで動作確認しやすくする。
> 安定後、WebGUI から任意の値（例：120分）に変更して運用する。
> 30分ごとに20%で発言するため、インターバル制限なし時の統計的期待値は約2.5時間に1回。

---

## コンテキスト収集

思考のインプットとして以下を収集する。

```python
context = {
    "datetime": "2025-04-07 14:32（月曜日）",
    "recent_conversations": [...],   # 直近N件の会話履歴
    "pending_memos": [...],          # 未処理メモ
    "pending_reminders": [...],      # 直近のリマインダー
    "ai_memory": [...],              # ミミ自身の記憶（ChromaDB）
    "people_memory": [...],          # ユーザーの記憶（ChromaDB）
    "last_monologue": "...",         # 前回の内的モノローグ
    "user_discord_status": "online", # Discordオンライン状態
}
```

新規 Unit が追加されるたびにコンテキストへ追加できる設計とする（例：天気・RSS）。

> **実装では ContextSource プラグイン方式を採用。**
> 各情報源（会話・メモ・リマインダー・天気・記憶・RSS・STT等）を統一的な
> `ContextSource` として扱い、InnerMind 本体を変更せずに追加可能。
> 詳細は `docs/plan.md` を参照。

---

## Ollama へのプロンプト設計

思考と発言は **2回の独立した LLM 呼び出し** に分かれる。

### LLM 呼び出し①：思考プロンプト（毎回実行）

```
システムプロンプト（ミミのペルソナ適用）

[状況]
現在時刻: {datetime}
ユーザーのDiscord状態: {user_discord_status}
最近の会話: {recent_conversations}
未処理メモ: {pending_memos}
前回の思考: {last_monologue}
記憶: {ai_memory} / {people_memory}

[指示]
あなた（ミミ）は今この状況を見て何を考えていますか？
誰にも見せない独り言として、自由に考えてください。
記憶に残すべきことがあれば memory_update に書いてください。

[出力形式 - JSON のみ]
{
  "monologue": "（内的モノローグ・誰にも見せない独り言）",
  "mood": "curious | calm | talkative | concerned | idle",
  "memory_update": "（記憶に残すべきことがあれば・なければ null）"
}
```

この結果は DB に保存されるが、ユーザーには送信しない。

---

### LLM 呼び出し②：発言プロンプト（発言判定をパスした時のみ実行）

呼び出し①の結果（monologue・mood）を受け取り、実際に何か言うかをミミ自身が判断する。

```
システムプロンプト（ミミのペルソナ適用）

[あなたの今の気持ち]
モノローグ: {monologue}
mood: {mood}

[状況]
現在時刻: {datetime}
最近の会話: {recent_conversations}

[指示]
今、ユーザーに何か話しかけたいですか？
話したいことがあれば message に書いてください。
特になければ message は null にしてください。

[出力形式 - JSON のみ]
{
  "message": "（Discordに送るメッセージ）| null"
}
```

`message` が `null` → 送信しない（ミミが自主的に黙った）
`message` に内容あり → Discord に送信

---

## 発言判定フロー

```
[思考フェーズ] 毎回実行
    │
    ├─ Ollama 未起動？ → スキップ（終了）
    │
    ├─ LLM呼び出し① → monologue・mood・memory_update 取得
    └─ DB保存（モノローグ・自己モデル・記憶更新）

[発言フェーズ] 条件チェック
    │
    ├─ 最終発言から min_speak_interval 未満？ → スキップ（※初期値0=制限なし）
    │
    ├─ ユーザーの Discord ステータス確認
    │    ├─ online / idle → 通過
    │    └─ offline / dnd → スキップ（モノローグのみ保存）
    │
    ├─ 確率判定（speak_probability: 20%）
    │    ├─ Hit  → 通過
    │    └─ Miss → スキップ
    │
    └─ LLM呼び出し② → message 取得
         ├─ message あり → Discord 送信 + 発言時刻を DB に記録
         └─ message null → スキップ（ミミが自主的に黙った）
```

---

## 記憶の3層構造

| 層 | 内容 | 実装 | 更新タイミング |
|---|---|---|---|
| **エピソード記憶** | いつ何があったか | ChromaDB（既存） | 会話のたびに |
| **ユーザーモデル** | ユーザーの習慣・状態の推測 | ChromaDB（既存 people_memory） | 思考サイクルで更新 |
| **自己モデル** | ミミ自身の興味・気になっていること | SQLite（新規テーブル） | 思考サイクルで更新 |

### 自己モデル（SQLite テーブル設計）

```sql
CREATE TABLE mimi_self_model (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,   -- 'mood' | 'interest' | 'concern' など
    value       TEXT NOT NULL,
    updated_at  DATETIME NOT NULL
);
```

例：
```
key='mood',     value='curious'
key='interest', value='ユーザーが最近よく言う「疲れた」が気になる'
key='concern',  value='先週の相談、その後どうなったか聞けていない'
```

---

## 内的モノローグの保存と WebGUI 表示

### DB テーブル

```sql
CREATE TABLE mimi_monologue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    monologue   TEXT NOT NULL,
    mood        TEXT,
    did_notify  BOOLEAN DEFAULT FALSE,  -- 実際に発言したか
    notified_message TEXT,              -- 発言した内容（あれば）
    created_at  DATETIME NOT NULL
);
```

### WebGUI への追加

`/web/app.py` に以下のエンドポイントを追加：

```
GET  /api/monologue?limit=50
GET  /api/inner-mind/settings
POST /api/inner-mind/settings
```

設定エンドポイントで編集できる項目：

| 項目 | 説明 |
|---|---|
| `enabled` | 思考サイクルのON/OFF |
| `speak_probability` | 発言確率（0〜100%） |
| `min_speak_interval_minutes` | 最低発言インターバル（分）。0=制限なし |

表示イメージ：
```
14:32  [curious]  「そういえば昨日の話、気になるな...」  ✉ 送信済
12:01  [calm]     「特に何もないか。平和。」
09:15  [talkative] 「朝だし何か話しかけようかな」        ✉ 送信済
```

---

## heartbeat.py との統合

```python
# heartbeat.py

THINKING_INTERVAL = 6  # 30分間隔想定（5分ハートビート × 6）

class Heartbeat:
    def __init__(self):
        self._tick = 0
        self.inner_mind = InnerMind(...)

    async def on_heartbeat(self):
        self._tick += 1

        # 既存処理
        await self._check_reminders()

        # 思考サイクル
        if self._tick % THINKING_INTERVAL == 0:
            await self.inner_mind.think()
```

---

## 設定ファイル（config.yaml への追加）

```yaml
inner_mind:
  enabled: true
  thinking_interval_ticks: 6      # ハートビート何回に1回思考するか
  speak_probability: 0.20         # 発言確率（0.0〜1.0）
  min_speak_interval_minutes: 0   # 最低発言インターバル（分）。0=制限なし。WebGUIで変更可能
  quiet_hours:                    # Discordオフライン時は自動制御するため基本不要
    enabled: false
    start: 0
    end: 7
```

---

## 実装優先順位

### Phase 1：土台
1. `mimi_monologue` / `mimi_self_model` テーブル追加（`database.py`）
2. `inner_mind.py` 作成（思考サイクル・発言判定）
3. `heartbeat.py` に統合
4. WebGUI にモノローグ閲覧ページ追加

### Phase 2：コンテキスト拡充
- 新規 Unit（天気・RSS 等）が追加されるたびにコンテキストへ追加
- `people_memory` の定期更新ロジック追加

### Phase 3：自己モデルの活用
- `mimi_self_model` を思考プロンプトへ注入
- 興味・気になることの蓄積と参照

---

## 関連ドキュメント

- `docs/units_plan.md` ― 新規 Unit 追加案（インプット拡充）
- `CLAUDE.md` ― 全体アーキテクチャ・開発ルール
