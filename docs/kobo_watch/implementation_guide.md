# kobo_watch 実装ガイド

このドキュメントは **「コードをどう実装していくか」** を解説する。設計の理由は `architecture.md`、タスク分解は `plan.md`、確定仕様は `design.md` を参照。

---

## 全体の流れ

```
T0 事前検証(楽天API疎通) →  T1 status拡張(IP変動検知)
                          ↓
                          T2 rakuten パッケージ作成
                          ↓
                          T3 DBスキーマ・migration
                          ↓
                          T4 kobo_watch ユニット本体
                          ↓
                          T5 定期チェック・通知統合
                          ↓
                          T6 WebGUI
                          ↓
                          T7 テスト・ドキュメント
```

T1 / T2 / T3 は独立して並列着手可能。T4以降は直列依存。

---

## モジュール間のデータフロー

### フロー1: 監視対象を登録する

```
User: 「『薬屋のひとりごと』の新刊監視して、著者は日向夏」
  ↓ (Discord)
Skill Router (LLM):
  UNIT_NAME="kobo_watch"
  parsed={intent:"register", author:"日向夏", title_keyword:"薬屋のひとりごと"}
  ↓
units/kobo_watch.py :: execute() → _handle_register()
  ↓
database/kobo_watch.py :: add_target()
  ↓ INSERT INTO book_watch_targets
  ↓
units/kobo_watch.py :: _backfill_known_books()
  ↓
rakuten/books_api.py :: search_books(title="薬屋のひとりごと", author="日向夏")
  ↓ 既刊13巻が返ってくる
  ↓
database/kobo_watch.py :: record_known_book() x 13回
  ↓ INSERT INTO book_watch_known_books
  ↓
User に返信: 「監視登録したよ。既刊13巻を既知として保存済み」
```

### フロー2: 定期チェックで新刊検出

```
Heartbeat @ JST 07:00
  ↓
units/kobo_watch.py :: _check_new_releases()
  ↓
database/kobo_watch.py :: list_targets(enabled_only=True)
  ↓ [Target#1(日向夏/薬屋), Target#2(...)]
  ↓
for each target:
  ↓
  rakuten/books_api.py :: search_books(..., sort="-releaseDate", hits=30)
    ↓ 最新30件
    ↓
  for each book:
    ↓
    database/kobo_watch.py :: is_book_known(isbn)
      ↓ → True: skip
      ↓ → False: 新刊候補
      ↓
    rakuten/kobo_api.py :: is_available_in_kobo(title, author)
      ↓ → KoboMatch or None
      ↓
    database/kobo_watch.py :: record_known_book()  # 次回以降スキップするため
    database/kobo_watch.py :: record_detection()   # 通知履歴
      ↓
    if suppressed (OBS配信中):
      notified_at = NULL
      suppressed_reason = "obs_streaming"
    else:
      _notify_new_release() → Discord送信
      mark_as_notified()
```

### フロー3: IP変動検知

```
status ユニット @ 30分ごと
  ↓
_fetch_global_ip() → https://api.ipify.org
  ↓ "203.0.113.42"
  ↓
_load_known_ip() → SQLite system_state
  ↓ 前回: "198.51.100.17"
  ↓
if current != previous:
  _save_known_ip(current)
  _notify_ip_changed()
    ↓
  Discord: 「⚠️ グローバルIPが変わったよ。楽天管理画面で更新してね」
```

---

## レイヤー別の実装指針

### 楽天API層 (`src/rakuten/`)

**目的**: 楽天APIのHTTP呼び出しを隠蔽し、型安全な dataclass として上位に提供する。

**設計のポイント**:

1. **client.py に状態を集約**: 認証情報・レート制限・Refererヘッダーの管理をすべて `RakutenApiClient` に閉じ込める。`books_api.py` と `kobo_api.py` は `client.request()` を呼ぶだけ
2. **asyncio.Lock で直列化**: 複数ターゲットを並列チェックしても、楽天への実リクエストは1.5秒間隔を保つ。これを `_lock = asyncio.Lock()` で保証
3. **エラーを型で表現**: HTTPステータスに応じて `RakutenAuthError` / `RakutenRefererError` / `RakutenRateLimitError` と分類。呼び出し側は型で catch できる
4. **レスポンスをdataclassに変換**: `BookItem` / `KoboItem` などの不変dataclass で返す。原始的な dict を上位に漏らさない

**実装手順**:
1. `client.py` の `RakutenApiConfig` を `.env` から組み立てる
2. `RakutenApiClient.request()` のHTTPフロー(params組立・throttle・send・エラー判定)を実装
3. `books_api.search_books()` で紙書籍検索を叩いてみて、 実レスポンスが `BookItem` に綺麗にマップできるか確認
4. `kobo_api.search_kobo()` と `is_available_in_kobo()` で Kobo検索と類似度マッチングを実装
5. `difflib.SequenceMatcher` でタイトル類似度を計算。正規化(空白・括弧除去)を `_normalize()` で吸収

**注意点**:
- 新仕様エンドポイントURLは記事情報(個人ブログ)ベース。**実装着手時に公式ドキュメントで必ず最終確認**
- `koboGenreId` は新仕様で常に必須。`KOBO_GENRE_EBOOK_ALL = "101"` をデフォルトで指定
- `outOfStockFlag=1` で予約販売中の本も取得する(新刊はほぼこの状態)
- レスポンスの `salesDate` は `"2026年05月01日"` 形式の文字列。ISO 8601 変換は `BookItem.sales_date_iso` プロパティで行う

### 永続化層 (`src/database/kobo_watch.py`)

**目的**: SQLiteのCRUD操作のみを提供する。ビジネスロジックは持たない。

**設計のポイント**:

1. **関数スタイル**: クラスを作らず、モジュールレベルの `async def` 関数のみ。状態を持たない
2. **dataclass で返す**: `WatchTarget` / `KnownBook` / `Detection` の dataclass で返し、row を上位に漏らさない
3. **`_base.get_connection()` を使う**: プロジェクト共通のコネクション管理に従う
4. **トランザクション**: 単一SQLは `conn.execute` + `conn.commit` で十分。複数INSERT(backfill)は batch でトランザクション

**実装手順**:
1. `_migration_snippet.py` の SQL を `_base.py` の migration 群に組み込む
2. `_SCHEMA_VERSION` をインクリメント(既存値の次の数字)
3. CRUD関数を順に実装:
   - targets: `add / list / find / find_by_keyword / remove / update`
   - known_books: `record_known_book / is_book_known / list_known_books_by_target`
   - detections: `record_detection / mark_as_notified / list_pending_detections / list_detections`

**注意点**:
- `INSERT INTO book_watch_targets` は `UNIQUE(author, title_keyword)` で重複時エラー → 呼び出し側で catch して「既に登録済み」メッセージ
- `ON DELETE CASCADE` により、target 削除で関連する known_books と detections が自動削除される
- `record_known_book` は既存ISBNでは INSERT が失敗する → `try/except` で握りつぶし True/False を返す(雛形通り)
- aiosqlite は Row オブジェクトを dict-like にアクセスできるが、プロジェクトの connection factory 設定次第。`_base.get_connection()` が `row_factory = aiosqlite.Row` を設定しているか確認

### ビジネス層 (`src/units/kobo_watch.py`)

**目的**: Discord/Heartbeat からの入り口を受け取り、検索→判定→通知の一連の流れを組み立てる。

**設計のポイント**:

1. **execute() は薄く**: intent で分岐するだけ。本体は `_handle_*()` メソッドに委譲
2. **`_check_new_releases()` が定期チェックのコア**: for ターゲット → for 書籍 → 判定 → DB記録 → 通知
3. **サーキットブレーカーを使う**: `self.breaker.allow()` で判定、`record_failure/success` で状態遷移
4. **抑制理由を残す**: OBS配信中等の抑制は `suppressed_reason` に文字列で記録

**実装手順**:
1. `__init__` で config.yaml からパラメータ読み込み、`RakutenApiClient` を生成
2. `execute()` と4つの `_handle_*` 実装
3. `_check_one_target()` で1ターゲット分のチェックロジック実装
4. `_notify_new_release()` で Discord通知テキスト生成
5. `_daily_check` の `@tasks.loop(hours=1) + 時刻チェック` で定期起動
6. `_flush_pending_notifications()` で抑制済み通知の後処理

**注意点**:
- `BaseUnit` のインターフェース(config取得方法、notify/notify_error/breaker)は既存コードに合わせる。`# TODO:` コメントで明示した箇所
- `ActivityDetector` の API 名は推測。既存の他ユニット(rss等)がどう使っているかを確認して合わせる
- `_flush_pending_notifications()` 内の TODO: 検出履歴から元の本情報を引き直す処理が必要。`book_watch_known_books` から isbn で引けば title/author が取れる

### プレゼンテーション層

#### Discord (`src/skill_router.py` への追記)

Skill Router のプロンプトに kobo_watch の intent 分類ルールを追加する必要がある。

**追加する内容の例**:
```
kobo_watch: 楽天Koboの新刊通知
  - "『○○』の新刊監視して" → intent=register
  - "新刊監視してる本教えて" → intent=list
  - "『○○』の監視やめて" → intent=remove
  - "新刊チェック今すぐ" → intent=check_now
  - register時は title_keyword と author を抽出する。
    著者が抽出できなければ空欄のまま返す(ミミが聞き返す)
```

#### WebGUI (`src/web/routes/kobo_watch.py` + `static/js/pages/kobo-watch.js`)

**実装手順**:
1. `src/web/app.py` に `from src.web.routes.kobo_watch import router; app.include_router(router)` を追加
2. `src/web/static/html/kobo-watch.html` を作成し、`kobo-watch.js` を読み込む
3. メインナビに「新刊監視」リンクを追加

**Basic認証**: 既存パターンに従い、全ルートにミドルウェアで適用される前提。個別のデコレータは不要のはず(既存の `/api/reminder/*` 等の実装を確認)。

---

## テスト戦略

### ユニットテスト(pytest + pytest-asyncio)

**`tests/rakuten/test_client.py`**: 
- `httpx.MockTransport` でHTTPレスポンスを固定
- 400/403/429 で各種例外が投げられること
- `_throttle()` で1.5秒のsleepが入ること(fakeclock)

**`tests/rakuten/test_kobo_api.py`**:
- `_title_similarity()` の境界値テスト: 完全一致=1.0、全く違う=0.0
- `_normalize()` の期待動作: `"薬屋のひとりごと（14）"` → `"薬屋のひとりごと14"`
- `_author_matches()`: 表記ゆれに耐えることを確認
- `is_available_in_kobo()`: しきい値を超えた最高スコアが返ること

**`tests/database/test_kobo_watch.py`**:
- `:memory:` SQLite で高速実行
- CRUD の各関数の境界値
- UNIQUE制約で重複INSERT時にエラー
- CASCADE DELETE で関連レコードが消えること

**`tests/units/test_kobo_watch.py`**:
- `_check_one_target()` のロジック単体テスト
- 楽天APIはモック、DB は `:memory:`
- 新刊あり/なし、Kobo版あり/なし、抑制あり/なし のマトリクステスト

### 統合テスト(`tests/integration/`、CI skip)

実際の楽天APIを叩く統合テスト。`@pytest.mark.integration` でマークし、CIではスキップ、ローカルで手動実行する。

### 手動検証

1. **T0 後の疎通確認**: `curl` で楽天API新仕様が叩けること
2. **backfill 確認**: テスト用の著者で登録し、既刊が正しく backfill されること
3. **新刊検出**: 近日発売の既知作品を登録し、実際に通知が来るまで観察(1週間〜1ヶ月)
4. **IP変動検知**: 手動でルーターを再起動してIPを変え、Discord通知が来ること
5. **抑制動作**: OBS を起動して定期チェックを走らせ、通知が遅延されること

---

## config.yaml / .env の反映手順

### 1. `.env.example` に追記

```ini
RAKUTEN_APPLICATION_ID=
RAKUTEN_ACCESS_KEY=
RAKUTEN_AFFILIATE_ID=
```

### 2. `.env` (本番) に値を設定

楽天Web Service で新アプリを作成した後に取得した値を設定する。

### 3. `config.yaml.example` に追記

```yaml
units:
  kobo_watch:
    enabled: true
    check_hour_jst: 7
    new_book_window_days: 60
    notify_kobo_only: false
    referer: "https://github.com/iniwa/ai-mimich-agent"
    request_interval_ms: 1500
    api:
      books_search_url: "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404"
      kobo_search_url:  "https://openapi.rakuten.co.jp/services/api/Kobo/EbookSearch/20170426"
    kobo_match:
      title_similarity_threshold: 0.80
      author_must_match: true
  status:
    ip_watch:
      enabled: true
      check_interval_min: 30
      endpoint: "https://api.ipify.org"
```

### 4. Portainer のスタック Web editor で設定を反映

secretary-bot スタックを開き、config.yaml をボリューム経由で更新。コード変更はWebGUIの「コード更新」ボタン(git pull)で反映。

---

## デプロイ手順

### 本番デプロイの順番

1. **楽天アプリ登録完了 + `.env` 更新** (T0)
2. **ブランチで実装** (T1〜T7、雛形の `# TODO:` を埋める)
3. **GitHub Actions で Docker イメージ push** は不要(コードはボリュームマウント、イメージは Pythonランタイムのみ)
4. **Raspberry Pi で `git pull`** (WebGUIの「コード更新」ボタン)
5. **Portainer でスタック再起動** (自動再起動が効くはず)
6. **Discord で動作確認**:
   - 自分の購読シリーズを1〜2件登録
   - `新刊チェック今すぐ` で即時実行
   - WebGUI で監視対象一覧を確認

### ロールバック手順

もし問題が発生した場合:

1. `config.yaml` で `units.kobo_watch.enabled: false` に変更
2. Portainer でスタック再起動
3. 他ユニットに影響は出ない(サーキットブレーカー + 疎結合設計のため)

---

## 運用ドキュメント

### 新規シリーズを追加したいとき

Discord で: 「『〇〇』の新刊監視して、著者は〇〇」

または WebGUI: `/units/kobo-watch` の「新規追加」フォーム

### 通知が止まった / 動いていないと感じるとき

1. WebGUI で `GET /api/kobo-watch/detections` を開いて最新履歴を確認
2. Discord のエラー通知 (`notify_error` 経由) を確認
3. Pi 上のログ(`/home/iniwa/docker/secretary-bot/logs/`)で `kobo_watch` を grep
4. 楽天API側の問題なら IP変動通知が来ているはず
5. それでも原因不明なら `新刊チェック今すぐ` でデバッグ情報を出す

### 楽天APIがエラーを返すとき

| エラー | 対処 |
|---|---|
| 400 accessKey must be present | `.env` の `RAKUTEN_ACCESS_KEY` を確認 |
| 403 REFERRER_MISSING | `config.yaml` の `referer` を確認 |
| 403 IP不一致 | 楽天管理画面で Allow IP を現在のIPに更新 |
| 429 | 他のユニットが楽天APIを叩きすぎている? `request_interval_ms` を増やす |

### サーキットブレーカーが開いた場合

- 10分後に自動復帰試行
- 手動復帰: Pi再起動 or `config.yaml` で `enabled: false` → 再起動 → `enabled: true` → 再起動

---

## 既存コードと衝突しないためのチェックリスト

実装着手時に以下を確認:

- [ ] `src/units/status.py` の既存構造を確認し、IP変動検知を mix-in できるか
- [ ] `src/database/_base.py` の migration パターン(既存の `_migrate_to_v*` 関数群)を確認
- [ ] `src/skill_router.py` のプロンプト構造を確認、intent 分類ルールの追記場所
- [ ] `src/units/base_unit.py` の `BaseUnit` の `config` / `notify` / `breaker` のI/F
- [ ] heartbeat 統合の既存パターン(他ユニットが heartbeat からどう呼ばれているか)
- [ ] WebGUI の認証ミドルウェア配線(`/api/*` に自動適用されているか)
- [ ] `config.yaml` の既存 unit section 命名(snake_case で統一されているか)
- [ ] `.gitignore` に `data/`, `*.db`, `.env`, `config.yaml` が含まれていること

---

## よくある実装時の躓きポイント

### Q1: `BaseUnit.breaker` のAPIが分からない

A: 既存のユニット(例: `src/units/reminder.py` や `src/units/rss.py`)で `self.breaker` の使い方を確認。一般的には:
- `self.breaker.allow() -> bool` で呼び出し可否判定
- `self.breaker.record_success()` / `record_failure()` で状態遷移

### Q2: `@tasks.loop` の `before_loop` が必要?

A: Bot起動完了を待つための慣習。discord.py公式ドキュメント準拠。雛形では `await self.bot.wait_until_ready()` を入れている。

### Q3: aiosqlite の Row が dict アクセスできない

A: `_base.get_connection()` で `conn.row_factory = aiosqlite.Row` を設定しているか確認。設定されていなければ `row[0]` のようなインデックスアクセスに書き換える。

### Q4: LLMがintent抽出を誤る

A: Skill Router のプロンプトに具体例を3〜5個入れる(few-shot prompting)。初期版は「著者が抽出できなければ聞き返す」で堅牢化。

### Q5: タイトル類似度マッチングで誤検出

A: `config.yaml` の `kobo_match.title_similarity_threshold` を 0.80 → 0.85 に上げる。または `author_must_match: true` を必ず適用する。検出履歴を WebGUI で目視チェックし、誤マッチが多ければ閾値調整。

---

## コーディング規約(プロジェクト共通)

プロジェクトの既存規約に従うこと:

- Python 3.11 / arm64 互換
- 型ヒントは公開関数・メソッドに必須
- docstring は日本語で
- エラーは `BotError` 系で統一
- ログは `get_logger(__name__)` で構造化ログ
- `asyncio.gather()` で並列化できる箇所は並列化
- pre-commit フックはスキップしない(`--no-verify` 禁止)

---

## 参考にすべき既存ユニット

類似機能を持つユニットを真似するのが最速:

| 参考したいポイント | 参考ユニット |
|---|---|
| SQLiteのCRUD + Discord + WebGUI一覧 | `memo` / `reminder` |
| 外部APIを叩いて通知 | `weather` / `rss` |
| 定期実行 + サーキットブレーカー | `rss` |
| サーバー状態監視の拡張パターン | `status` (IP変動検知もここに統合) |

既存コードを `serena MCP` で検索して、パターンを盗むのが効率的。
