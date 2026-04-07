# Inner Mind 設計メモ

## 概要

`docs/autonomous_design.md` に基づき実装済み。
思考サイクルで内的モノローグを生成し、条件を満たした時のみDiscordに自発発言する。

**拡張性の核心:** コンテキストソースをプラグイン方式で管理し、
RSS・STT等の将来のインプットを InnerMind 本体を変更せずに追加できる設計。

---

## アーキテクチャ

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
    │       └── ContextSourceRegistry.collect_all()
    │               ├── ConversationSource  → 直近の会話履歴
    │               ├── MemoSource          → 未処理メモ
    │               ├── ReminderSource      → 直近のリマインダー
    │               ├── MemorySource        → ai_memory / people_memory
    │               └── WeatherSource       → 天気サブスク地域の天気情報
    │
    ├── _think_phase()   ← 全コンテキストを統合してLLMへ
    └── _speak_phase()
```

### ファイル構成

```
src/inner_mind/
├── __init__.py
├── core.py                    # InnerMind クラス本体
├── prompts.py                 # 思考・発言プロンプトテンプレート
└── context_sources/
    ├── __init__.py
    ├── base.py                # ContextSource 基底クラス
    ├── registry.py            # ContextSourceRegistry
    ├── conversation.py        # ConversationSource（直近会話）
    ├── memo.py                # MemoSource（メモ）
    ├── reminder.py            # ReminderSource（リマインダー）
    ├── memory.py              # MemorySource（ai_memory / people_memory）
    └── weather.py             # WeatherSource（天気サブスクリプション）
```

### shared パラメータの流れ

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
        └── WeatherSource.collect(shared)       → shared 不使用（DB直読み）
```

---

## 将来のソース追加手順

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

### 自律行動シナリオ例

**RSS × ユーザーの関心:**
RSSSource で「○○の新DLCが発表」、MemorySource で「○○が気になる」
→ 発言: 「前気になるって言ってた○○、新DLC出るみたいだよ！」

**天気 × リマインダー:**
WeatherSource で「明日は雨」、ReminderSource で「明日 14:00 外出予定」
→ 発言: 「明日雨っぽいけど、14時の外出大丈夫？傘忘れないでね」

**メモ × 時間経過:**
MemoSource で「3日前のメモ: APIの設計案を考える」
→ 発言: 「そういえばAPIの設計案、進んだ？」

---

## 設計判断メモ

| 判断 | 理由 |
|------|------|
| 既存データも全て ContextSource 化 | メモ・リマインダー・天気を特別扱いしない。統一設計で将来のRSS・STTと同列に扱える |
| パッケージ構成（`inner_mind/`） | ソース追加でファイルが増えるため単一ファイルでは管理困難 |
| ソースの `format_for_prompt()` | 各ソースが自身のデータを最もよく知っている。プロンプト構築を分散 |
| `priority` による収集順序 | プロンプト内のセクション順序を制御（重要度順に並べられる） |
| 失敗ソースのスキップ | 1つのソースが壊れても思考サイクル全体を止めない |
| ソースは「読むだけ」 | 各ソースは既存DBを参照するだけ。データの書き込み・更新は各Unitの責務 |
| Ollama専用（Gemini不可） | 自律思考は内部処理でありAPI課金を避ける |
| 設定は DB settings 優先 | WebGUIから動的変更可能にするため。config.yaml はデフォルト値 |
| JSON パース 4段階フォールバック | ローカル LLM は JSON を壊しやすい。最悪でもレスポンス全文をモノローグとして保存 |
| 自発発言を conversation_log に記録 | 次回の思考サイクルで自分の発言も文脈に入る。会話の連続性を保つ |
| think() はバックグラウンド実行 | LLM 2回呼び出しで数分かかる可能性がある。ハートビートをブロックしない |
| quiet_hours は実装しない | Discordステータス（offline/dnd）で同等の制御が可能。重複する仕組みを避ける |
| shared パラメータで動的検索 | MemorySource の検索クエリを固定文字列にしない。前回モノローグや直近会話を手がかりに関連記憶を引く |

---

## 未対処のリスク

- **Ollama 同時リクエスト競合**: inner_mind の LLM 呼び出し中にユーザーのチャットが来ると、Ollama 側でキューイングされ後発がタイムアウトする可能性。問題が顕在化したら `asyncio.Lock` で排他制御を追加する
- **コンテキスト長**: ソースが増えるとプロンプトが肥大化。各ソースの `format_for_prompt()` で簡潔に要約する設計で対処。将来的にトークン上限管理が必要になる可能性
- **Intents 設定**: Discord Developer Portal で Presence Intent と Server Members Intent の特権インテント許可が必要
