# ZZZ Disc Manager — スキル要約の情報収集フロー

`zzz_characters.skill_summary`（キャラごとの運用メモ）を Web ソースから収集・要約する際のガイド。

## ツール選定

WebFetch / WebSearch はサイトごとに壁（Cloudflare / SPA レンダリング / 403）があるため、
実戦では Tavily MCP が最も安定する。

| ツール | 使いどころ | 備考 |
|--------|------------|------|
| `mcp__tavily__tavily_search` | クエリで情報収集（要点スニペット含む） | JP / EN 両方ヒットする。日本語ブログにも強い |
| `mcp__tavily__tavily_extract` | 特定 URL の本文を抽出 | `extract_depth="advanced"` で Fandom Wiki もほぼ取れる |
| `WebSearch` | キャラの英名や ID の同定、URL 候補の洗い出し | 軽量クエリ向き |
| `WebFetch` | 単発 URL を 15 分キャッシュ付きで取得 | **Hakush.in は ECONNREFUSED、Fandom Wiki は 403**。非推奨 |

## ソース優先順位

1. **Fandom Wiki（英語）** — `https://zenless-zone-zero.fandom.com/wiki/<EnglishName>`
   - キットの素の挙動（Basic / Dodge / Assist / Special / Chain / Core Passive / Additional Ability）が一番網羅的
   - Tavily extract で本文が取れる
2. **Hakush.in** — `https://zzz3.hakush.in/character/<id>`
   - データベース寄り。倍率やスケーリングの一次ソースに近い
   - SPA 構成で WebFetch / Tavily どちらも空レスポンスになるため、現状は参照不可（ブラウザで開く必要あり）
3. **Mobalytics / Icy Veins / Prydwen.gg** — 英語のメタ解説・ビルド論
   - 運用サイクル、優先度、閾値（「ATK 3,500 でバフ最大」等）の言語化が上手い
4. **日本語ソース**（note / game-walker / gamewith / hatenablog 等）
   - 応援エネルギー生成テーブルやバフ数値の日本語表記、**固有名詞の公式訳**を裏取り
   - note の攻略記事は数値まで含めて書いてあるものが多く、ATK 閾値・バフ上限などの合わせ込みに有用
   - Game8 日本語版は当初「参考にならない」と評価だったが、コアパッシブのフラグ値など具体数値で重宝する場面があった。**一次採用は避けつつ、数値の裏取り用として併用する** 運用が実用的
5. **Claude の学習データ** — 単独で信用しない
   - 新キャラ（ZZZ Ver.2.6 以降など）は学習が薄い / 古い

## 典型ワークフロー

1. **英名を確定する**
   - `characters.json` に格納されているのは日本語名のみ。`WebSearch` に日本語名 + "ZZZ" を投げ、Fandom/Hakush.in の URL から英名を同定する
   - 陣営単位（例: `Angels of Delusion faction members`）で引くと 1 クエリで複数キャラ分片付くことがある
2. **Fandom Wiki から本文抽出**
   - `tavily_extract(urls=[...], extract_depth="advanced", query="role specialty skill mechanics playstyle")` で複数キャラを並列取得
   - 返ってくる raw content にスキル倍率テーブル・コアパッシブ本文が含まれる
3. **日本語ソースで数値裏取り & 固有名詞の確認**
   - `tavily_search` に日本語キーワードで投げる（例: `千夏 妄想エンジェル ダメージバフ 攻撃力 条件`）
   - 複数サイトで同じ数値が出てくれば確度が高い。食い違う場合は Mindscape 差・バージョン差を疑う
4. **Claude が要約 → ユーザーレビュー**
   - 1 キャラ 2〜3 文に圧縮。役割タグ（「物理 / 支援」等）を先頭に付けると一覧性が上がる
   - 固有名詞は **日本語訳が確定しているものは日本語、未確定のものは英字併記**（例: 「Polarity Disorder」）
   - ATK 閾値・バフ上限・リソース生成量など **数値はできる限り残す**。運用メモとして実用度が跳ね上がる
5. **シナジー要素を明示的に拾う**
   - 単キャラの要約だけでなく、陣営シナジー（例: 千夏のエーテルベール展開 → アリアの応援エネルギー +4）は別立てで書いておく
   - ユーザーが具体的に挙げたシナジー（会話で指摘されたもの）は優先して取り込む

## 注意点

- **ZZZ Ver.2.6 頃のキャラは情報が薄い**。Fandom Wiki の記述が未埋めのことがある → 日本語ブログ頼り
- **Mindscape（凸）前提の数値に注意**。無凸基準で揃えないと矛盾した情報が混ざる
- **Hakush.in の URL はサブドメインが `zzz3.` で固定**（`zzz.hakush.in` は現在 DNS 解決しない）
- game8 の JP 版は当初ユーザーが「参考にならない」と判断したソース。**メイン参照は避ける**が、数値の裏取り用途で使うのは可

## 実例（妄想エンジェル 3 人の要約に用いたソース）

- `https://zenless-zone-zero.fandom.com/wiki/{Sunna,Aria,Nangong_Yu}` — キットの素の記述
- `https://mobalytics.gg/zzz/builds/{sunna,aria}` — リソース生成テーブル、ATK 閾値の言語化
- `https://note.com/dandy_okapi8097/n/nf6d90d09b247` — Ver.2.6 環境解説、数値まとめ
- `https://game-walker.com/zenzero-agent-chinatsu/` — 千夏のバフ数値の日本語表記
- `https://gamewith.jp/zenless/538068` — アリアの応援エネルギー運用フロー
