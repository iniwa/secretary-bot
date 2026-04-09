# WebGUI Units リファレンス

> 調査日: 2026-04-09

---

## ユニット一覧

| Unit | 目的 | 委任先 | データ保存先 | WebGUI操作 |
|------|------|--------|-------------|-----------|
| reminder | リマインダー/Todo管理 | - | SQLite | CRUD, スヌーズ |
| memo | メモ保存・検索 | - | SQLite | CRUD, 検索 |
| timer | カウントダウンタイマー | - | メモリ（非永続） | 表示, 作成 |
| weather | 天気予報・定期通知 | - | SQLite + Open-Meteo API | 購読管理 |
| rss | RSSフィード集約 | - | SQLite | フィード管理, 記事閲覧 |
| chat | 自由会話 | - | ChromaDB | チャットUI |
| status | システムステータス | - | StatusCollector | ダッシュボード |
| power | PC電源管理(WoL/shutdown) | - | WoL API | 管理者限定 |
| calendar | Googleカレンダー連携 | - | Google API + SQLite | イベント作成 |
| web_search | SearXNG検索 | - | SearXNG API | 検索UI |
| rakuten_search | 楽天商品検索 | - | HTMLスクレイピング | 検索UI |

## 補助システム

| System | 目的 | 説明 |
|--------|------|------|
| remote_proxy | 委任ラッパー | `DELEGATE_TO`設定でWindows Agentへ透過的に転送 |
| agent_pool | Agent管理 | 複数Windows PCの優先度・ヘルス管理 |
| heartbeat | 定期バックグラウンド処理 | InnerMind, RSS取得, STT収集, スヌーズ再通知 |
| inner_mind | 自律思考システム | 6つの思考レンズ, 自発発言, ムード管理 |

---

## 各ユニット詳細

### 1. Reminder Unit

**チャットコマンド**: add, list, edit, delete, done, contextual_done, contextual_snooze, todo_add, todo_list, todo_edit, todo_done, todo_delete

**データモデル**:
- `reminders`: id, message, remind_at, repeat_type, repeat_interval, active, notified, snooze_count, snoozed_until
- `todos`: id, title, done, due_date, done_at

**WebGUI要件**: 一覧表示、編集ダイアログ、完了/削除、スヌーズUI

### 2. Memo Unit

**チャットコマンド**: save, list, search, edit, append, delete

**データモデル**:
- `memos`: id, content, tags, user_id, created_at

**WebGUI要件**: メモ一覧、キーワード検索、インライン編集、タグ表示

### 3. Timer Unit

**チャットコマンド**: set_timer（分数 + メッセージ）

**データモデル**: メモリ内のみ（`_active_timers`, `_timer_info`）

**WebGUI要件**: アクティブタイマー一覧、残り時間のリアルタイム表示

### 4. Weather Unit

**チャットコマンド**: get_weather, weekly, subscribe, unsubscribe, list

**データモデル**:
- `weather_subscriptions`: id, location, latitude, longitude, notify_hour, notify_minute, active

**WebGUI要件**: 購読一覧、有効/無効切替、通知時刻変更、削除

### 5. RSS Unit

**チャットコマンド**: digest, list, add, remove, enable_category, disable_category

**データモデル**:
- `rss_feeds`: id, url, title, category, is_preset
- `rss_articles`: id, feed_id, title, url, summary, published_at
- `rss_user_prefs`: user_id, feed_id/category, enabled
- `rss_feedback`: user_id, article_id, rating

**WebGUI要件**: フィード管理（追加/削除）、記事一覧、手動フェッチ、カテゴリフィルタ

### 6. Chat Unit

**設定**: `history_minutes`（会話履歴の遡り時間）

**メモリ連携**: AIMemory（Ollama専用）、PeopleMemory

**WebGUI要件**: チャットUI（実装済み）

### 7. InnerMind

**思考レンズ（6種、ローテーション）**:
1. concrete: 具体的な事実にコメント
2. empathy: ユーザーの気持ちを想像
3. time_space: 時間・季節・天気との関連
4. curiosity: 一つのトピックを深掘り
5. reflection: 最近の出来事を振り返り
6. rest: 静かに待つモード

**コンテキストソース**: 会話, メモ, リマインダー, メモリ, 天気, RSS, STT

**設定項目**:
- enabled: 有効/無効
- thinking_interval_ticks: 思考頻度
- speak_probability: 自発発言確率
- min_speak_interval_minutes: 最小発言間隔
- speak_channel_id: Discord発言チャンネル
- target_user_id: ユーザー監視対象

**WebGUI要件**: 設定変更、モノローグ履歴、ムード表示、コンテキストソース確認

---

## 注意事項

- **todo.py は存在しない**: TodoはReminderUnit内に組み込み
- **monologue.py は存在しない**: InnerMindが管理（`mimi_monologue`テーブル）
- **obs.py は存在しない**: OBSはWindows Agent経由（`/api/obs/*`エンドポイント）
- **stt.py は存在しない**: STTはheartbeat + Windows Agent経由（`/api/stt/*`エンドポイント）
- **input_relay は Windows Agent サブモジュール**: `windows-agent/tools/input-relay/`
