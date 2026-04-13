# InnerMind 改善設計書（未実装項目）

> 実装済み項目は削除済み。現時点で未着手の改善アイデアのみを残す。

---

## 1. Discordアクティビティ検出と収集/思考の分離

### 背景
PC側のアクティビティ収集（ゲーム/フォアグラウンド）は `ActivitySource` として実装済み。
残っているのは **Discord側のステータス・アクティビティ**を使った抑制ロジック。

### 設計：収集と思考の分離

```
ユーザー活発時:  情報を収集・キャッシュするだけ（LLM呼び出しなし）
ユーザー非活発時: 蓄積した情報をまとめてLLMで思考（Ollamaが空いている）
```

### 判定ロジック

- 直近N分以内にユーザー発言があるか（デフォルト10分）
- `inner_mind.active_threshold_minutes` で設定可能

### Discordアクティビティ検出

discord.py の `member.activities` から以下を取得:

| 型 | 検出内容 | 例 |
|---|---|---|
| `discord.Streaming` | 配信中（Twitch/YouTube） | `Streaming on Twitch` |
| `discord.Game` | ゲームプレイ中（Steam含む） | `Playing ELDEN RING` |
| `discord.Spotify` | Spotify再生中 | `Listening to ...` |
| `discord.CustomActivity` | カスタムステータス | `仕事中` |

### 抑制ルール

| 状態 | InnerMindの動作 | 理由 |
|------|-----------------|------|
| **配信中（Streaming）** | **完全停止** | リソース競合最大 |
| **ゲーム中（Game）** | **収集のみ**（LLMなし） | GPU/CPU使用中だが情報は溜めたい |
| **直近N分内に発言あり** | **収集のみ**（LLMなし） | Ollama競合回避 |
| **Spotify再生中** | 通常動作 | リソース消費が軽い |
| **非活発（idle）** | **フル思考サイクル** | Ollamaが空いている |

### 思考への活用
取得したアクティビティは抑制判定だけでなく、思考フェーズのコンテキストにも注入する。
「ユーザーは今ELDEN RINGをプレイ中」→ 思考の質が上がる。

```python
async def _get_user_activity(self) -> dict:
    """ユーザーのステータスとアクティビティを取得。"""
    # member.activities から Streaming/Game/Spotify/Custom を抽出
    return {"status": "online", "activities": [...]}
```

### 前提
Discord Developer Portal で **Presence Intent** と **Server Members Intent** の特権インテント許可が必要。

---

## 2. 追加 ContextSource 候補

既存の ContextSource プラグインシステムに追加予定のソース。

| ソース | 内容 | 取得方法 | priority |
|--------|------|----------|----------|
| **GitHub活動** | 最近のcommit/issue | GitHub API or git log | 80 |
| **X/Twitterタイムライン** | フォロー先の投稿 | X API | 90 |
| **Webニュース** | 特定ジャンルの話題 | Tavily / RSS | 100 |

各ソースは `ContextSource.update()` フックで背景更新する想定。
InnerMind 思考サイクル時は `collect()` でキャッシュ読取りのみ。

---

## 3. 並列LLM処理の追加最適化

### インスタンス別メトリクス追跡
- Ollama インスタンス別の成功率・レイテンシを記録
- 不調なインスタンスへのルーティングを減らす
- 運用データが貯まってから検討

### GPU メモリ使用量に基づく動的ルーティング
- 各PCのGPU使用率を参照してルーティング判断
- `nvidia_gpu_exporter` 導入が前提
