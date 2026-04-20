# image_gen ドキュメント

secretary-bot の AI 画像生成基盤（ComfyUI バックエンド + LoRA 学習）に関する全文書をここにまとめている。

## ドキュメント一覧

### 設計

| ファイル | 内容 |
|---|---|
| [design.md](design.md) | 全体設計（アーキテクチャ・状態機械・エラー階層・キャッシュ戦略） |
| [api.md](api.md) | Windows Agent (`:7777`) API 仕様 |
| [nas_setup.md](nas_setup.md) | NAS 共有 `ai-image/` の初期化・運用 |
| [preset_compat.md](preset_compat.md) | Main/Sub PC 間のプリセット互換性チェック |

### セットアップ（[setup/](setup/)）

| ファイル | 対象 |
|---|---|
| [setup/README.md](setup/README.md) | セットアップ全体のマスターインデックス（作業順序・テンプレート） |
| [setup/mainpc.md](setup/mainpc.md) | MainPC（RTX 4080） |
| [setup/subpc.md](setup/subpc.md) | SubPC（RTX 5060 Ti） |
| [setup/pi.md](setup/pi.md) | Raspberry Pi（bot 本体） |
| [setup/verify.md](setup/verify.md) | E2E 動作確認 + トラブルシュート |

### 利用ガイド・残タスク

| ファイル | 内容 |
|---|---|
| [comfyui_usage.md](comfyui_usage.md) | ComfyUI 単体 + WebGUI 連携の使い方 |
| [todo.md](todo.md) | 実装ロードマップ（Phase 1〜4） |

## 実装状況サマリ

`todo.md` の集計（2026-04-20 時点）:

- **Phase 1（Walking Skeleton）**: 完了
- **Phase 2（複数 PC 分散）**: 完了
- **Phase 3（プロンプト / Discord 連携）**: 完了
- **Phase 4（LoRA 学習）**: 未着手 — `lora_train.py` / `kohya_manager.py` / WebGUI `/api/lora/projects/*` が残課題
- **API 仕様の未確定事項**（[api.md](api.md) §12）: Preview 送出頻度、ログバッファ上限、kohya sample_prompts 埋め込み、NAS 並行読み出し本数、`/image/generate` タイムアウト既定値
