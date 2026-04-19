## 未実装項目サマリ

（現在なし）

## 完了済み

### Image Gen Console: Wildcard / Dynamic Prompts

プロンプト内に変数記法を埋め込み、投入時に展開する機能。サーバ / クライアント両側で対称実装。

**対応記法**
- `{a|b|c}` 均等ランダム
- `{2::a|1::b}` 重み付き（非負数値、負値は 0 にクランプ、合計 0 は均等）
- `{1-5}` 整数ランダム（inclusive、両端逆転可、負値可）
- `__name__` 辞書ファイルから 1 行ランダム（`#` 行と空行はコメント）
- `\{ \| \} \: \\ \_` 1 文字エスケープ
- 入れ子は非対応（`{` は最初の `}` で閉じ、置換結果は再スキャンしない）
- 未定義 `__name__` はリテラルを残し warnings に記録

**展開の実装場所**
- サーバ側: `src/units/image_gen/wildcard_expander.py`（真のソース）
- クライアント側: `src/tools/image_gen_console/static/js/lib/wildcard.js`
  （プレビューとバッチループ内展開用、Mulberry32 で決定的）
- 両側の挙動一致はテスト / スモークで確認

**辞書ファイルの保管**
- SQLite `wildcard_files` テーブル（migration v31、`name` PK）
- 内容・説明・タイムスタンプを持ち、1 ファイル最大 200KB
- ファイル名は `^[A-Za-z0-9_.\-]{1,64}$`
- API: `/api/generation/wildcards/*`
  - `GET /` 一覧 / `GET /bulk` 全内容（N+1 回避）/ `POST /expand` プレビュー
  - `GET|PUT|DELETE /{name}`

**Seed 整合（バッチ時の展開モード）**
- `random`: 毎イテレーションで独立にランダム展開
- `tied`: 画像 `SEED` に追従（同 seed = 同展開）
- `fixed`: バッチ前に 1 回だけ展開、全枚同じ結果を使い回す
- 展開結果は `params.__WILDCARD_TRACE__` に `{template_positive, template_negative, rng_seed, choices, warnings}` として保存し、ジョブ DB から再現可能

**UI**
- Generate ページ: 🎲 Wildcard 行（モード選択 + 専用 seed + 辞書ページへのリンク）
- `#/wildcards`: 辞書ファイルの一覧 / エディタ（CRUD）/ サーバ側 `/expand` プレビュー
- 保存・削除時はクライアントキャッシュを無効化
