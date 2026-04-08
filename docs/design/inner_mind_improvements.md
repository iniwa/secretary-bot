# InnerMind 改善設計書

> 2026-04-08 作成。モノローグログ分析から導出した改善設計。
> 「実装済み」「今回実装」「未実装（将来）」を区分。

---

## 1. 実装済み（本日コミット済み）

### 1.1 思考品質の6項目改善
- 直近5件のモノローグ履歴をプロンプトに注入し重複思考を厳禁化
- memory_update保存前にChromaDB類似検索で重複記憶を防止（distance < 0.3）
- JSONパース二次検証でmonologueフィールド内のネストJSONを再パース
- 発言メッセージを80文字以内に制限し自然な雑談レベルに
- コンテキスト不変時にMD5ハッシュ比較で思考サイクルをスキップ
- 自己モデルに interest_topic フィールドを追加しLLM出力から保存

---

## 2. 今回実装：会話トピックセグメンテーション

### 2.1 問題
会話履歴がフラットな時系列リストとしてInnerMindに渡されるため、話題の遷移が混ざって「情報が交錯している」と誤認する。

### 2.2 設計

#### 処理フロー
```
[conversation_log 30件取得]
    | inner_mind発言を除外
    | channel_name付き
[URL内容を並列取得（最大5件, 各500文字）]
    | メッセージに紐付け
[ヒューリスティクス: セグメント分割]
    分割条件: 時間ギャップ(30分) / チャンネル名変更 / ユニット境界
    |
[キャッシュチェック: 最新message ID比較]
    | 変化なし → キャッシュ返却
    | 変化あり
[LLM 1回: セグメント要約・ラベル付け]
    | 失敗時 → ヒューリスティクス要約にフォールバック
[構造化された会話コンテキスト]
    |
[InnerMind 思考フェーズ（LLM）]
```

#### DBスキーマ変更
```sql
ALTER TABLE conversation_log ADD COLUMN channel_name TEXT DEFAULT '';
```

#### ファイル変更
| ファイル | 変更内容 |
|----------|----------|
| `src/database.py` | `log_conversation` に `channel_name` 引数追加、マイグレーション |
| `src/bot.py` | `message.channel.name` を `log_conversation` に渡す |
| `src/inner_mind/context_sources/conversation.py` | セグメント分割 + LLM要約に全面改修 |
| `src/inner_mind/prompts.py` | `CONVERSATION_SUMMARY_SYSTEM/PROMPT` 追加 |

#### セグメント分割ルール
| ルール | 条件 | 理由 |
|--------|------|------|
| 時間ギャップ | 前メッセージから30分以上 | 話題の自然な区切り |
| チャンネル名変更 | discord内の別チャンネルへ | 強い話題変更シグナル |
| チャンネル種別変更 | discord ↔ webgui | 操作コンテキストが異なる |
| ユニット境界 | assistant応答のunit変化 | 機能的な話題の切り替え |

#### inner_mind自発発言の除外
`unit="inner_mind"` のメッセージはセグメント対象から除外。自己参照ループを防止。

#### LLM要約プロンプト
各セグメントの生メッセージ（URL内容付き）を1回のLLM呼び出しで構造化要約。
出力形式: `[話題: ラベル] (チャンネル, 時間帯) + 要約文1-2行`

---

## 3. 未実装：ContextSource TTL・取得上限制御

### 3.1 問題
ContextSourceが増えるほど毎サイクルの取得・処理コストが線形に増加する。

### 3.2 設計

#### TTL（キャッシュ有効期間）
`ContextSource` 基底クラスに `ttl_minutes` プロパティを追加。前回取得からTTL以内ならキャッシュを返す。

```python
class ContextSource:
    ttl_minutes: int = 0  # 0 = 毎回取得
```

| ソース | ttl_minutes | 理由 |
|--------|-------------|------|
| 会話 | 0 | 常に最新が必要 |
| リマインダー | 0 | 時間に敏感 |
| システム状態 | 5 | 数分おきで十分 |
| 記憶（ChromaDB） | 10 | 急変しない |
| カレンダー | 15 | 予定は頻繁に変わらない |
| 天気 | 30 | 30分おき |
| RSS / ニュース | 60 | 1時間おき |
| X/Twitter | 30 | 30分おき |
| GitHub | 60 | 1時間おき |

#### 1サイクルあたりの取得上限
```python
class ContextSourceRegistry:
    max_fresh_per_cycle: int = 5
```
TTL切れソースが上限を超えた場合、priority順に上位N件だけ新規取得し、残りは次サイクルへ。キャッシュがあるソースは常にキャッシュから供給。

---

## 4. 未実装：ユーザーアクティビティ検出と収集/思考の分離

### 4.1 問題
ユーザーが活発に動いている = 情報が豊富 = だがOllamaを自分で使いたい時間帯。
InnerMindがこのタイミングでLLMを呼ぶとリソース競合が発生する。

### 4.2 設計：収集と思考の分離

```
ユーザー活発時:  情報を収集・キャッシュするだけ（LLM呼び出しなし）
ユーザー非活発時: 蓄積した情報をまとめてLLMで思考（Ollamaが空いている）
```

#### 活発/非活発の判定
- 直近N分以内にユーザー発言があるか（デフォルト10分）
- `inner_mind.active_threshold_minutes` で設定可能

#### Discordアクティビティ検出
discord.py の `member.activities` から以下を取得:

| 型 | 検出内容 | 例 |
|---|---|---|
| `discord.Streaming` | 配信中（Twitch/YouTube） | `Streaming on Twitch` |
| `discord.Game` | ゲームプレイ中（Steam含む） | `Playing ELDEN RING` |
| `discord.Spotify` | Spotify再生中 | `Listening to ...` |
| `discord.CustomActivity` | カスタムステータス | `仕事中` |

#### 抑制ルール
| 状態 | InnerMindの動作 | 理由 |
|------|-----------------|------|
| **配信中（Streaming）** | **完全停止** | リソース競合最大 |
| **ゲーム中（Game）** | **収集のみ**（LLMなし） | GPU/CPU使用中だが情報は溜めたい |
| **直近N分内に発言あり** | **収集のみ**（LLMなし） | Ollama競合回避 |
| **Spotify再生中** | 通常動作 | リソース消費が軽い |
| **非活発（idle）** | **フル思考サイクル** | Ollamaが空いている |

#### アクティビティ情報の思考への活用
取得したアクティビティは抑制だけでなく、思考フェーズのコンテキストとしても注入する。
「ユーザーは今ELDEN RINGをプレイ中」→ 思考の質が上がる。

```python
async def _get_user_activity(self) -> dict:
    """ユーザーのステータスとアクティビティを取得。"""
    # member.activities から Streaming/Game/Spotify/Custom を抽出
    return {"status": "online", "activities": [...]}
```

---

## 5. 未実装：追加 ContextSource 候補

ContextSource プラグインシステムで追加予定のソース:

| ソース | 内容 | 取得方法 | priority |
|--------|------|----------|----------|
| **Googleカレンダー** | 今日〜明日の予定 | Google Calendar API | 60 |
| **RSSフィード** | 興味あるサイトの新着 | feedparser等 | 70 |
| **GitHub活動** | 最近のcommit/issue | GitHub API or git log | 80 |
| **システム状態** | 各PCのオン/オフ、負荷 | AgentPoolの既存データ | 55 |
| **X/Twitterタイムライン** | フォロー先の投稿 | X API | 90 |
| **天気（実データ）** | 現在の天気・予報 | OpenWeatherMap等 | 65 |
| **Webニュース** | 特定ジャンルの話題 | Tavily / RSS | 100 |

---

## 実装優先度

| 順位 | 項目 | 理由 |
|------|------|------|
| 1 | 会話セグメンテーション（2章） | 思考品質の根本改善 |
| 2 | アクティビティ検出 + 収集/思考分離（4章） | リソース競合回避 |
| 3 | TTL・取得上限制御（3章） | ソース増加への備え |
| 4 | 追加ContextSource（5章） | 3章の制御基盤の上に構築 |
