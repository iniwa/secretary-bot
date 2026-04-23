# kobo_watch アーキテクチャと設計思想

このドキュメントは **「なぜこの設計になったのか」** を ADR (Architecture Decision Record) 形式で記録する。コードの「どう実装するか」は `implementation_guide.md` 、確定した仕様は `design.md` を参照。

---

## 設計原則

kobo_watch の設計は、プロジェクト全体の設計思想に従う:

1. **機能=ユニット原則**: 新機能は `src/units/<name>.py` で1ファイル追加するだけで有効化する。Skill Routerが自然言語から自動的にルーティングする
2. **疎結合**: 外部API呼び出しを `src/rakuten/` として独立パッケージに分離し、他ユニットからも再利用可能にする
3. **Fail-safe**: 外部API失敗で kobo_watch 全体を止めず、サーキットブレーカーで局所化。他ユニットへの影響ゼロ
4. **Pi負荷最小化**: 定期処理は1日1回。heavy処理はWindows Agentに委託する基盤があるが、本ユニットはAPI呼び出しのみなのでPi内で完結
5. **透明性**: 全検出・通知・抑制をSQLiteに記録し、WebGUIから閲覧できる

---

## レイヤー構造

```
┌─────────────────────────────────────────────────────────┐
│ [Discord]  [WebGUI]                                     │  Presentation
│     ↓         ↓                                          │
├─────────────────────────────────────────────────────────┤
│ Skill Router → execute() / intent分岐                    │  Orchestration
│ Heartbeat → _daily_check()                              │
├─────────────────────────────────────────────────────────┤
│ src/units/kobo_watch.py                                 │  Business Logic
│   - register / list / remove / check_now                │
│   - _check_new_releases() 定期チェック                   │
│   - _notify_new_release() 通知生成                       │
├─────────────────────────────────────────────────────────┤
│ src/rakuten/ (パッケージ)                                │  External API
│   - client.py  (認証・レート制限・エラー変換)            │
│   - books_api.py  (紙書籍検索)                           │
│   - kobo_api.py  (Kobo検索+類似度マッチング)             │
├─────────────────────────────────────────────────────────┤
│ src/database/kobo_watch.py                              │  Persistence
│   - book_watch_targets                                  │
│   - book_watch_known_books                              │
│   - book_watch_detections                               │
└─────────────────────────────────────────────────────────┘
```

### 横串の共通機能(他ユニットから借りる)

- `BaseUnit.notify()` / `notify_error()`: Discord送信
- `BaseUnit.breaker`: サーキットブレーカー
- `ActivityDetector`: OBS配信・ゲーム中の判定
- `src/logger.get_logger()`: 構造化ログ(trace_id付き)

### 依存関係のルール

- `src/units/kobo_watch.py` → `src/rakuten/*` と `src/database/kobo_watch.py` のみ参照
- `src/rakuten/*` → プロジェクトの errors / logger のみ参照(ユニットや DB を参照しない)
- `src/database/kobo_watch.py` → `src/database/_base` のみ参照

これにより `src/rakuten/` は独立テスト可能で、将来別ユニットからも再利用できる。

---

## ADR (Architecture Decision Records)

### ADR-001: 情報源として楽天APIを採用

**状況**: 新刊検出に使える情報源は複数ある。楽天API、NDLサーチ(jpro)、スクレイピング、openBD、Google Books API など。

**検討した選択肢**:

| 選択肢 | 登録 | Kobo版確認 | 速報性 | 耐久性 |
|---|---|---|---|---|
| 楽天API新仕様 | 要(IP登録要) | ◎ | ◎ | ISP帯変動リスク |
| NDLサーチ(jpro) + URL生成 | 不要 | ✗ | ○(数日遅れ) | ◎ |
| 楽天ブックス・楽天Koboのスクレイピング | 不要 | △ | ◎ | HTML変更で壊れる |
| openBD + 出版社RSS | 不要 | ✗ | △ | ◎ |

**決定**: **楽天API新仕様を採用**する。

**理由**:
- Kobo版の存在確認が目的達成の核心。NDLやopenBDでは確認不可
- ユーザー(いにわ)自身の強い希望がAPI利用
- IP登録の手間は「単一IP+変動検知」の仕組みで許容可能な運用コストに落とし込める

**トレードオフ**:
- IP変動でサービス停止するリスク → 別機能(IP変動検知)で早期発見して手動更新を促す
- accessKey管理が必要 → `.env`で既存の機密情報と同じ扱い
- 旧APIは2026年5月13日停止なので新仕様前提で実装する必要がある

**代替案が浮上する条件**:
- 楽天が再びAPI仕様を厳格化し、個人利用が実質不可能になった場合 → NDLサーチ + スクレイピングへ移行
- 設計では `src/rakuten/` を切り離しているので、情報源の差し替えは比較的容易

---

### ADR-002: 監視キーは「著者+タイトルキーワード」

**状況**: 新刊検出のクエリ設計。何をキーに楽天APIを叩くか。

**検討した選択肢**:

| 選択肢 | 精度 | 登録の手間 | 誤検出 |
|---|---|---|---|
| 著者のみ | 低 | 最小 | 同著者の別シリーズを拾う |
| タイトルのみ | 中 | 小 | 同名作品(著者違い)を拾う |
| 著者+タイトル(AND) | **高** | 中 | 少ない |

**決定**: **著者(必須) + タイトルキーワード(任意)** の組を監視対象の単位とする。

**理由**:
- 楽天ブックス書籍検索APIは `author` と `title` を両方パラメータに指定可能(AND検索)
- 著者を必須にすると、タイトルキーワード未指定で「同著者の新刊全部」を監視する柔軟性が残る
- タイトルキーワードを指定すればシリーズに絞り込める

**ユニーク制約**: `UNIQUE(author, title_keyword)` で同一組み合わせの重複登録を防ぐ。

**初期版の割り切り**: Skill Router (LLM) から著者を自動抽出するのは精度が不安定。登録時に著者が抽出できなければミミが「著者を教えてほしいな」と聞き返すUXで対応する。将来的に書籍タイトル→著者の自動引きを実装する余地あり。

---

### ADR-003: Kobo版確認は「両方通知+ラベル分け+設定で絞り込める」

**状況**: 紙が先行発売、Kobo版が遅れて出るケースが多い。紙発売時点で通知するか、Kobo版配信時点で通知するか。

**検討した選択肢**:
- **B=1 通知しない**: そもそも Kobo版確認を省略。紙の新刊だけを通知
- **B=2 Kobo版ありのみ通知**: Kobo版が確認できたものだけ通知、紙のみの状態では通知しない
- **B=3 両方通知してラベル分け**: 紙発売時と Kobo版配信時の両方で通知、絵文字でラベルを分ける
- **B=3 + 設定トグル**: デフォルトB=3、設定でB=2相当に切り替え可能 ← **採用**

**決定**: **B=3 + 設定トグル**。`notify_kobo_only` フラグを監視対象ごとに持たせる。

**理由**:
- 実装コストはB=2とB=3でほぼ同じ(Kobo検索処理は共通、違うのは通知フィルタのみ)
- ユーザー行動の違いに対応できる:
  - 紙も買う派 → B=3 で両方通知
  - Kobo専門 → B=2 で Kobo版のみ
- 運用開始後の「やっぱり通知多すぎた」を設定変更だけで解決できる

**実装上の工夫**:
- 同じISBNを紙・Kobo版の両方で記録するのではなく、紙のISBNをプライマリキーとする
- 1回目の通知(紙のみ)と2回目の通知(Kobo配信)の両方を `book_watch_detections` に記録
- suppressed_reason カラムで「通知しない理由」(`kobo_only_filter`等)を残す

---

### ADR-004: IP管理は「単一IP + 変動検知」

**状況**: 楽天API新仕様は Allow IP が必須。いにわさん宅の回線は固定IPではない。

**検討した選択肢**:
- **案α: 単一IP登録 + 変動検知**: 現在のグローバルIPを1つ登録し、変動時にDiscord通知
- **案β: /16 CIDR広め登録**: ISPのIP帯域を65536アドレス単位で許可
- **案γ: 変動検知+自動再登録**: 楽天に自動再登録APIがないため却下

**決定**: **案α 単一IP + 変動検知**を採用。IP変動検知は `status` ユニットに統合する。

**理由**:
- 単一IP登録が最もセキュリティ的に厳密(accessKey漏洩時の影響を最小化)
- いにわさん宅のIPv4は ISP都合で変動するものの、家庭用途では数日〜数週間は固定されるのが一般的
- 変動時に即座にDiscord通知が来れば、1分程度の手動更新で復旧できる

**実装位置の判断**:
- 独立ユニット `ip_monitor` にするか既存 `status` ユニットに統合するかの選択肢
- **`status` ユニットに統合**を採用: システム監視は status の責務に近く、ユニットを増やしすぎない方針

**失敗時のフォールバック**:
- 楽天APIが連続失敗 → サーキットブレーカーが開く → kobo_watch が自動停止
- Discord に「楽天API連続失敗、IP変動かも?」とエラー通知
- 問題解消後、自動復帰を試行

---

### ADR-005: UI中心はDiscord、WebGUIは閲覧・補助

**状況**: 登録・一覧・削除のUIをDiscord中心とWebGUI中心のどちらに寄せるか。

**決定**: **Discord中心**。WebGUIは「一覧確認・削除・即時チェック」の補助UI。

**理由**:
- Skill Router が LLM で自然言語を振り分ける基盤が既にある
- 外出先でスマホから登録できる方が体験が良い
- WebGUIは夜間の自宅PCで落ち着いて整理する時に使う
- 実装コスト: Discord側だけで全CRUDできるので、WebGUIを最小化できる

**Discord コマンドのUX例**:
- 「『薬屋のひとりごと』の新刊監視して、著者は日向夏」 → register
- 「新刊監視してる本を教えて」 → list
- 「『薬屋のひとりごと』の監視やめて」 → remove
- 「新刊チェック今すぐ走らせて」 → check_now(デバッグ用)

**Skill Router 側の工夫**:
- プロンプトにintent分類ルールを追加(`kobo_watch` 以下の4つのintent)
- 曖昧な発話は`intent=register` に倒しつつ、著者が抽出できなければミミが聞き返す

---

### ADR-006: 通知抑制はActivityDetector連携+遅延配信

**状況**: ゲーム配信中やOBS起動中に通知が飛ぶと配信事故になる。

**決定**: **ActivityDetector(既存機能)と連携し、OBS配信中は通知を SQLite に保存して後で配信**する。

**実装方針**:
- `_check_new_releases()` 内で `_suppressed_reason()` を判定
- 抑制時は `book_watch_detections.suppressed_reason = 'obs_streaming'` で保存、`notified_at = NULL`
- 次回定期チェック時に `_flush_pending_notifications()` で未通知分を配信
- これにより「配信終了後に溜まっていた通知が順に届く」体験になる

**kobo_only_filter との区別**:
- `suppressed_reason = 'obs_streaming'`: 一時的抑制、後で通知する
- `suppressed_reason = 'kobo_only_filter'`: 永続抑制、そもそも通知しない
- これを区別するためのカラム設計

---

### ADR-007: 登録直後の誤通知を backfill で防止

**状況**: 新規登録時に既刊(1巻〜N巻)が全部「未知のISBN」扱いになり、一斉通知される事故を防ぐ必要がある。

**決定**: **登録直後に楽天APIを叩いて既刊を取得し、`book_watch_known_books` テーブルに一括記録**する。この処理を `backfill` と呼ぶ。

**実装**:
```python
async def backfill_known_books(target_id, author, title_keyword):
    books = await search_books(client, title=title_keyword, author=author, hits=30)
    for b in books:
        await record_known_book(b.isbn, target_id, ...)
```

**制約と将来拡張**:
- `hits=30` なので、長期連載作品の全巻を backfill するには不足する可能性あり
- ただし「長期連載の14巻目が新刊として誤通知される」のはむしろ実害が少ない(ユーザーは14巻が出たと認識)
- page 処理で完全backfillしたい場合は将来拡張

---

### ADR-008: 責務の切り分け

**状況**: ファイル単位の責務をどう分けるか。

**決定**:

| レイヤー | ファイル | 責務 | 依存してよい相手 |
|---|---|---|---|
| 外部API | `src/rakuten/client.py` | 楽天API HTTPクライアント、認証、レート制限 | 標準ライブラリ、aiohttp、プロジェクトのerrors/logger |
| 外部API | `src/rakuten/books_api.py` | 楽天ブックス書籍検索のラッパー | client.py、標準ライブラリ |
| 外部API | `src/rakuten/kobo_api.py` | 楽天Kobo検索+類似度マッチング | client.py、標準ライブラリ |
| 永続化 | `src/database/kobo_watch.py` | SQLiteアクセサ | `_base.get_connection()` のみ |
| ビジネス | `src/units/kobo_watch.py` | 意図分岐、定期チェック、通知生成 | rakuten、database/kobo_watch、BaseUnit |
| UI | `src/web/routes/kobo_watch.py` | REST API | database/kobo_watch |
| UI | `src/web/static/js/pages/kobo-watch.js` | 管理ページJS | /api/kobo-watch/* |

**原則**:
- **上位レイヤーは下位レイヤーのみ参照**(逆流禁止)
- **ユニットだけがビジネスロジックを持つ**(ルーティングや通知生成)
- **DB層はCRUDのみ**(条件や判定を持たない)
- **楽天API層はHTTP応答→dataclass変換のみ**(ビジネスロジックを持たない)

**効用**:
- `src/rakuten/` は独立してユニットテスト可能
- `src/database/kobo_watch.py` はin-memory SQLiteで高速テスト可能
- ビジネスロジックの変更が他レイヤーに波及しない

---

### ADR-009: データモデル3テーブル構成

**状況**: SQLiteテーブル設計。最小でも1テーブル(監視対象)で動作させられるが、通知履歴や新刊判定のために分けるか。

**決定**: **3テーブル構成**:
- `book_watch_targets` (監視対象)
- `book_watch_known_books` (既知ISBN履歴)
- `book_watch_detections` (検出・通知履歴)

**理由**:

`book_watch_known_books` を分ける理由:
- 新刊判定の一次キーがISBN。高速照合のためインデックスを貼りたい
- target削除時のカスケード削除(`ON DELETE CASCADE`)で自動クリーンアップ
- backfill 時に大量INSERT、定期チェック時は頻繁SELECT、という異なるアクセスパターンを分離

`book_watch_detections` を分ける理由:
- 「通知した・しなかった・遅延した」の監査ログとして価値がある
- WebGUIで「最近検出された新刊履歴」として表示できる
- suppressed_reason で通知抑制の原因を残せる(運用上の観察材料)
- 同じISBNに対して複数回(紙→Kobo版)通知を送るケースに対応

**各テーブルの主キー設計**:
- `book_watch_targets.id`: AUTOINCREMENT(Discordコマンドで `#5 を削除` 等の参照用)
- `book_watch_known_books.isbn`: PRIMARY KEY(新刊判定の一次キー、重複記録を防ぐ)
- `book_watch_detections.id`: AUTOINCREMENT(通知履歴の独立ログ)

---

### ADR-010: 定期チェックは1時間ごと起動で1日1回実行

**状況**: 「毎日JST 7:00に1回実行」をどうスケジュールするか。

**検討した選択肢**:
- **APScheduler の cron トリガー**: 正確な時刻指定可、プロジェクトでも既に使用
- **`@tasks.loop(hours=24)`**: シンプルだが起動時刻が安定しない(Botの再起動で変わる)
- **`@tasks.loop(hours=1) + 時刻チェック`** ← **採用**

**決定**: **毎時起動し、`hour == check_hour_jst` の時だけ本処理を走らせる**。

**理由**:
- discord.py の `@tasks.loop` で完結し、APScheduler を追加で噛ませない
- Bot再起動があっても、次の毎時ループで処理される
- 1日1回しか実行されないため、起動分のオーバーヘッドは無視できる

**トレードオフ**:
- 正確にJST 7:00に走らない(その時間帯の1時間窓のどこか)
- 精度が必要なら将来 APScheduler cron に置き換え可能

---

## 失敗モード分析 (FMEA)

| 失敗 | 検知方法 | 影響 | 対処 |
|---|---|---|---|
| 楽天API 認証失敗 (400) | HTTPステータス | kobo_watch停止 | `.env` 設定ミス。起動時バリデーション追加 |
| Referer/IP不一致 (403) | HTTPステータス | kobo_watch停止 | IP変動検知と連携。Discord即時通知 |
| レート超過 (429) | HTTPステータス | 一時失敗 | リクエスト間隔1.5秒を内部保証。指数バックオフ |
| 楽天API 継続的失敗 | サーキットブレーカー | kobo_watch一時停止 | 10分後に自動復帰試行。人間に通知 |
| Kobo検索の誤マッチ | なし(サイレント) | 「Kobo版あり」と誤通知 | 類似度しきい値を config.yaml で調整可 |
| 登録直後に既刊が新刊扱い | 通知量で気づく | 大量通知 | 登録時 backfill で事前に「既知」化 |
| OBS配信中に通知が飛ぶ | 事後に気づく | 配信事故 | ActivityDetector 連携で遅延配信 |
| IPが/16範囲外に変動 | Discord通知 | 楽天API 403 | 手動で楽天管理画面のIPを更新 |
| SQLite書き込み競合 | ログ | 一部記録欠損 | WAL モード + aiosqlite の排他制御 |

---

## 将来拡張の余地

### 優先度高(実装しやすい)
- **ページング対応**: 30件超のシリーズで取りこぼしが出るケースに対応
- **購入確認メール連携**: Gmail APIで「【楽天Kobo】ご購入ありがとうございます」を読んで自動登録
- **通知サマリー**: 1日分の新刊を朝のサマリーにまとめて送信(現在は検出時即通知)

### 優先度中(設計変更あり)
- **出版社フィルタ**: 監視対象に `publisher` 条件を追加
- **複数書店対応**: Amazon Kindle、honto、BookWalker への拡張。`src/bookstore/` パッケージ化を検討
- **発売前予約通知**: 予約開始時点(楽天の `availability=5`等)での検知

### 優先度低(機能追加)
- **シリーズ自動紐付け**: 同シリーズの別出版社展開(紙は小学館、電子はKADOKAWA等)の自動発見
- **LLMによる著者自動抽出**: 「『薬屋のひとりごと』の新刊監視して」から著者名を LLMで引く
- **InnerMindへの情報源追加**: 自律思考の ContextSource として「今週の新刊」を追加し、ミミが雑談ネタにできる

---

## 非採用にした案の記録

### NDLサーチ + 楽天Kobo検索URL生成(案C)
- 登録不要で実装が最も簡単
- Kobo版の存在確認ができないため、ユーザー価値が下がる
- 楽天API新仕様の IP登録が運用可能と判断したため非採用

### 楽天ブックス検索ページのスクレイピング
- 登録不要だが、HTML変更で壊れる耐久性の低さが問題
- 楽天のTOSに触れる可能性
- APIが使える見込みが立ったため不要

### openBD + 出版社RSS
- 登録不要だが、openBDはISBN指定のみで検索機能がない
- 出版社ごとに異なるRSS対応が必要で実装コスト膨張

---

## 参考文献・外部情報

- 楽天ウェブサービス 公式ドキュメント: `https://webservice.rakuten.co.jp/documentation`
- 楽天API 2026年新仕様 移行ガイド: `https://ai-fukugyo-hack.com/rakuten-api-2026-migration/` (個人ブログ)
- openBD: `https://openbd.jp/`
- 国立国会図書館サーチ API: `https://ndlsearch.ndl.go.jp/help/api/specifications`
- プロジェクト共通規約: `CLAUDE.md` / `docs/guides/unit-creation-guide.md`
