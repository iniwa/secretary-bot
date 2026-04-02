# 実環境確認で見つかった課題（2026-04-02）

## 1. LLM出力に特殊トークン・繰り返し・言語混入

- **現象**: Ollamaの応答に `<|endoftext|>` `<|im_start|>` などの特殊トークンがそのまま出力される
- **現象**: 同じ内容が複数回繰り返される
- **現象**: `conversation_summary` に中国語・英語の要約が混入（日本語で生成されるべき）
- **該当データ**: `conversation_log` id:162、`conversation_summary` id:38〜45 など
- **原因候補**: qwen3.5モデルのプロンプト制御（stop token設定、言語指定）が不十分
- **対応方針**: Ollamaへのリクエスト時にstopトークンを明示的に指定する、システムプロンプトで日本語を強制する等

## 2. ChromaDB `ai_memory` が空

- **現象**: `ai_memory` コレクションにドキュメントが0件
- **仕様**: Ollama専用（Gemini不可）のため、Ollama未稼働時は書き込まれない
- **確認事項**: Ollama稼働中に記憶形成処理が正しく動作しているか検証が必要

## 3. ChromaDB `conversation_log` が空

- **現象**: ChromaDB側の `conversation_log` コレクションが0件
- **一方**: SQLite側の `conversation_summary` には45件の要約が存在
- **原因候補**: ハートビートで生成した要約をChromaDBに書き込む処理が未実装または停止中
- **対応方針**: `heartbeat.py` の圧縮処理でChromaDBへの書き込みフローを確認・修正
