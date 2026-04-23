# kobo_watch ユニット設計書

楽天Kobo で購入済みシリーズの新刊が発売されたら通知する Unit。

## 1. 概要

### 1.1 目的
- ユーザーが登録した「著者 + タイトルキーワード」の組で楽天ブックス(紙書籍)を定期監視
- 新刊を検出したらタイトル・発売日・Kobo版有無・購入リンクをDiscordに通知
- ミミから自発的に「『○○』の新刊出てるよ」と伝えられる状態を作る

### 1.2 Step 1 確定事項

| 項目 | 決定 |
|---|---|
| 分類 | **A. Unit** (`src/units/kobo_watch.py`) + heartbeat 定期起動 |
| 実行場所 | Pi のみ |
| 永続化 | SQLite (3テーブル追加) |
| LLM利用 | 不要 (intent 分岐はキーワードベース、必要に応じ Skill Router で既に振り分け済み) |
| UI | Discord 中心(登録・一覧・削除)、WebGUI は閲覧と管理のみ |
| 抑制ルール | 通知のみ `activity.block_rules.obs_streaming` で遅延、検出は常時 |
| 頻度 | 1日1回(JST 07:00 想定、config で変更可) |
| 監視キー | 著者(必須) + タイトルキーワード(オプション) |
| Kobo版確認 | 紙を検出→Kobo APIで存在確認→両方通知してラベル分け、設定で「Kobo版のみ」に絞れる |
| IP管理 | 単一IP登録 + 変動検知 (別機能として `status` ユニットに統合) |

---

## 2. 外部API仕様

### 2.1 楽天API新仕様 (2026年2月10日〜)

**重要**: 旧APIは 2026年5月13日 に完全停止。新API前提で実装。

共通要件:
- ドメイン: `openapi.rakuten.co.jp`
- 認証: `applicationId` (UUID) + `accessKey` (pk_で始まる) の**両方必須**
- ヘッダー: `Referer` または `Origin` **必須** (登録したアプリURLを送信)
- Allow IP: 事前に楽天管理画面で登録した IP からのみリクエスト可
- レート制限: 最低 1.5 秒間隔を推奨

**⚠️ 実装時の最終確認事項:**
以下のエンドポイントURLは記事情報(個人ブログ)ベース。**実装着手時に公式ドキュメント `https://webservice.rakuten.co.jp/documentation/books-book-search` および `/kobo-ebook-search` で最新URL・パスを確認すること**。

### 2.2 使用するAPI

| 用途 | API名 | 想定エンドポイント(要確認) |
|---|---|---|
| 紙書籍新刊検索 | 楽天ブックス書籍検索API | `https://openapi.rakuten.co.jp/...` |
| Kobo版存在確認 | 楽天Kobo電子書籍検索API | `https://openapi.rakuten.co.jp/...` |

### 2.3 主要パラメータ

**楽天ブックス書籍検索API:**
- `title`: タイトルキーワード (部分一致)
- `author`: 著者名 (部分一致)
- `sort`: `-releaseDate` (新刊順)
- `hits`: 取得件数 (最大30)
- `booksGenreId`: 絞り込み(漫画・ラノベなら `001001`)
- `outOfStockFlag`: 1(在庫なしも含める、予約販売対応)

**楽天Kobo電子書籍検索API:**
- `title` + `author`: 存在確認用
- `koboGenreId`: 必須(101=電子書籍全般)

### 2.4 ISBN マッチング戦略

Kobo版の存在確認は ISBN では不可能(電子書籍は独自IDで ISBN と別)。紙の ISBN から直接 Kobo を引けないため、**タイトル + 著者の組み合わせ**で Kobo 検索してマッチを取る。完全一致は期待できないので「タイトルが80%以上一致 + 著者一致」などの簡易スコアリングで判定。

---

## 3. ファイルツリー

新規/変更ファイルのみ記載。

```
secretary-bot/
├── src/
│   ├── units/
│   │   └── kobo_watch.py                       # [新規] メインユニット
│   ├── database/
│   │   ├── _base.py                            # [変更] _SCHEMA_VERSION++ と migration 追加
│   │   └── kobo_watch.py                       # [新規] DB アクセサ
│   ├── units/__init__.py                       # [変更] _UNIT_MODULES に 'kobo_watch' 追加
│   ├── rakuten/
│   │   ├── __init__.py                         # [新規]
│   │   ├── client.py                           # [新規] 楽天API共通HTTPクライアント(新仕様対応)
│   │   ├── books_api.py                        # [新規] 楽天ブックス書籍検索API ラッパー
│   │   └── kobo_api.py                         # [新規] 楽天Kobo電子書籍検索API ラッパー
│   ├── web/
│   │   ├── routes/kobo_watch.py                # [新規] WebGUI API
│   │   └── static/js/pages/kobo-watch.js       # [新規] 管理ページ JS
│   └── units/status.py                         # [変更] IP変動検知を統合
├── config.yaml.example                          # [変更] units.kobo_watch 追加
├── .env.example                                 # [変更] RAKUTEN_* 追加
└── docs/
    └── kobo_watch/
        ├── design.md                           # [新規] 本ファイル
        ├── plan.md                             # [新規] 実装タスク分解
        └── api_verification.md                 # [新規] 楽天API新仕様の検証メモ
```

---

## 4. 各ファイルの責務

### 4.1 `src/rakuten/client.py`
楽天API新仕様用の共通HTTPクライアント。

- `applicationId + accessKey` をクエリに付与
- `Referer` ヘッダーを自動付与
- レート制限(1.5秒間隔) を内部スリープで保証
- 共通エラーハンドリング(400/403/429/500)
- `.env` から認証情報を読み込み

### 4.2 `src/rakuten/books_api.py`
楽天ブックス書籍検索API のラッパー。

- `search_books(title=None, author=None, sort="-releaseDate", ...) -> list[BookItem]`
- 返り値は dataclass `BookItem`

### 4.3 `src/rakuten/kobo_api.py`
楽天Kobo電子書籍検索API のラッパー。

- `search_kobo(title, author) -> list[KoboItem]`
- `is_available_in_kobo(title: str, author: str) -> KoboMatch | None`
  - タイトル類似度 + 著者一致でマッチ判定、最高スコア1件を返す

### 4.4 `src/database/kobo_watch.py`
DBアクセサ (aiosqlite, WAL)。

```python
async def add_target(author: str, title_keyword: str | None, user_id: str) -> int
async def list_targets(enabled_only: bool = True) -> list[WatchTarget]
async def remove_target(target_id: int) -> bool
async def update_target(target_id: int, ...) -> bool

async def record_known_book(isbn: str, target_id: int, ...) -> bool
async def is_book_known(isbn: str) -> bool

async def record_detection(isbn: str, kobo_available: bool, notified_at: datetime | None) -> int
async def list_detections(limit: int = 50) -> list[Detection]
```

### 4.5 `src/units/kobo_watch.py`
メインユニット。

- `UNIT_NAME = "kobo_watch"`
- `UNIT_DESCRIPTION = "楽天Koboで購入した本のシリーズの新刊監視と通知"`
- `execute(ctx, parsed)`: intent で処理分岐
  - `intent == "register"`: 監視対象を登録
  - `intent == "list"`: 登録一覧を返す
  - `intent == "remove"`: 監視対象を削除
  - `intent == "check_now"`: 即時チェックを走らせる(デバッグ用)
- `@tasks.loop(hours=24)` or Heartbeat連携 で定期チェック
- 新刊検出ロジック:
  1. `list_targets()` でアクティブな監視対象を取得
  2. 各ターゲットについて `books_api.search_books(title=kw, author=ao, sort="-releaseDate", hits=30)` で最新30件取得
  3. 各結果の ISBN を `is_book_known()` で照合
  4. 未知のISBN かつ 発売日が未来 or 過去N日以内 のものを「新刊候補」
  5. 候補それぞれに対して `kobo_api.is_available_in_kobo()` で Kobo版確認
  6. `record_known_book()` で ISBN を登録、`record_detection()` で検出履歴保存
  7. `notify_kobo_only` 設定次第で Discord 通知

### 4.6 `src/units/status.py` (拡張)
既存の status ユニットに「IP変動検知」機能を追加。

- 新関数 `async def check_global_ip() -> str`: `https://api.ipify.org` を叩く
- 新関数 `async def _ip_watch_task()`: 前回のIPと比較、変動したら Discord通知
- heartbeat 統合 (15分〜1時間に1回でOK)
- 通知テンプレート: 「グローバルIPが変わったよ(XXX.XXX.XXX.XXX → YYY.YYY.YYY.YYY)。楽天API用のIP登録を更新してね → [楽天管理画面](https://webservice.rakuten.co.jp/app/list)」

### 4.7 `src/web/routes/kobo_watch.py`
WebGUI API (最小限)。

- `GET  /api/kobo-watch/targets` — 監視対象一覧
- `POST /api/kobo-watch/targets` — 追加 (body: author, title_keyword)
- `DELETE /api/kobo-watch/targets/{id}` — 削除
- `PATCH /api/kobo-watch/targets/{id}` — enable/disable/編集
- `GET  /api/kobo-watch/detections?limit=50` — 検出履歴
- `POST /api/kobo-watch/check-now` — 即時チェック

### 4.8 `src/web/static/js/pages/kobo-watch.js`
管理ページの JS。一覧表示 + 追加フォーム + 削除ボタン + 即時チェックボタン。

---

## 5. SQLite スキーマ

`src/database/_base.py` の `_SCHEMA_VERSION` をインクリメントし、migration を追加。

```sql
-- 監視対象
CREATE TABLE book_watch_targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    author        TEXT    NOT NULL,
    title_keyword TEXT,                              -- NULL なら著者全書籍
    user_id       TEXT    NOT NULL,                  -- 登録したDiscordユーザー
    enabled       INTEGER NOT NULL DEFAULT 1,
    notify_kobo_only INTEGER NOT NULL DEFAULT 0,     -- ユーザー個別設定
    created_at    TEXT    NOT NULL,
    UNIQUE(author, title_keyword)
);

-- 既知のISBN履歴(新刊判定用)
CREATE TABLE book_watch_known_books (
    isbn          TEXT    PRIMARY KEY,
    target_id     INTEGER NOT NULL,
    title         TEXT    NOT NULL,
    author        TEXT    NOT NULL,
    publisher     TEXT,
    sales_date    TEXT,                              -- ISO8601 'YYYY-MM-DD'
    item_url      TEXT,                              -- 楽天ブックスの商品URL
    image_url     TEXT,
    first_seen_at TEXT    NOT NULL,
    FOREIGN KEY(target_id) REFERENCES book_watch_targets(id) ON DELETE CASCADE
);
CREATE INDEX idx_known_books_target ON book_watch_known_books(target_id);

-- 検出・通知履歴
CREATE TABLE book_watch_detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn            TEXT    NOT NULL,
    target_id       INTEGER NOT NULL,
    kobo_available  INTEGER NOT NULL DEFAULT 0,
    kobo_url        TEXT,
    notified_at     TEXT,                            -- NULL なら未通知
    suppressed_reason TEXT,                          -- 'obs_streaming' 等
    created_at      TEXT    NOT NULL,
    FOREIGN KEY(target_id) REFERENCES book_watch_targets(id) ON DELETE CASCADE
);
CREATE INDEX idx_detections_isbn ON book_watch_detections(isbn);
CREATE INDEX idx_detections_notified ON book_watch_detections(notified_at);
```

### 5.1 初回登録時の既知ISBN埋め込み

新規ターゲット登録時に、**既に出ている巻を「既知」として記録する**処理を挟む。これをやらないと登録直後に既刊全部が新刊扱いで通知されてしまう。

```python
# add_target() 直後に呼ぶ処理(疑似コード)
async def backfill_known_books(target_id, author, title_keyword):
    books = books_api.search_books(title=title_keyword, author=author, hits=30)
    for b in books:
        await record_known_book(b.isbn, target_id, ...)
```

---

## 6. 設定

### 6.1 `.env.example` 追記

```ini
# 楽天Web Service (新仕様, 2026年2月10日〜)
RAKUTEN_APPLICATION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RAKUTEN_ACCESS_KEY=pk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
RAKUTEN_AFFILIATE_ID=                                      # 任意、空欄可
```

### 6.2 `config.yaml.example` 追記

```yaml
units:
  kobo_watch:
    enabled: true
    check_hour_jst: 7                # 毎日JST 7:00に定期チェック
    check_interval_hours: 24         # 定期チェック間隔
    notify_kobo_only: false          # true にすると Kobo版ありのみ通知
    new_book_window_days: 60         # 発売日がN日以内のみ「新刊」扱い
    referer: "https://github.com/iniwa/ai-mimich-agent"  # 楽天APIヘッダー
    request_interval_ms: 1500        # 楽天推奨(1.5秒以上)
    api:
      books_search_url: "https://openapi.rakuten.co.jp/..."   # 実装時最終確認
      kobo_search_url:  "https://openapi.rakuten.co.jp/..."   # 実装時最終確認
    kobo_match:
      title_similarity_threshold: 0.80   # タイトル類似度のしきい値
      author_must_match: true

status:
  ip_watch:
    enabled: true
    check_interval_min: 30           # 30分に1回IP確認
    endpoint: "https://api.ipify.org"
```

---

## 7. Discord コマンド設計

Skill Router (LLM) が `UNIT_NAME=kobo_watch` に振り分けた後、`parsed` の `intent` で分岐。

| 自然言語例 | intent | parsed パラメータ |
|---|---|---|
| 「『薬屋のひとりごと』の新刊監視して、著者は日向夏」 | register | author="日向夏", title_keyword="薬屋のひとりごと" |
| 「新刊監視してる本を教えて」 | list | - |
| 「『薬屋のひとりごと』の監視やめて」 | remove | title_keyword="薬屋のひとりごと" |
| 「新刊チェック今すぐ走らせて」 | check_now | - |

Skill Router プロンプトに intent 抽出のルールを追記する必要あり(`src/skill_router.py` のプロンプトテンプレートに kobo_watch 用の intent 分類を含める)。

---

## 8. 通知フォーマット

### 8.1 新刊検出時 (Discord)

```
📚 新刊が出てるよ！

**薬屋のひとりごと 14巻**
著者: 日向夏
発売日: 2026年5月1日
出版社: 小学館

📕 紙のみ(Kobo版はまだ出てないみたい)
🔗 楽天ブックス: https://books.rakuten.co.jp/rb/XXXXXX/

---

(Kobo版がある場合)
📱 Kobo版配信中
🔗 楽天Kobo: https://search.kobobooks.rakuten.co.jp/search?q=XXX
```

### 8.2 登録完了時

```
監視登録したよ。
著者: 日向夏 / タイトル: 薬屋のひとりごと
既刊13巻を「既知」として保存済み。新刊が出たら通知するね。
```

### 8.3 IP変動検知時

```
⚠️ グローバルIPが変わったよ
前回: 203.0.113.42
今回: 198.51.100.17

楽天API(kobo_watch)が動かなくなる前に、楽天管理画面でIPを更新してね。
https://webservice.rakuten.co.jp/app/list
```

---

## 9. エラーハンドリング

### 9.1 サーキットブレーカー (BaseUnit.breaker)
- 楽天API連続失敗(3回) で一時停止 → 10分後に自動復帰を試行
- 通知は `notify_error()` で人間にも知らせる

### 9.2 想定エラーと対処

| エラー | 原因 | 対処 |
|---|---|---|
| HTTP 400 `accessKey must be present` | `.env` 設定漏れ | 起動時バリデーション |
| HTTP 403 `REFERRER_MISSING` | Refererヘッダー欠落 | client.py で自動付与 |
| HTTP 403 IP不一致 | Allow IP不一致(IP変動) | Discordで即時通知、status ユニットの IP変動検知と連携 |
| HTTP 429 | レート超過 | 1.5秒間隔を守る、指数バックオフ |

---

## 10. 既存コードとの衝突・整合性チェック

実装着手時に確認する項目:

- [ ] `src/units/status.py` の既存構造(IP変動検知を追加可能か)
- [ ] `src/database/_base.py` の migration パターン(`_SCHEMA_VERSION` の増やし方)
- [ ] `src/skill_router.py` のプロンプト拡張方法(intent 分類ルール追加)
- [ ] `src/units/base_unit.py` の `notify()` / `notify_error()` / `breaker` の使い方
- [ ] heartbeat からユニット処理を呼ぶ既存パターン(`rss` や `reminder` が参考になるはず)
- [ ] WebGUI の認証経路(Basic認証が全APIに自動適用されているか)
- [ ] `config.yaml` の他ユニット section との命名一貫性

---

## 11. 要決定事項(実装前に要確認)

### 11.1 楽天API新仕様の確定URL
実装着手時に以下を公式ドキュメントで最終確認:
- 楽天ブックス書籍検索API の新エンドポイントパス
- 楽天Kobo電子書籍検索API の新エンドポイントパス
- `https://webservice.rakuten.co.jp/documentation/books-book-search`
- `https://webservice.rakuten.co.jp/documentation/kobo-ebook-search`

### 11.2 NDLサーチ併用の可能性
楽天API単独で十分だが、楽天ブックスに載らない出版社(同人・電子オリジナル)も拾うなら NDL (dpid=jpro) を補助ソースとして追加する手もある。**初期版では楽天単独で行い、取りこぼしが気になれば後から追加**。

### 11.3 Skill Router の intent 抽出精度
「『薬屋のひとりごと』の新刊監視して」のような曖昧な発話から `author` を自動抽出するのは難しい。**初期版は登録時に著者を必ず聞き返す**UX(例: 「著者を教えて?」) で逃げる。将来的に LLM 呼び出しで自動抽出してもよい。

### 11.4 IP変動検知: status ユニット統合 vs 新ユニット
既存 `status` ユニットのコード量と責務境界を見て決定。「システム監視」的な責務なら status に統合、独立した責務なら新ユニット `ip_monitor` として切り出し。**推奨は status 統合**(新ユニットを増やしすぎない)。
