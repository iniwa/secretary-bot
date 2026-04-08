# RSSフィーダー + ニュース機能 設計書

## 概要
定期的にRSSを巡回し、ユーザの好みに合った記事をミミがカテゴリ別ダイジェストで紹介する機能。
「最近のニュース」機能はニュースサイトのRSSフィードとして統合。

## 設計判断（確定）

### 1. RSSソース管理方式
**採用: B — プリセット + ユーザー追加**
- config.yaml にジャンル別プリセットRSSソースを定義
- ユーザーが「このRSS追加して」で追加・削除も可能

> **将来案（C）**: LLMが会話から興味を推定し、関連RSSソースを提案する。
> 例: 「最近○○に興味あるんだよね」→ ミミが関連RSSを提案。
> 実装コストが高いため、B方式が安定してから検討。

### 2. ユーザー嗜好の学習方式
**採用: まずA（明示的フィードバック）で立ち上げ**
- 記事紹介後に「いいね/いらない」のリアクションで評価
- SQLite に `rss_feedback(article_id, user_id, rating)` で記録

> **将来実装（B）**: 会話履歴からの暗黙的な嗜好推定。
> PeopleMemory と同様に、会話から興味キーワードを抽出し、
> ChromaDB に嗜好ベクトルとして保存。記事との類似度で推薦精度を向上。
> A方式でデータが十分溜まってから段階的に導入。

### 3. 記事フィルタリング・推薦ロジック
**採用: タグマッチ + LLM要約/ChromaDB類似度のハイブリッド**
- 第1段階: RSSソースのカテゴリタグとユーザの好みタグで粗いフィルタリング
- 第2段階: LLMで記事を要約→ChromaDBにベクトル化→嗜好ベクトルと類似度比較

### 4. 巡回・通知タイミング
- **RSS取得（フェッチ）**: ラズパイで実行するためいつでもOK。1時間間隔
- **LLM処理（要約・推薦）**: ユーザーが非アクティブ時（ゲーム・配信をしていない状態）に実行
- **通知**: 1日1回ダイジェスト形式（設定時刻、デフォルト9時）

### 5. ダイジェスト通知フォーマット
- **カテゴリ別に分けて表示**
- 各カテゴリの表示件数上限は **WebGUIで調整可能**（デフォルト: カテゴリごと5件）

### 6. 記事保持
- **30日で自動削除**（フェッチ時 or ハートビートで古い記事をパージ）
- URLで重複排除

## プリセットRSSソース

### gaming（ゲーム）
| サイト | URL |
|--------|-----|
| EAA | `https://eaa-fps.com/feed/` |
| AUTOMATON | `https://automaton-media.com/feed/` |
| 電ファミニコゲーマー | `https://news.denfaminicogamer.jp/feed` |
| 4Gamer.net | TBD |
| Game*Spark | TBD |

### tech（テック）
| サイト | URL |
|--------|-----|
| Note | `https://note.com/rss` |
| Zenn | `https://zenn.dev/feed` |
| Qiita | TBD |
| Publickey | TBD |
| GIGAZINE | TBD |

### pc（PC）
| サイト | URL |
|--------|-----|
| 自作とゲームと趣味の日々 | TBD |
| ちもろぐ | TBD |
| PC Watch | TBD |

### vr（VR・VTuber）
| サイト | URL |
|--------|-----|
| PANORA | `https://panora.tokyo/feed` |
| MoguraVR | TBD |

### news（ニュース）
| サイト | URL |
|--------|-----|
| NHK主要ニュース | TBD |
| ITmedia | TBD |

> フィードURLの `TBD` は実装時に調査・確定する。

## config.yaml 構造

```yaml
rss:
  fetch_interval_minutes: 60
  digest_hour: 9
  article_retention_days: 30
  max_articles_per_category: 5    # WebGUIからも変更可能
  presets:
    gaming:
      label: "ゲーム"
      feeds:
        - url: "https://eaa-fps.com/feed/"
          title: "EAA"
        - url: "https://automaton-media.com/feed/"
          title: "AUTOMATON"
        # ...
    tech:
      label: "テック"
      feeds:
        - url: "https://zenn.dev/feed"
          title: "Zenn"
        # ...
    pc:
      label: "PC"
      feeds: [...]
    vr:
      label: "VR・VTuber"
      feeds: [...]
    news:
      label: "ニュース"
      feeds: [...]
```

## DB設計

```sql
CREATE TABLE rss_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,       -- gaming, tech, pc, vr, news
    is_preset INTEGER DEFAULT 0,  -- 1=プリセット, 0=ユーザー追加
    added_by TEXT,                 -- user_id（ユーザー追加の場合）
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE rss_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES rss_feeds(id),
    title TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,     -- 重複排除キー
    summary TEXT,                  -- LLM要約（未処理時はNULL）
    published_at TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE rss_user_prefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    feed_id INTEGER REFERENCES rss_feeds(id),
    category TEXT,                 -- カテゴリ単位の購読にも対応
    enabled INTEGER DEFAULT 1,
    UNIQUE(user_id, feed_id),
    UNIQUE(user_id, category)
);

CREATE TABLE rss_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    article_id INTEGER NOT NULL REFERENCES rss_articles(id),
    rating INTEGER NOT NULL,      -- +1 or -1
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, article_id)
);

-- 将来用: 嗜好タグ
CREATE TABLE rss_user_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    UNIQUE(user_id, tag)
);
```

## ユーザーコマンド（SkillRouter経由）
- **購読管理**: 「ゲームのRSS止めて」「テック系のフィード再開して」
- **フィード追加/削除**: 「このRSS追加して: https://...」「○○のフィード消して」
- **一覧表示**: 「購読中のRSS見せて」
- **即時ダイジェスト**: 「最新のおすすめ記事教えて」

## 処理フロー

```
[APScheduler: 1時間間隔]
  → fetch_all_feeds()          # RSSフェッチ（軽量、常時実行OK）
  → 新規記事を rss_articles に INSERT（URL重複スキップ）
  → 30日超の古い記事を DELETE

[APScheduler: 毎日 digest_hour 時]
  → ユーザーアクティビティ判定（非アクティブ時のみ）
  → LLMで未要約記事を要約（summary埋め）
  → フィードバック履歴を参照し、推薦スコア算出
  → カテゴリ別ダイジェスト生成
  → notify_user() で通知
```

## 未決事項
- [ ] ユーザー非アクティブ判定の実装（別途設計）
- [ ] WebGUI設定画面のUI設計
- [ ] InnerMind ContextSource としての統合（モノローグで記事に言及）
