# OBSディレクトリ管理 設計書

## 概要
OBSの録画・リプレイ・スクリーンショットを、ゲーム名フォルダに自動整理する機能。
2pc-obs プロジェクトの Sub PC 側機能を secretary-bot の Windows Agent に統合する。

**ゲーム名の取得はアクティビティ判定の `/activity` API を間借りする。**
→ 設計詳細: `docs/design/activity_detection_design.md`

## 設計思想

```
activity_detection（アクティビティ判定）
  └── Main PC /activity → ゲーム検出（game_detector.py）
        ↑ 間借り
obs_dir_manage（本機能）
  └── OBSイベント発火 → /activity でゲーム名取得 → フォルダ振り分け
```

- ゲーム検出はアクティビティ判定の責務
- 本機能はその公開APIを消費するだけ
- ゲーム検出が拡張されれば、本機能も自動的に恩恵を受ける

## 機能一覧

| 機能 | トリガー | 動作 |
|------|---------|------|
| 録画ファイル整理 | OBS RecordStateChanged (STOPPED) | ゲーム名フォルダに移動 |
| リプレイ整理 | OBS ReplayBufferSaved | ゲーム名フォルダに移動 |
| スクリーンショット整理 | OBS ScreenshotSaved | ゲーム名フォルダに移動 + pngquant圧縮 |
| 迷子ファイル掃除 | 定期（1時間） | OBS出力ディレクトリの残留ファイルをUnknownに移動 |
| 空フォルダ削除 | 定期（1時間） | メディアファイルのないフォルダを削除 |

## 実行場所
Sub PC の Windows Agent がバックグラウンドで実行。
OBS WebSocket EventClient でイベントを受信し、ファイル操作を行う。

## 処理フロー

```
[OBS イベント発火（録画停止/リプレイ保存/スクショ保存）]
  → Main PC の /activity を問い合わせ
  → game が返れば → ゲーム名フォルダに移動
  → game が null なら → foreground_process のプロセス名をフォルダ名に使用
  → どちらも null → Unknown フォルダに移動

[スクリーンショットの場合]
  → 上記に加え pngquant で圧縮 → encoded_base_dir に配置

[定期クリーンアップ（1時間間隔）]
  → OBS出力ディレクトリの残留ファイルを Unknown に掃除
  → メディアファイルのない空フォルダを削除
  → incoming スクショの残存PNGを圧縮
```

## ディレクトリ構成（出力先）

```
V:/tdarr/incoming/           # output_base_dir
  ├── VALORANT/
  │   ├── 録画ファイル.mp4
  │   └── リプレイ.mp4
  ├── Apex Legends/
  ├── Unknown/               # ゲーム検出できなかったファイル
  └── _screenshot/
      ├── VALORANT/
      │   └── screenshot.png
      └── Unknown/

V:/tdarr/encoded/            # encoded_base_dir
  └── _screenshot/
      ├── VALORANT/
      │   └── screenshot.png  # pngquant圧縮済み
      └── Unknown/
```

## Windows Agent 構造

```
windows-agent/
  activity/
    obs_manager.py       # OBS WebSocket接続 + 本機能の実装
```

`obs_manager.py` は以下を担当：
- OBS WebSocket への接続・再接続・死活監視
- OBS状態取得（`/activity` レスポンス用）← アクティビティ判定向け
- ファイル整理イベントハンドラ ← 本機能

## config.yaml

```yaml
obs_file_organizer:
  enabled: true
  output_base_dir: "V:/tdarr/incoming"
  encoded_base_dir: "V:/tdarr/encoded"
  unknown_folder: "Unknown"
  screenshot_folder: "_screenshot"
  file_move_retries: 3
  file_move_retry_delay_seconds: 1
  cleanup_interval_seconds: 3600
```

WebGUIからパス設定を変更可能。

## 2pc-obs からの移植元

| 参照元 | 用途 |
|--------|------|
| `2pc-obs/sub_pc/agent.py` → `organize_file()` | ファイル移動（リトライ付き） |
| `2pc-obs/sub_pc/agent.py` → `make_handlers()` | OBSイベントハンドラ生成 |
| `2pc-obs/sub_pc/agent.py` → `compress_screenshot()` | pngquant圧縮 |
| `2pc-obs/sub_pc/agent.py` → `sweep_stray_files()` | 迷子ファイル掃除 |
| `2pc-obs/sub_pc/agent.py` → `cleanup_empty_dirs()` | 空フォルダ削除 |
| `2pc-obs/sub_pc/agent.py` → `cleanup_loop()` | 定期クリーンアップ |

## 依存パッケージ（Sub PC Windows Agent）
- `obsws-python` — OBS WebSocket v5 クライアント
- `pngquant` — スクリーンショット圧縮（システムインストール or `pngquant-cli`）

## 2pc-obs 退役手順
1. 本機能が安定稼働を確認
2. Sub PC の 2pc-obs スタートアップ登録を解除
3. Main PC の 2pc-obs スタートアップ登録を解除
4. 2pc-obs リポジトリをアーカイブ

## 未決事項
- [ ] game_editor.py のWebGUI統合（将来）
