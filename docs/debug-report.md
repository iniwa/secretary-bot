# デバッグ報告書 — ユニット単体テスト (2026-03-27)

## 概要

Ollama / Discord が利用できないリモート環境で、各ユニットの機能をデバッグするための仕組みを導入し、動作確認を行った。

## 導入した仕組み

### A. dry_run拡張（LLMRouter）

**変更ファイル:** `src/llm/router.py`

`config.yaml` の `debug.dry_run_responses` に purpose 別の固定レスポンスを設定できるようにした。

```yaml
debug:
  dry_run: true
  dry_run_responses:
    skill_routing: '{"skill": "memo", "parsed": {"action": "save", "content": "テスト"}}'
    conversation: "これはdry_runの返答です。"
    memory_extraction: "なし"
```

- `dry_run: true` の場合、`LLMRouter.generate()` は実際のLLMを呼ばず固定値を返す
- `dry_run_responses` に該当 purpose があればその値を返す
- 未設定の purpose は従来通り `[dry_run] purpose=xxx` を返す
- SkillRouter は `skill_routing` のレスポンスをJSONパースするため、任意のユニットへのルーティングをテスト可能

### C. ユニット単体デバッグランナー

**新規ファイル:** `debug_runner.py`

Discord Bot を起動せず、モックオブジェクトで各ユニットの `execute()` を直接呼べるCLIスクリプト。

#### 実物を使うもの
- **Database (SQLite)** — 一時ディレクトリに `debug_bot.db` を作成
- **ChromaDB** — 一時ディレクトリにインプロセスDBを作成
- **LLMRouter** — `dry_run: true` で動作（実際のLLM呼び出しはしない）

#### モック化するもの
- **Discord Bot** — `MockBot`（`add_cog`, `get_channel` 等をエミュレート）
- **Discord Context** — `MockContext`（`ctx.channel.send()` の内容を記録）
- **UnitManager / AgentPool** — `MockUnitManager`（Windows Agent は存在しない前提）

#### 使い方

```bash
# 全ユニット一括テスト
python debug_runner.py --all

# 特定ユニットのテスト
python debug_runner.py memo           # memo の全シナリオ
python debug_runner.py memo save      # memo の save アクションのみ
python debug_runner.py reminder list  # reminder の list アクションのみ

# SkillRouter テスト（dry_run_responses の設定に従ってルーティング）
python debug_runner.py --route "明日の会議をメモして"

# 対話モード（メニュー選択）
python debug_runner.py

# 外部 config.yaml を使用（dry_run は自動で true に強制）
python debug_runner.py --config config.yaml --all
```

## テスト結果

### 実行環境
- OS: Windows 11 Pro
- Python: 3.13
- Ollama: なし（リモート環境）
- Discord: 未接続

### 一括テスト結果

| # | ユニット | シナリオ | 結果 | 応答（要約） |
|---|---------|---------|------|-------------|
| 1 | reminder | add | OK | リマインダーを設定しました: 04/01 10:00 に「会議」 |
| 2 | reminder | list | OK | リマインダー一覧: #1 ... |
| 3 | reminder | todo_add | OK | ToDoに追加しました: 買い物リスト作る |
| 4 | reminder | todo_list | OK | ToDo一覧: #1 ... |
| 5 | reminder | todo_done | OK | ToDo #1 を完了にしました |
| 6 | memo | save | OK | メモしました: テストメモの内容 |
| 7 | memo | search | OK | メモ検索結果: #1 ... |
| 8 | timer | start (3秒) | OK | タイマー#1 を設定しました: 0.05分後に... |
| 9 | status | check | OK | システム状態: Ollama: 停止中, ... |
| 10 | chat | chat | OK | 現在省エネ稼働中です。これはdry_runの返答です。 |

**結果: 10/10 OK**

### SkillRouter テスト

入力: `"明日の会議をメモして"`

```json
{
  "skill": "chat",
  "parsed": {
    "message": "テスト"
  }
}
```

`dry_run_responses.skill_routing` に設定した固定JSONが正しくパースされ、ルーティングが機能した。

## 確認できたこと

1. **Database (SQLite)** — テーブル作成・CRUD操作・WALモードが正常動作
2. **CircuitBreaker** — 各ユニットのbreaker.check()/record_success() が正常動作
3. **LLMRouter** — dry_run モードで purpose 別レスポンスが正しく返される
4. **ChromaDB** — PersistentClient でのコレクション作成・count() が正常動作
5. **各ユニットの execute()** — parsed dict を受け取り、DB操作を含む処理が正常完了
6. **SkillRouter** — dry_run レスポンスのJSONパース・フォールバック動作が正常

## 未テスト項目（本環境では不可）

- Ollama / Gemini 実接続での LLM 生成
- Discord Bot のメッセージ送受信
- Windows Agent への委託（HTTP通信）
- VictoriaMetrics メトリクス取得
- ハートビートの定期実行
- WebGUI の画面表示・操作
- グレースフルシャットダウン（SIGTERM ハンドリング）

これらは Pi 本番環境またはOllama搭載PCでのデバッグが必要。

## 備考

- デバッグ用DBは `%TEMP%\secretary_bot_debug\` に作成される（テスト間で永続化される）
- `--config` で実際の `config.yaml` を使う場合、`dry_run` は自動で `true` に強制される
- 対話モードではカスタム parsed (JSON) を手入力してテスト可能
