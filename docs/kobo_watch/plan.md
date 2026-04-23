# kobo_watch 実装計画 (plan.md)

`design.md` の設計を実装に落とすタスク分解。1タスク = 1PR が理想。

## タスク依存関係

```
T1 (IP変動検知・status拡張)   ─── 独立
T2 (楽天APIクライアント)      ─── T4 の前提
T3 (DBスキーマ・migration)    ─── T4 の前提
    ↓
T4 (kobo_watch ユニット本体)  ─── T5, T6 の前提
    ↓
T5 (heartbeat 定期チェック統合) ─── T7 の前提
T6 (WebGUI 管理ページ)
    ↓
T7 (テスト・ドキュメント整備)
```

並列化可能: T1 / T2 / T3 は同時着手可。

---

## T0: 事前調査・検証 (Size: S)

実装着手前に必ず実施する検証。

- [ ] `ssh iniwapi` で Raspberry Pi 上から `curl https://ndlsearch.ndl.go.jp/api/opensearch?title=test` が動くことを確認(楽天API移行中の代替案として手元に残す)
- [ ] 楽天Web Serviceで新アプリを登録
  - アプリURL: `https://github.com/iniwa/ai-mimich-agent`
  - Allow IP: 現在のグローバルIP(単一)
  - `applicationId` (UUID) と `accessKey` (pk_...) を取得
- [ ] 楽天ブックス書籍検索API を手元で1回だけ curl で叩いて200が返ることを確認
  - 必ず `Referer` ヘッダー付きで
  - 公式ドキュメントで新仕様エンドポイントURLを確定させる
- [ ] 楽天Kobo電子書籍検索API も同様に疎通確認
- [ ] `docs/kobo_watch/api_verification.md` に検証結果とエンドポイントURLを記録

**成果物**: `docs/kobo_watch/api_verification.md` (検証済みURLと curl コマンド)

---

## T1: status ユニット拡張 (IP変動検知) (Size: S)

- [ ] `src/units/status.py` に `check_global_ip()` 関数追加
  - `https://api.ipify.org` を叩いてプレーンテキストでIPを取得
  - タイムアウト5秒、失敗時は None を返す
- [ ] SQLite に `system_state` テーブルが無ければ追加 (`key TEXT PK, value TEXT, updated_at TEXT`)
- [ ] heartbeat 連携 (30分間隔のチェック)
- [ ] 変動検知時に `notify()` で Discord に通知
  - 通知文は設計書 §8.3 のテンプレート
- [ ] `config.yaml` に `status.ip_watch` セクション追加

**成果物**: IP変動が Discord に通知される

---

## T2: 楽天API共通クライアント (Size: M)

- [ ] `src/rakuten/__init__.py` 作成
- [ ] `src/rakuten/client.py`
  - `RakutenApiClient(app_id, access_key, referer, rate_limit_ms=1500)`
  - async `request(endpoint: str, params: dict) -> dict`
    - クエリに `applicationId` と `accessKey` を自動付与
    - `Referer` ヘッダー自動付与
    - 前回リクエストからの経過時間を計算し、不足分 sleep
    - HTTP 400/403/429 でそれぞれ専用例外を投げる
  - `.env` から認証情報読み込み(`bot.py` の起動時に環境変数検査を追加)
- [ ] `src/rakuten/books_api.py`
  - `@dataclass BookItem(isbn, title, author, publisher, sales_date, item_url, image_url)`
  - `async def search_books(client, title=None, author=None, sort="-releaseDate", hits=30, genre_id=None) -> list[BookItem]`
- [ ] `src/rakuten/kobo_api.py`
  - `@dataclass KoboItem(title, author, item_url, sales_date)`
  - `async def search_kobo(client, title, author) -> list[KoboItem]`
  - `async def is_available_in_kobo(client, title, author, threshold=0.80) -> KoboItem | None`
    - タイトル類似度は `difflib.SequenceMatcher.ratio()` で計算
- [ ] ユニットテスト (httpx MockTransport でAPIレスポンスをモック)

**成果物**: `src/rakuten/` 配下の動作確認済みクライアント

---

## T3: DBスキーマ・migration (Size: S)

- [ ] `src/database/_base.py` の `_SCHEMA_VERSION` をインクリメント
- [ ] migration 関数追加 (既存パターンに合わせる)
  - 設計書 §5 の3テーブルを CREATE
  - インデックス作成
- [ ] `src/database/kobo_watch.py` 作成
  - `add_target / list_targets / remove_target / update_target`
  - `record_known_book / is_book_known / list_known_books_by_target`
  - `record_detection / list_detections`
- [ ] ユニットテスト (in-memory SQLite)

**成果物**: migration が本番DBで正常に走る + CRUD が動作する

---

## T4: kobo_watch ユニット本体 (Size: M)

T2, T3 完了後に着手。

- [ ] `src/units/kobo_watch.py` 作成
  - `UNIT_NAME = "kobo_watch"`
  - `UNIT_DESCRIPTION = "楽天Koboで購入した本のシリーズの新刊監視と通知"`
  - `execute(ctx, parsed)` で intent 分岐
    - `register`: 監視対象追加 → 既刊を「既知」として backfill
    - `list`: 登録一覧を見やすいDiscord embed で返す
    - `remove`: 著者またはタイトルキーワードで削除
    - `check_now`: 即時チェック発火(手動デバッグ用)
- [ ] `src/units/__init__.py::_UNIT_MODULES` に `'kobo_watch'` 追加
- [ ] `config.yaml` の `units.kobo_watch.enabled: true` で自動ロードされることを確認
- [ ] `src/skill_router.py` のプロンプトに kobo_watch の intent 分類ルールを追記
  - 具体例を入れる(「『薬屋のひとりごと』の新刊監視して」 → `register`)
- [ ] 登録完了時の Discord メッセージ(設計書 §8.2)

**成果物**: Discord から登録・一覧・削除ができる

---

## T5: 新刊チェック・通知ロジック (Size: M)

- [ ] `kobo_watch.py` に `async def _check_new_releases()` を実装
  - 設計書 §4.5 のロジックに従う
  - 検索 → ISBN照合 → 新刊判定 → Kobo確認 → DB記録 → 通知
- [ ] heartbeat に統合
  - 既存の heartbeat 構造を確認して、定刻実行パターンに合わせる
  - `check_hour_jst = 7` で毎日1回起動
- [ ] `ActivityDetector` 連携
  - `obs_streaming` 中は通知を `suppressed_reason` 付きで保存し、解除後に配信
- [ ] サーキットブレーカー適用
  - 連続失敗 3回で一時停止、10分後に自動復帰試行
- [ ] 通知フォーマット(設計書 §8.1) 実装

**成果物**: 定刻で新刊チェックが走り、Discord に通知が来る

---

## T6: WebGUI 管理ページ (Size: S)

T4 完了後。

- [ ] `src/web/routes/kobo_watch.py` 作成
  - 設計書 §4.7 のエンドポイント
  - Basic認証下で動作(既存パターン踏襲)
- [ ] `src/web/static/js/pages/kobo-watch.js` 作成
  - 一覧表示・追加フォーム・削除ボタン・即時チェックボタン
  - PCファーストのレスポンシブHTML
- [ ] メインナビに「新刊監視」リンク追加
- [ ] 静的ファイルの md5 キャッシュバスターが自動適用されることを確認

**成果物**: WebGUI から管理できる

---

## T7: テスト・ドキュメント (Size: S)

- [ ] ユニットテスト
  - `tests/units/test_kobo_watch.py`
  - `tests/rakuten/test_books_api.py` (モック使用)
  - `tests/rakuten/test_kobo_api.py` (類似度判定含む)
  - `tests/database/test_kobo_watch_db.py`
- [ ] 統合テスト(手動)
  - 自分の好きなシリーズを実際に登録してみる
  - 7日間放置して通知が来るか観察
- [ ] `docs/kobo_watch/README.md` — エンドユーザー向け使い方ガイド
- [ ] `docs/CHANGELOG.md` に追記
- [ ] `CLAUDE.md` の Unit一覧セクション(あれば)に `kobo_watch` 追記

**成果物**: テスト通過 + ドキュメント整備

---

## 見積もり合計

| タスク | Size | 想定時間 |
|---|---|---|
| T0 | S | 30分 |
| T1 | S | 1〜2h |
| T2 | M | 3〜4h |
| T3 | S | 1〜2h |
| T4 | M | 3〜4h |
| T5 | M | 3〜4h |
| T6 | S | 2〜3h |
| T7 | S | 2〜3h |
| **合計** | | **15〜22h** |

---

## テスト方針

### ユニットテスト (pytest + pytest-asyncio)
- 楽天API は httpx の MockTransport で固定レスポンス
- 類似度判定 (`is_available_in_kobo`) は具体例で境界値テスト
- DB は in-memory SQLite (`:memory:`) で高速実行

### 手動統合テスト
- 実際の楽天APIを叩くのは `tests/integration/` に隔離
- CI では skip、ローカルでは `pytest -m integration` で実行

### 運用検証
- 初日: 好きなシリーズ2-3件を登録、即時チェック(`check_now`)で通知動作確認
- 1週間: 定刻チェックが正常動作することを heartbeat ログで確認
- 1ヶ月: 実際に新刊が出た時に正しく通知されるか確認

---

## リスクと対応

| リスク | 対応 |
|---|---|
| 楽天API新仕様 URL が記事情報と違う | T0 で公式ドキュメント確認、違えば `config.yaml` で吸収 |
| Allow IP が頻繁に変わる | T1 の IP変動検知で早期発見、月1〜2回の手動更新を受容 |
| タイトル類似度判定の誤検出 | しきい値を config 化、検出履歴を WebGUI で確認できるようにする |
| ゲーム配信中に通知スパム | T5 で ActivityDetector 連携、suppressed_reason で抑制記録 |
| 検索件数の取りこぼし | `hits=30` で足りないケースは page 処理追加(将来拡張) |
