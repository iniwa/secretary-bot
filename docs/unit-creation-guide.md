# ユニット作成マニュアル

## 概要

ユニットは secretary-bot の機能単位。BaseUnit（discord.py の Cog）を継承し、`execute()` を実装するだけで動作する。

## 最小構成

### 1. ユニットファイルを作成

`src/units/my_unit.py`:

```python
"""ユニットの説明。"""

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit


class MyUnit(BaseUnit):
    UNIT_NAME = "my_unit"                    # UnitRouterが使う識別子（必須・一意）
    UNIT_DESCRIPTION = "このユニットの説明。" # UnitRouterがLLMに渡す説明文（必須）

    async def execute(self, ctx, parsed: dict) -> str | None:
        """メイン処理。戻り値がユーザーへの返答になる。"""
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        # サーキットブレーカーチェック（必須）
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        # ユニット実行
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            result = "処理結果"
            self.breaker.record_success()
            self.session_done = True  # セッション終了（後続操作がなければTrue）
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise


async def setup(bot) -> None:
    await bot.add_cog(MyUnit(bot))
```

### 2. UnitManager に登録

`src/units/__init__.py` の `_UNIT_MODULES` に追加:

```python
_UNIT_MODULES = {
    # ...既存ユニット...
    "my_unit": "src.units.my_unit",
}
```

### 3. config.yaml に追加

```yaml
units:
  my_unit:
    enabled: true
```

これだけで自動ロードされる。

## BaseUnit が提供する機能

### プロパティ・属性

| 属性 | 型 | 説明 |
|------|-----|------|
| `self.bot` | SecretaryBot | Bot本体。DB・ChromaDB等にアクセス可能 |
| `self.llm` | UnitLLM | LLMアクセスファサード（ユニット別設定対応） |
| `self.breaker` | CircuitBreaker | サーキットブレーカー |
| `self.session_done` | bool | `True` にするとルーターのセッションがクリアされる |

### ヘルパーメソッド

```python
await self.notify("メッセージ")        # Discord管理チャンネルに通知
await self.notify_error("エラー内容")   # エラー通知（[Error] プレフィックス付き）
await self.personalize(result, user_message, flow_id)  # ペルソナ注入（Ollama稼働時のみ）
```

### bot 経由でアクセスできるもの

```python
self.bot.database        # Database — SQLite操作
self.bot.chroma          # ChromaMemory — ベクトル検索
self.bot.config          # dict — config.yaml の内容
self.bot.unit_router     # UnitRouter
self.bot.unit_manager    # UnitManager
# LLMへのアクセスは self.llm を使う（self.bot.llm_router は直接使わない）
```

## クラス定数

| 定数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `UNIT_NAME` | str | Yes | 一意の識別子。UnitRouterのルーティング先 |
| `UNIT_DESCRIPTION` | str | Yes | LLMに渡す自然言語の説明。ルーティング精度に直結 |
| `DELEGATE_TO` | str \| None | No | `"windows"` でWindows PCに委託 |
| `PREFERRED_AGENT` | str \| None | No | 委託先PCのID（省略時はpriority順） |

## execute() の引数

```python
async def execute(self, ctx, parsed: dict) -> str | None:
```

| 引数 | 型 | 説明 |
|------|-----|------|
| `ctx` | discord.Context \| None | Discordからの呼び出し時はContext、WebGUIからは `None` |
| `parsed` | dict | UnitRouterがLLMから受け取ったパース結果。`message` キーにユーザーの元メッセージが入る |

**戻り値**: 文字列を返すとユーザーに送信される。`None` は無応答。

### parsed の主要キー

| キー | 型 | 説明 |
|------|-----|------|
| `message` | str | ユーザーの元メッセージ |
| `channel` | str | チャンネル識別子（`"discord"` / `"webgui"`） |
| `flow_id` | str | フローチャート追跡用ID（後述） |

UnitRouterはLLMに以下のJSON形式を要求する:

```json
{"unit": "my_unit", "parsed": {"action": "xxx", "param1": "yyy"}}
```

`parsed` のキーはユニット側で自由に定義する。LLMの判断精度に依存するため、シンプルに保つのが望ましい。

## サーキットブレーカー（必須パターン）

連続失敗時にユニットを一時停止する仕組み。全ユニットで以下のパターンを守る:

```python
async def execute(self, ctx, parsed: dict) -> str | None:
    self.breaker.check()          # 開いていたら CircuitOpenError を送出
    try:
        result = await self._do_work(parsed)
        self.breaker.record_success()  # 成功を記録
        return result
    except Exception:
        self.breaker.record_failure()  # 失敗を記録（3回連続で回路が開く）
        raise
```

デフォルト設定: 3回連続失敗 → 60秒間停止 → half_open で1回試行 → 成功なら復帰。

## フローチャート追跡（必須）

WebGUIの「Flow」ページで処理のリアルタイム可視化を行うため、全ユニットで `flow_tracker.emit()` を呼ぶ必要がある。

### 概要

- `FlowTracker` はシングルトン。`get_flow_tracker()` で取得する
- `emit(node, status, detail, flow_id)` でイベントを発火する
- イベントは SSE 経由で Flow ページに配信され、対応するノードがハイライトされる
- emit は非同期だが軽量で、メイン処理への影響はない

### 必須の emit ポイント

すべてのユニットで以下の2箇所は必須:

```python
from src.flow_tracker import get_flow_tracker

async def execute(self, ctx, parsed: dict) -> str | None:
    ft = get_flow_tracker()
    flow_id = parsed.get("flow_id")

    # 1. サーキットブレーカーチェック
    await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
    self.breaker.check()
    await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

    # 2. ユニット実行
    await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
    try:
        result = await self._do_work()
        self.breaker.record_success()
        await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
        return result
    except Exception:
        self.breaker.record_failure()
        await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
        raise
```

### 追加の emit ポイント（任意）

ユニットが記憶検索・書き込みを行う場合:

```python
# 記憶検索
await ft.emit("MEM_SEARCH", "active", {}, flow_id)
# ... 検索処理 ...
await ft.emit("MEM_SEARCH", "done", {"count": len(results)}, flow_id)

# 記憶書き込み
await ft.emit("MEM_WRITE", "active", {}, flow_id)
# ... 書き込み処理 ...
await ft.emit("MEM_WRITE", "done", {}, flow_id)
```

### emit のパラメータ

```python
await ft.emit(node, status, detail, flow_id)
```

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `node` | str | フローチャート上のノードID（下表参照） |
| `status` | str | `"active"` / `"done"` / `"error"` / `"skipped"` |
| `detail` | dict | 情報パネルに表示する追加情報（任意） |
| `flow_id` | str \| None | `parsed.get("flow_id")` で取得した値をそのまま渡す |

### 使用可能なノードID一覧

ユニットから使用するノードID:

| ノードID | 用途 | 使用場面 |
|---------|------|---------|
| `CB_CHECK` | サーキットブレーカーチェック | 全ユニット（必須） |
| `UNIT_EXEC` | ユニット実行 | 全ユニット（必須） |
| `MEM_SEARCH` | 記憶検索 | ChatUnit など記憶を参照するユニット |
| `MEM_WRITE` | 記憶書き込み | ChatUnit など記憶を保存するユニット |

他のノード（`MSG`, `LOCK`, `ROUTE_LLM`, `LLM_SELECT`, `PERSONA` 等）は Bot本体・UnitRouter・BaseUnit が自動的に emit するため、個別ユニットでの呼び出しは不要。

### personalize() と flow_id

`BaseUnit.personalize()` はペルソナ注入時に自動的に `PERSONA` / `PERSONA_GEN` / `SKIP_PERSONA` ノードを emit する。呼び出し時に `flow_id` を渡すこと:

```python
result = await self.personalize(result, user_message, flow_id)
```

## セッション管理

### session_done フラグ

`execute()` 内で `self.session_done = True` を設定すると、UnitRouter のセッションがクリアされ、次回は新規ルーティングになる。

- **True にする場合**: 1回で完結する処理（メモ保存、タイマー設定、リマインダー登録など）
- **False のままにする場合**: 後続操作が期待される処理（一覧表示後にIDで削除・編集など）

```python
# 例: list はセッション維持、それ以外は完了
if action == "list":
    result = await self._list()
    # session_done は False のまま → 次の入力も同じユニットで処理
elif action == "delete":
    result = await self._delete(extracted)
    self.session_done = True  # 完了 → 次回は新規ルーティング
```

### セッション文脈の保存

セッション継続中にLLMへ文脈を渡すためのヘルパー:

```python
# execute() の最後に保存
self.save_exchange(channel, user_message, result)

# _extract_params() 等で利用
context = self.get_context(channel) if channel else ""
prompt = base_prompt + context
```

## LLMパラメータ抽出パターン

ユーザー入力からLLMで構造化データを取り出すパターン。Reminder・Memo・Timer で共通して使われる:

```python
_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- action_a: 説明（必要なフィールド名を記載）
- action_b: 説明

## 出力形式（厳守）
{{"action": "アクション名", "param1": "値", "param2": "値"}}

- 不要なフィールドは省略してください。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""

async def _extract_params(self, user_input: str, channel: str = "") -> dict:
    context = self.get_context(channel) if channel else ""
    prompt = _EXTRACT_PROMPT.format(user_input=user_input)
    if context:
        prompt = prompt + context
    return await self.llm.extract_json(prompt)
```

## DB を使うユニット

### テーブル追加

`src/database.py` の `_INIT_SQL` にCREATE TABLE文を追加する:

```python
_INIT_SQL = """
-- ...既存テーブル...

CREATE TABLE IF NOT EXISTS my_data (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
```

### CRUD操作

```python
# INSERT
await self.bot.database.execute(
    "INSERT INTO my_data (content) VALUES (?)", ("値",)
)

# SELECT (単一行)
row = await self.bot.database.fetchone(
    "SELECT * FROM my_data WHERE id = ?", (1,)
)
# row => {"id": 1, "content": "値", "created_at": "..."} or None

# SELECT (複数行)
rows = await self.bot.database.fetchall(
    "SELECT * FROM my_data ORDER BY created_at DESC LIMIT 10"
)
# rows => [{"id": ..., ...}, ...]

# UPDATE
await self.bot.database.execute(
    "UPDATE my_data SET content = ? WHERE id = ?", ("新しい値", 1)
)
```

## LLM を使うユニット（UnitLLM）

全ユニットは `self.llm`（`UnitLLM` インスタンス）を通じてLLMにアクセスする。
`self.bot.llm_router` を直接呼ばないこと。

### 基本的な使い方

```python
# 自由文生成（チャット返答など）
response = await self.llm.generate(
    "プロンプト",
    system="システムプロンプト",    # 省略可
)

# JSON抽出（構造化データの取得）
# パース失敗時は自動リトライ（最大2回）
data = await self.llm.extract_json(
    "以下の情報をJSONで返してください: ..."
)
# data => {"key": "value", ...}
```

### ユニット別LLM設定

`config.yaml` でユニットごとにモデルを上書きできる:

```yaml
units:
  reminder:
    enabled: true
    llm:
      ollama_model: "qwen3:1.7b"   # 軽量モデルで日時解析
  chat:
    enabled: true
    # llm を省略 → グローバル設定（llm.ollama_model）を使用
```

設定可能な項目:

| キー | 型 | 説明 |
|------|-----|------|
| `ollama_model` | str | Ollamaモデル名（省略時: グローバル設定） |
| `gemini_model` | str | Geminiモデル名（省略時: gemini-2.0-flash） |
| `ollama_only` | bool | `true` でGeminiフォールバック禁止 |

### UnitLLM メソッド一覧

| メソッド | 戻り値 | 用途 |
|---------|--------|------|
| `generate(prompt, system=None)` | `str` | 自由文生成 |
| `extract_json(prompt, system=None, max_retries=2)` | `dict` | JSON抽出（リトライ付き） |

### purpose について

`BaseUnit` のデフォルト purpose は `"conversation"`。UnitRouter は `"skill_routing"` を使用する。
purpose は Gemini フォールバックの可否を制御する（`config.yaml` の `gemini:` セクション参照）。

| purpose | 用途 | Geminiトグルキー |
|---------|------|-----------------|
| `conversation` | 会話生成 | `gemini.conversation` |
| `skill_routing` | スキル振り分け | `gemini.skill_routing` |
| `memory_extraction` | 記憶抽出 | `gemini.memory_extraction` |

## ハートビート対応（任意）

定期実行したい処理がある場合、`on_heartbeat()` をオーバーライドする:

```python
async def on_heartbeat(self) -> None:
    """Ollama稼働時: 15分間隔、停止時: 180分間隔で呼ばれる。"""
    rows = await self.bot.database.fetchall("SELECT ...")
    for row in rows:
        await self.notify(f"通知: {row['content']}")
```

## Windows委託ユニット

PCのリソースが必要な処理はWindows PCに委託できる:

```python
class HeavyUnit(BaseUnit):
    UNIT_NAME = "heavy"
    UNIT_DESCRIPTION = "重い処理の説明"
    DELEGATE_TO = "windows"           # この1行で委託が有効になる
    PREFERRED_AGENT = "pc-main"       # 省略時はpriority順に選択

    async def execute(self, ctx, parsed: dict) -> str | None:
        # このコードはWindows Agent側で実行される
        ...
```

`DELEGATE_TO = "windows"` を指定すると、UnitManagerが自動的に `RemoteUnitProxy` でラップする。

## WebGUI にデータ閲覧を追加する

### API エンドポイント追加

`src/web/app.py` に GET エンドポイントを追加:

```python
@app.get("/api/units/my_data", dependencies=[Depends(_verify)])
async def get_my_data():
    rows = await bot.database.fetchall(
        "SELECT * FROM my_data ORDER BY created_at DESC LIMIT 100"
    )
    return {"items": rows}
```

### フロントエンド追加

`src/web/static/index.html` に:

1. サイドバーの Units グループにボタンを追加
2. `<div id="page-unit-myunit" class="page">` でページを追加
3. `showPage()` のif文にデータ読み込みを追加
4. データ取得用のJavaScript関数を追加

既存の Reminder / Memo ページの実装を参考にすること。

## debug_runner でテストする

### シナリオ追加

`debug_runner.py` の `SCENARIOS` に追加:

```python
SCENARIOS = {
    # ...既存...
    "my_unit": [
        {"label": "action_a", "parsed": {"action": "a", "param": "値"}},
        {"label": "action_b", "parsed": {"action": "b"}},
    ],
}
```

### テスト実行

```bash
python debug_runner.py my_unit         # 全シナリオ
python debug_runner.py my_unit action_a # 特定シナリオ
python debug_runner.py --all           # 全ユニット一括
```

## チェックリスト

新しいユニットを作成したら以下を確認:

- [ ] `src/units/my_unit.py` を作成し BaseUnit を継承
- [ ] `UNIT_NAME` と `UNIT_DESCRIPTION` を設定
- [ ] `execute()` でサーキットブレーカーパターンを実装
- [ ] `execute()` で `flow_tracker.emit()` を追加（CB_CHECK + UNIT_EXEC は必須）
- [ ] `personalize()` 呼び出し時に `flow_id` を渡す
- [ ] `session_done` を適切に設定（完結処理は `True`、一覧表示等は `False`）
- [ ] `src/units/__init__.py` の `_UNIT_MODULES` に登録
- [ ] `config.yaml` の `units:` に `enabled: true` で追加
- [ ] DBテーブルが必要なら `src/database.py` の `_INIT_SQL` に追加
- [ ] `debug_runner.py` の `SCENARIOS` にテストシナリオを追加
- [ ] `python debug_runner.py my_unit` で動作確認
- [ ] （任意）WebGUI にデータ閲覧ページを追加
- [ ] （任意）`on_heartbeat()` で定期処理を実装
- [ ] （任意）Flow ページに新規ノードを追加する場合は下記を参照

## Flow ページへのノード追加（任意）

ユニット固有の処理ステップをフローチャートに追加したい場合:

### 1. フロントエンドに Mermaid ノードを追加

`src/web/static/index.html` の `FLOW_CHART_DEF` にノードを追加:

```javascript
// 既存ノード間に挿入
const FLOW_CHART_DEF = `graph TD
    ...
    UNIT_EXEC --> MY_STEP[🔧 カスタム処理]
    MY_STEP --> LLM_SELECT
    ...
`;
```

### 2. NODE_LABELS にラベルを追加

```javascript
const NODE_LABELS = {
    // ...既存...
    "MY_STEP": "カスタム処理",
};
```

### 3. ユニットから emit する

```python
await ft.emit("MY_STEP", "active", {"detail": "値"}, flow_id)
# ... 処理 ...
await ft.emit("MY_STEP", "done", {"result": "値"}, flow_id)
```

通常のユニット追加では、既存の `CB_CHECK` + `UNIT_EXEC` ノードで十分なため、このステップは不要。
