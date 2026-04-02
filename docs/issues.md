# 実環境確認で見つかった課題（2026-04-02）

## 1. LLM出力に特殊トークン・繰り返し・言語混入

- **現象**: Ollamaの応答に `<|endoftext|>` `<|im_start|>` などの特殊トークンがそのまま出力される
- **現象**: 同じ内容が複数回繰り返される
- **現象**: `conversation_summary` に中国語・英語の要約が混入（日本語で生成されるべき）
- **該当データ**: `conversation_log` id:162、`conversation_summary` id:38〜45 など
- **原因候補**: qwen3.5モデルのプロンプト制御（stop token設定、言語指定）が不十分
- **対応方針**: Ollamaへのリクエスト時にstopトークンを明示的に指定する、システムプロンプトで日本語を強制する等

## 2. ChromaDB `ai_memory` が空 → 対応済み

- **現象**: `ai_memory` コレクションにドキュメントが0件
- **原因**: Ollamaが長時間unavailableだったため書き込みがスキップされていた。Ollama復帰後の会話ではLLMが「なし」と判定
- **対応**: ハートビート圧縮時にもai_memory抽出を実行するよう `heartbeat.py` を修正。Ollama稼働中であれば圧縮対象の会話から自動的に記憶抽出が行われる

## 3. ChromaDB `conversation_log` が空 → 修正済み

- **現象**: ChromaDB側の `conversation_log` コレクションが0件
- **原因**: `heartbeat.py` の `_check_compact()` がSQLiteにのみ保存し、ChromaDBへの書き込みが未実装だった
- **対応**:
  - `_check_compact()` に ChromaDB `conversation_log` への書き込みを追加
  - Bot起動時に `sync_summaries_to_chroma()` でSQLiteの既存45件をChromaDBに同期
  - デプロイ済み・動作確認完了
