# InnerMind 再設計 — 自律行動への刷新

> 状態: **準備中**（ユーザーのGOサイン待ち）
> 目的: 「確率発言する独り言機」から「観測→判断→行動を選ぶ自律エージェント」へ

---

## 全体像

```
[Event Bus]              ← 外界イベント（後続フェーズ）
      ↓
[Observer]               ← ContextSource から関連のみ収集
      ↓
[Decider (LLM)]          ← decision を1つ選び reasoning を添える
      ↓
[Actuator]               ← Tier別ゲート → 実行 or ask_user 承認待ち
      ↓
[Reflector]              ← monologue/decision を mimi_monologue に保存
```

## 用語

| 用語 | 意味 |
|---|---|
| **monologue** | 内面の自由文。`no_op` 時のみ記録される独り言 |
| **decision** | `action` + `params` の組。今回取る行動そのもの |
| **reasoning** | decision を選んだ理由（行動時に必須） |
| **no_op** | action の1種。「何もしない」という decision |

## Tier 定義

| Tier | 行動 | 自律実行 |
|---|---|---|
| T0 内省 | `memorize` / `update_self_model` / `recall` | 直接実行 |
| T1 受動的表出 | `speak` / `ask_user` | 直接実行 |
| T2 能動的支援 | `memo.add` / `reminder.add` / `timer.add` | **必ず ask_user 経由** |
| T3 外界送信 | `web_search` / `rakuten_search` / `calendar.write` / `rss.*` / `power.*` / `docker_log_monitor.*` | **必ず ask_user 経由** |
| T4 破壊的 | （将来用） | config で明示解放のみ |

自律モードのプリセット：
- `off`: InnerMind 停止
- `observe_only`: T0 のみ（記憶整理だけする）
- `proposal`: T0 + T1 + ask_user 経由の T2/T3
- `full`: T4 以外を自動実行（現時点では推奨しない）

---

## 1. DB スキーマ確定版

### 1.1 既存テーブル ALTER

```sql
-- mimi_monologue にカラム追加
ALTER TABLE mimi_monologue ADD COLUMN action        TEXT;     -- no_op/speak/memorize/update_self_model/recall/ask_user/call_unit
ALTER TABLE mimi_monologue ADD COLUMN reasoning     TEXT;     -- 行動時のみ
ALTER TABLE mimi_monologue ADD COLUMN action_params TEXT;     -- JSON
ALTER TABLE mimi_monologue ADD COLUMN action_result TEXT;     -- JSON (実行結果)
ALTER TABLE mimi_monologue ADD COLUMN pending_id    INTEGER;  -- ask_user 経由時の pending_actions.id
```

- `action = "no_op"` の時は従来通り `monologue` カラムに内省文、`reasoning` は NULL
- `action != "no_op"` の時は `reasoning` に理由、`monologue` は NULL（将来「行動時にも独り言を残す」拡張は案γとして保留）

### 1.2 新規テーブル

```sql
CREATE TABLE IF NOT EXISTS pending_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    monologue_id    INTEGER,                         -- mimi_monologue.id への外部キー相当
    tier            INTEGER NOT NULL,                -- 2 or 3
    unit_name       TEXT,                            -- NULL なら内部アクション
    method          TEXT,                            -- "add" 等
    params          TEXT NOT NULL,                   -- JSON
    reasoning       TEXT NOT NULL,                   -- ユーザー提示用の理由
    summary         TEXT NOT NULL,                   -- 確認メッセージの要約文
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/approved/rejected/expired/executed/cancelled/failed
    discord_message_id TEXT,                         -- 確認メッセージの Discord ID
    channel_id      TEXT,                            -- 送信先チャンネル
    user_id         TEXT NOT NULL,                   -- 承認対象のユーザー（現状は target_user_id 固定、将来の複数ユーザー対応のためカラム化）
    result          TEXT,                            -- 実行結果 JSON
    error           TEXT,                            -- 失敗時のエラー
    created_at      DATETIME NOT NULL,
    resolved_at     DATETIME,
    expires_at      DATETIME NOT NULL                -- created_at + タイムアウト
);

CREATE INDEX IF NOT EXISTS idx_pending_actions_status ON pending_actions(status);
CREATE INDEX IF NOT EXISTS idx_pending_actions_user   ON pending_actions(user_id, status);
```

### 1.3 settings キー一覧

**命名規則**: `<domain>.<group>.<key>` （既存の `inner_mind.*` を踏襲）

#### LLM
```
llm.ollama_model           = "gemma4"
llm.ollama_url             = "http://192.168.1.211:11434"
llm.ollama_timeout         = "60"
llm.gemini_model           = "gemini-2.0-flash"
```

#### Gemini（既存 `gemini_config` API と互換）
```
gemini.conversation        = "true"
gemini.memory_extraction   = "false"
gemini.unit_routing        = "true"
gemini.monthly_token_limit = "0"
```

#### Heartbeat
```
heartbeat.interval_with_ollama_minutes    = "15"
heartbeat.interval_without_ollama_minutes = "180"
heartbeat.compact_threshold_messages      = "20"
```

#### InnerMind 基本
```
inner_mind.enabled                   = "true"
inner_mind.thinking_interval_ticks   = "2"
inner_mind.min_speak_interval_minutes = "0"
inner_mind.speak_channel_id          = "..."
inner_mind.target_user_id            = "..."
inner_mind.active_threshold_minutes  = "10"
```

#### InnerMind 自律（新規）
```
inner_mind.autonomy.mode                     = "proposal"   # off/observe_only/proposal/full
inner_mind.autonomy.approval_timeout_minutes = "30"          # 既定30分
inner_mind.autonomy.t2_daily_limit           = "0"          # 0 = 無制限
inner_mind.autonomy.t3_daily_limit           = "0"
inner_mind.autonomy.concurrent_pending       = "queue"      # single/queue/prefer_new
inner_mind.autonomy.show_reasoning           = "true"
inner_mind.autonomy.t2_allowed_units         = "memo.add,reminder.add,timer.add"   # カンマ区切り
inner_mind.autonomy.t3_allowed_units         = ""
inner_mind.autonomy.notify_pending           = "true"       # 承認待ちが増えたら通知する
```

#### InnerMind GitHub / Tavily（既存 config から移行）
```
inner_mind.github.username             = "iniwa"
inner_mind.github.lookback_hours       = "24"
inner_mind.github.max_items            = "8"
inner_mind.tavily_news.queries         = "生成AI,VTuber"   # カンマ区切り
inner_mind.tavily_news.max_results_per_query = "3"
inner_mind.tavily_news.lookback_days   = "2"
inner_mind.tavily_news.topic           = "news"
```

#### キャラクター
```
character.name        = "ミミ"
character.persona     = "..."
character.ollama_only = "false"
```

#### Chat
```
chat.history_minutes = "0"
```

#### 外部サービス
```
rss.fetch_interval_minutes      = "60"
rss.digest_hour                 = "9"
rss.article_retention_days      = "30"
rss.max_articles_per_category   = "5"

weather.default_location        = "東京"
weather.umbrella_threshold      = "50"

searxng.url                     = "http://localhost:8888"
searxng.max_results             = "5"
searxng.fetch_pages             = "3"
searxng.max_chars_per_page      = "3000"

rakuten_search.max_results          = "5"
rakuten_search.fetch_details        = "true"
rakuten_search.detail_concurrency   = "5"
rakuten_search.detail_max_desc_chars= "300"

stt.enabled                             = "true"
stt.polling_interval_minutes            = "5"
stt.processing.summary_threshold_chars  = "2000"

delegation.thresholds.cpu_percent    = "80"
delegation.thresholds.memory_percent = "85"
delegation.thresholds.gpu_percent    = "80"
```

#### メモリ
```
memory.sweep_enabled    = "true"
memory.sweep_stale_days = "90"      # ChromaDBの鮮度sweep閾値（saved_atがこの日数以上前かつhit_count==0で削除）
```

#### アクティビティ
```
activity.enabled                        = "true"
activity.block_rules.obs_streaming      = "true"
activity.block_rules.obs_recording      = "true"
activity.block_rules.obs_replay_buffer  = "false"
activity.block_rules.gaming_on_main     = "true"
activity.block_rules.discord_vc         = "false"

docker_monitor.enabled                 = "true"
docker_monitor.check_interval_seconds  = "60"
docker_monitor.cooldown_minutes        = "30"
docker_monitor.max_lines_per_check     = "200"
```

**config.yaml に残すもの**（DB に移さない）:
- `windows_agents:` (リスト構造。GUI化は範囲外)
- `units.*.enabled` (ユニット自動ロード系)
- `rss.presets:` (フィード構造が複雑。将来別GUIで編集)
- `docker_monitor.containers:` (同上)
- `wol.url`, `metrics.victoria_metrics_url` (インフラ設定)
- `debug.*`

---

## 2. API エンドポイント仕様

### 2.1 既存エンドポイントの扱い

| 既存 | 扱い |
|---|---|
| `/api/llm-config` | そのまま維持（内部で DB 読み書き） |
| `/api/gemini-config` | そのまま維持 |
| `/api/heartbeat-config` | そのまま維持 |
| `/api/chat-config` | そのまま維持 |
| `/api/rakuten-config` | そのまま維持 |
| `/api/persona` | そのまま維持 |

後方互換のため、個別エンドポイントは残す。**新規は汎用 `/api/settings` に寄せる**。

### 2.2 新規エンドポイント

#### 汎用 settings
```
GET  /api/settings?prefix=<str>           → { "key1": "val1", ... }
POST /api/settings                         { "key1": "val1", ... }   一括保存
DELETE /api/settings/<key>                 単一削除
```

#### InnerMind 自律
```
GET  /api/inner-mind/autonomy              → { mode, approval_timeout_minutes, t2_daily_limit, ... }
POST /api/inner-mind/autonomy              保存
GET  /api/inner-mind/autonomy/units        → { "tier2": [{unit_name, method, description, allowed}...], "tier3": [...] }
  ※ BaseUnit.AUTONOMOUS_ACTIONS から列挙
```

#### pending_actions
```
GET  /api/pending                          → { items: [...], counts: {pending, today_t2, today_t3} }
GET  /api/pending/<id>
POST /api/pending/<id>/approve             → 実行して result 返す
POST /api/pending/<id>/reject              
POST /api/pending/<id>/cancel              
GET  /api/pending/history?limit=50         履歴
```

#### 通知バッジ用カウンタ
```
GET  /api/pending/unread-count             → { count: N }     ※ヘッダナビバッジ用
```

#### RSS / STT / Activity などの設定
いずれも既存パターンに合わせ `/api/<domain>-config` を追加。同じ構造で `GET`/`POST`。

---

## 3. アコーディオン UI マップ

### 3.1 セクション構成

| # | セクション | デフォルト展開 | 含むフィールド |
|---|---|---|---|
| 1 | LLM & モデル | 閉 | ollama_model / ollama_url / ollama_timeout / gemini_model |
| 2 | Gemini（課金注意） | 閉 | conversation / memory_extraction / unit_routing / monthly_token_limit |
| 3 | Heartbeat | 閉 | interval_with_ollama / interval_without_ollama / compact_threshold |
| 4 | InnerMind 基本 | 閉 | enabled / thinking_interval_ticks / min_speak_interval / speak_channel_id / target_user_id / active_threshold_minutes |
| 5 | **InnerMind 自律（新規）** | 開 | mode / approval_timeout / t2_daily_limit / t3_daily_limit / concurrent_pending / show_reasoning / notify_pending / t2_allowed_units (チェックリスト) / t3_allowed_units (チェックリスト) |
| 6 | InnerMind 外部情報 | 閉 | github.* / tavily_news.* |
| 7 | キャラクター | 閉 | name / persona / ollama_only |
| 8 | Chat | 閉 | history_minutes |
| 9 | 外部サービス | 閉 | RSS / Weather / SearXNG / Rakuten / STT / Delegation（サブグリッドで分割） |
| 10 | メモリ & アクティビティ | 閉 | memory.* / activity.* / docker_monitor.* |

### 3.2 UI パターン

- `<details>` / `<summary>` ネイティブ要素をベースにカスタム CSS
- URL ハッシュ `#section=autonomy` で該当セクションのみ開く
- 各セクションの末尾に Save ボタン（セクション単位保存）
- 編集中に他セクションを触ると警告（optional、Phase 1 では省略）

### 3.3 フィールド入力型

| 型 | UI | 例 |
|---|---|---|
| bool | トグル | enabled |
| int | number input | thinking_interval_ticks |
| float | number input step=0.01 | speak_probability |
| enum | select | autonomy.mode, concurrent_pending |
| string | text input | speak_channel_id |
| textarea | textarea | persona |
| list | チェックリスト or tag input | t2_allowed_units |

---

## 4. AUTONOMY_TIER 初期値案（各ユニット）

| ユニット | TIER | AUTONOMOUS_ACTIONS | 備考 |
|---|---|---|---|
| `memo` | 2 | `["add"]` | 提案時に「○○をメモしてもいい?」 |
| `reminder` | 2 | `["add"]` | `cancel_all` などは非公開 |
| `timer` | 2 | `["add"]` | |
| `status` | 0 | `["get"]` | 状態取得は副作用なし |
| `chat` | — | `[]` | そもそも自律呼び出し対象外 |
| `weather` | 0 | `["get_current"]` | 情報取得のみ。`add_daily_notification` はT2 |
| `web_search` | 3 | `["search"]` | 外部API呼び出し＋課金可能性 |
| `rakuten_search` | 3 | `["search"]` | 外部API |
| `rss` | 3 | `[]` | 自律呼び出し不可（バックグラウンドのみ） |
| `power` | 3 | `["sleep", "shutdown"]` | T3扱いで承認必須 |
| `docker_log_monitor` | 3 | `[]` | 自律呼び出し不可 |
| `calendar` | 3 | `["create_event"]` | 書き込みは承認必須 |
| `zzz_disc` | — | — | **永続的に自律対象外**（ユーザー指示・確定） |

`BaseUnit` デフォルトは `AUTONOMY_TIER = 4`（最も安全）、`AUTONOMOUS_ACTIONS = []`。各ユニットが明示的にダウングレードする形。

---

## 5. Discord ApprovalView 仕様

### 5.1 メッセージ構造

```
【ミミからの提案】(embed)
──────────────────
📝 メモに「Blender のショートカット集」を追加してもいい?

理由: さっきの会話で3回参照してたから残しておくと便利そう

期限: 30分後 (2026-04-14 22:30)
──────────────────
[ OK ]  [ NG ]
```

### 5.2 View 実装

```python
class ApprovalView(discord.ui.View):
    def __init__(self, pending_id: int, timeout_seconds: int):
        super().__init__(timeout=timeout_seconds)
        # custom_id を pending_id 付きにして persistent_view 対応
```

- `custom_id = f"approval:ok:{pending_id}"` / `f"approval:ng:{pending_id}"`
- Bot 起動時に `bot.add_view(ApprovalView())` で persistent 登録
- タイムアウト時：view のボタンを無効化＋`status = 'expired'`
- 承認済みメッセージは embed を「✅ 承認済み」「❌ 却下」に書き換え
- ボタン押下者 = `pending_actions.user_id` 検証。別人は拒否

### 5.3 同時 pending の制御

`concurrent_pending` 設定値による分岐：
- `single`: 既存 pending がある間、新規 T2/T3 は `rejected_by_policy` で保存（monologue には残す）
- `queue`: `pending_actions` に積む。既存が resolved されたら次を送信
- `prefer_new`: 既存 pending を `cancelled_by_newer` にして新規送信

---

## 6. 通知方式

### 6.1 WebGUI ナビバッジ

- `GET /api/pending/unread-count` を定期 poll（既存の heartbeat poll に相乗り）
- ナビの「Pending」項目に赤バッジ `(3)` 表示
- 新規ページ: `#pending` → `src/web/static/js/pages/pending.js`

### 6.2 Discord 通知

- `ApprovalView` メッセージ自体が通知を兼ねる（Discord の標準通知で届く）
- 追加で DM は送らない（二重通知回避）
- 送信先: `inner_mind.speak_channel_id`（既存設定を流用）

### 6.3 pending ページの UI

```
/pending
├─ 承認待ち（pending）
│   各項目: summary / reasoning / tier / unit / params / 残り時間 / [承認] [却下] [キャンセル]
├─ 今日の履歴
│   approved / rejected / expired / executed / failed の一覧
└─ フィルタ: tier / status / 期間
```

---

## 7. config.yaml → DB シード規約

### 7.1 ブート時の初期シード

1. Bot 起動時、`settings` テーブルに `_seed_version` キーがなければ初回起動とみなす
2. config.yaml を全キー走査し、`settings` テーブルに存在しないキーのみを DB に書き込む
3. `_seed_version = 1` を設定して次回以降スキップ
4. 既存 DB のキーは**上書きしない**（ユーザーが WebGUI で変更した値を尊重）

### 7.2 優先順位

読み取り時の優先順位は既存の `inner_mind._get_setting` パターンを他 domain にも展開：

```
DB settings → config.yaml → コード内デフォルト
```

### 7.3 移行対象外の config.yaml 項目

1.3 末尾の「config.yaml に残すもの」参照。インフラ系・構造化データは yaml のまま。

### 7.4 Shim レイヤ

`src/config_provider.py`（新規）を作り、`bot.config.get_setting(key, default)` 形式の統一APIを提供。既存コードは段階的に移行。Phase 1 では既存の `bot.config.get(...)` と併存させる。

---

## 実装フェーズ再掲

| Phase | コミット | 内容 |
|---|---|---|
| 1 | `feat(web/settings): アコーディオン化と既存項目のDB移行` | Settings UI・API・シード |
| 2 | `feat(inner_mind): 自律アクション基盤（Tier制・承認View・pending_actions）` | DB / BaseUnit / Actuator / ApprovalView / pending ページ |
| 3 | `feat(inner_mind): 確率発言から decision 選択への刷新` | think() 刷新・新プロンプト・monologue ALTER |

3コミット完了後、まとめて `git push origin main`。各コミットはビルド通過すること。Phase 2/3 は feature flag (`inner_mind.autonomy.mode`) で挙動を制御するため、デフォルト `off` なら既存挙動維持。

### Phase 3 での削除対象

- `inner_mind.speak_probability` 設定キー（確率発言を廃止）
- `src/inner_mind/core.py` の `_check_speak_conditions` 内の確率ロール
- `SPEAK_PROMPT` / `_generate_message` / `_speak_phase` の現行実装（decision 形式に統合）
- `_build_speak_hint` / `SPEAK_HINT_CATEGORIES`（decision の reasoning に吸収）

---

## 決定済み事項（2026-04-14）

1. ✅ メモリ sweep 閾値: `memory.sweep_stale_days = 90`（コード既定値）
2. ✅ pending タイムアウト既定: 30分
3. ✅ pending 対象ユーザーは `inner_mind.target_user_id` 固定（カラムは拡張性のため残す）
4. ✅ zzz_disc は永続的に自律対象外
5. ✅ Phase 3 で `speak_probability` 関連を完全削除

## 残論点

（現時点なし。実装着手後に発生したものをここに追記していく）
