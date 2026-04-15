## 未実装項目サマリ

### AI画像生成機能
- [ ] `image_gen_**.md` を参照。
  - `docs/design/` と `docs/setup/` にファイルがある
  - リモート環境下である程度実装済み
  - 計画済みなので、あとは実機で実稼働環境を整えてから

### LLM
- [x] MainPCのollamaがCPU稼働してたかも？
  - 2026-04-16 に確認・修正済み
  - 原因1: Ollama 0.20.3 時点で CUDA バックエンドが機能しておらず GPU 未検出 → 0.20.7 へ更新で解消
  - 原因2: ComfyUI が二重起動（0.0.0.0 と 127.0.0.1）して VRAM を約 11 GiB 占有し、Ollama が部分 CPU オフロードに落ちていた
  - 対策: `windows-agent/tools/image_gen/comfyui_manager.py` に既存プロセス検知（HTTP プローブ）を追加し、エージェント再起動後の二重起動を防止
