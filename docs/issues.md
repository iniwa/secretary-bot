# 実環境確認で見つかった課題（2026-04-02）

## 1. LLM出力に特殊トークン・繰り返し・言語混入 → 修正済み

- **現象**: Ollamaの応答に `<|endoftext|>` `<|im_start|>` などの特殊トークンがそのまま出力される
- **現象**: 同じ内容が複数回繰り返される
- **現象**: `conversation_summary` に中国語・英語の要約が混入（日本語で生成されるべき）
- **該当データ**: `conversation_log` id:162、`conversation_summary` id:37〜38(英語)、id:45〜46(中国語) など
- **原因**: qwen3モデルのstopトークン未設定、プロンプトでの言語指定不足
- **対応**:
  - `ollama_client.py`: stopトークン(`<|endoftext|>`, `<|im_start|>`, `<|im_end|>`)を追加、特殊トークン除去・連続重複除去の後処理を追加
  - `heartbeat.py`: 圧縮プロンプトに日本語出力を明示的に強制
  - `ai_memory.py`, `people_memory.py`: 記憶抽出プロンプトに日本語出力ルールを追加
