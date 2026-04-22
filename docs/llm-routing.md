# LLM ルーティング仕様

Ollama と Gemini の使い分け、優先度制御、フォールバック条件のリファレンス。
実装は `src/llm/` 配下。

## 1. 全体像

```
呼び出し元（Unit / InnerMind / RSS / WebGUI chat など）
        │
        ▼  purpose + フラグ
[ UnitLLM ]   ← src/llm/unit_llm.py（Unit 側ファサード）
        │
        ▼
[ LLMRouter.generate() ]   ← src/llm/router.py（中央ディスパッチャ）
   ├── [ OllamaClient ]    ← src/llm/ollama_client.py（マルチインスタンス・優先度キュー）
   └── [ GeminiClient ]    ← src/llm/gemini_client.py（フォールバック）
```

すべての LLM 呼び出しは `LLMRouter.generate()` を通る。Router は purpose と
フラグに従って Ollama / Gemini を選択する。

## 2. 呼び出し時の判定フロー

`LLMRouter.generate()`（`src/llm/router.py:139`）の処理順。

1. **dry_run モード**: `debug.dry_run: true` なら `debug.dry_run_responses[purpose]`
   を返して終了。
2. **Ollama 可用性チェック**: `ollama_available` が False ならその場で `check_ollama()`。
   クールダウン中（後述）はチェックをスキップして False 固定。
3. **Ollama 利用可能 → Ollama で生成**
   - purpose → priority に変換して `OllamaClient.generate()` を呼ぶ。
   - 成功: 結果を返して終了。
   - `OllamaUnavailableError`: `ollama_available = False`、クールダウン開始、
     次のフォールバック判定へ進む。
4. **`ollama_only=True`**: Gemini を使わず `AllLLMsUnavailableError("Ollama required but unavailable")` で終了。
5. **Gemini 許可判定** (`_is_gemini_allowed(purpose)`, `router.py:127`):
   - purpose が `conversation` / `unit_routing` / `memory_extraction` のいずれか。
   - `gemini.<purpose>` トグルが `true`。
   - `gemini.monthly_token_limit` が 0 でなく、かつ使用量が上限未満。
   - 加えて呼び出し側の `gemini_allowed=True`（デフォルト True）。
6. **Gemini 利用 → Gemini で生成**
   - 成功: 結果を返して終了。
   - `GeminiError`: ログ出力して次へ。
7. **全滅**: `AllLLMsUnavailableError("No LLM available for purpose=...")` を送出。

### 2.1 Ollama クールダウン

`OllamaUnavailableError` が出た瞬間に `_ollama_cooldown_until` を
`llm.ollama_cooldown_sec`（デフォルト 60 秒）先にセットする。クールダウン中は
`check_ollama()` が `/api/tags` を叩かず False を返す。`/api/tags` が 200 でも
`/api/chat` で 500 を返す「ゾンビ状態」での誤復帰を防ぐ仕組み。

## 3. Purpose と優先度

`PURPOSE_PRIORITY`（`src/llm/ollama_client.py:22`）で purpose → Ollama 優先度
を決める。数値が小さいほど優先。

| purpose | priority | 用途 | Gemini トグル対象 |
|---|---|---|---|
| `conversation` | 0 (HIGH) | Unit の LLM 呼び出し／ユーザー会話 | ◯ |
| `unit_routing` | 0 (HIGH) | 入力をどの Unit に渡すかの判定 | ◯ |
| `inner_mind` | 1 (MEDIUM) | 内部思考・文脈要約 | × |
| `stt_summary` | 1 (MEDIUM) | 音声認識後の要約 | × |
| `rss_summary` | 2 (LOW) | RSS 記事要約 | × |
| `memory_extraction` | 2 (LOW) | ai_memory / people_memory 抽出 | ◯（実際は `ollama_only=True` 呼びが多い） |

`_PURPOSE_TO_TOGGLE`（`router.py:15`）に含まれない purpose は Gemini
フォールバックされない。追加する際は両方のマッピングを更新する。

## 4. Ollama マルチインスタンス

### URL 優先順

`LLMRouter.__init__` で以下の順に URL を構築（`router.py:29-48`）。

1. `llm.ollama_url`（直接指定・任意）
2. `windows_agents[].host` を `priority` 昇順で並べたもの
3. どちらも空なら `http://localhost:11434`

### インスタンス選択（least-connections + 優先度キュー）

`OllamaClient._try_acquire`（`ollama_client.py:131`）:

1. `_available_urls` を先頭から走査。
2. `_active_count[url] == 0`（空きインスタンス）かつ指定モデルを持つものを取る。
3. GPU モニターが「他プロセスでビジー」と判定したインスタンスはスキップ。
   他に空きが無ければフォールバックで使用（全滅防止）。
4. 空きが無ければ `_Waiter` を作って優先度キュー（heapq）で待機。
   解放時 `_dispatch_next` で最高優先度の待機者に割り当て。

### リトライ

`OllamaClient.generate`（`ollama_client.py:255`）は 1 インスタンスで失敗したら
そのインスタンスを `_mark_unavailable` して、残りから別インスタンスを取り直し
て 1 回だけリトライする。両方失敗で `OllamaUnavailableError`。

### GPU メモリ監視

`llm.gpu_memory_skip_bytes` が 0 より大きい場合、`metrics.victoria_metrics_url`
から各インスタンスの GPU メモリ使用量を取得し、閾値超過なら「ビジー」と判定。
ゲーム等で GPU が埋まっている PC にルーティングしない。

## 5. ollama_only フラグ

Gemini を使わせず Ollama 必須にしたい呼び出しで指定する。Ollama 落ちると
即エラー。現状の使用箇所:

- `src/inner_mind/core.py`（内部思考 3 箇所）
- `src/inner_mind/context_sources/conversation.py`
- `src/memory/ai_memory.py`（記憶書き込み — Gemini だと人格の一貫性が崩れるため）
- `src/rss/processor.py`（RSS 要約）

### UnitLLM での継承

`UnitLLM.from_config`（`src/llm/unit_llm.py:84`）は次の優先順で `ollama_only`
を決める:

1. `units.<unit>.llm.ollama_only`（Unit 個別上書き）
2. `character.ollama_only`（全 Unit のデフォルト）
3. `False`

`character.ollama_only: true` にすると全 Unit が Ollama 必須になる。
**Ollama 落下時にチャット全滅する**ので、Gemini フォールバックを有効にしたい
場合は `false` が必須。

## 6. Gemini トグル

### 設定の場所

| 場所 | キー | 優先順 |
|---|---|---|
| `config.yaml` | `gemini.conversation` / `gemini.unit_routing` / `gemini.memory_extraction` / `gemini.monthly_token_limit` | 起動時に読む |
| SQLite `settings` テーブル | `gemini.<name>` | **DB が上書き**（`src/bot.py::_restore_settings`） |

DB 側は WebGUI の設定画面から変更される。`config.yaml` を直接触っても DB に
値があれば起動時に上書きされるので、運用では **WebGUI 経由で変更** するのが
正。CLI で変える場合は `settings` テーブルも削除 or 更新する必要がある。

### Unit 単位の Gemini 許可

- `units.<unit>.llm.gemini_allowed`（`config.yaml`）
- `unit_gemini.<unit>`（DB、WebGUI から設定）

両方 `true`（デフォルト）なら Unit からの呼び出しで Gemini フォールバック可。
`false` にすると purpose 側トグルが true でもその Unit からは Gemini を使わない。

### Gemini モデル

- `llm.gemini_model`（config）/ DB `llm.gemini_model` で指定。
- 未設定なら `GeminiClient.DEFAULT_MODEL = "gemini-2.0-flash"`。
- トークン使用量は `GeminiClient.total_tokens_used` に累積。`monthly_token_limit`
  を超えたら自動で Gemini 使用停止（`limit > 0` のときのみ）。

## 7. 典型的なパス

### 通常会話（Ollama 稼働中）

```
User → WebGUI → bot → UnitRouter.generate(purpose="unit_routing", HIGH)
                          └→ Ollama (gemma4 等)
   → Unit.execute → UnitLLM.generate(purpose="conversation", HIGH)
                          └→ Ollama
   → BaseUnit.personalize → Ollama（ペルソナ注入）
```

### Ollama 落下（Gemini フォールバック有効）

```
UnitRouter → Ollama 失敗 → cooldown セット → Gemini (unit_routing トグル true)
Unit → Ollama unavailable → Gemini (conversation トグル true)
BaseUnit.personalize → `ollama_available == False` なので skip（定型文返す）
```

### Ollama 落下（トグル無効 or ollama_only）

```
UnitRouter → Ollama 失敗 → Gemini 不許可 → AllLLMsUnavailableError
                                           → WebGUI にエラー表示
```

## 8. 並列化ガイドライン

`OllamaClient` は least-connections 分配なので、独立した LLM 呼び出しは
`asyncio.gather()` で並列化すると空きインスタンスへ自動分配される。

- ループ内で直列 `await llm.generate(...)` は避ける。
- 依存関係がある場合（前の結果が次の入力）は直列のまま。
- `return_exceptions=True` を付けて 1 件の失敗が他に波及しないようにする。

## 9. トラブルシューティング

| 症状 | 原因の当たり所 |
|---|---|
| `Ollama required but unavailable` | 呼び出し側が `ollama_only=True`。`character.ollama_only` か Unit 設定を確認 |
| `No LLM available for purpose=...` | `gemini.<purpose>` トグルが false、または `gemini_allowed=False` |
| Ollama 生きてるのに Gemini に落ちる | クールダウン中（60 秒）。`ollama_cooldown_sec` で調整 |
| 特定 PC にだけ振り分かれない | GPU メモリ閾値超過、またはモデル未インストール |
| config.yaml を変えても反映されない | DB `settings` テーブルの同名キーが上書きしている。WebGUI で変更するか DB を直接操作 |
