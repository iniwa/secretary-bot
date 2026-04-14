# ZZZ Disc Manager

Secretary Bot に同居する **ゼンゼロ（Zenless Zone Zero）ディスク管理ツール**。

## 目的

- キャラクターごとの理想ディスク構成（プリセット）を保存
- 所持ディスクの台帳をスクショから VLM（Ollama `gemma4`）で自動抽出
- プリセットと突き合わせて「このディスクはどのキャラの候補か」「同じディスクを複数キャラで奪い合っていないか」を可視化
- 連続処理: ゲーム内でディスクを切り替え → Web UI の「解析」ボタン → バックグラウンドでキュー処理 → 後から一括確認、のサイクル

## アーキテクチャ

```
[WebGUI /tools/zzz-disc/] ─┐
                           ▼
[Pi: src/tools/zzz_disc]  ← BOT本体の src/web/app.py に register() で注入
    │
    ├ SQLite (zzz_* テーブル)
    └ asyncio キュー
            │
            ▼
[Windows Agent: /tools/zzz-disc/*]
    ├ mss / OBS WebSocket で画面キャプチャ
    └ Ollama gemma4 (format:"json") で VLM 抽出
```

- **別コンテナは立てない**（既存 `secretary-bot` コンテナと `windows-agent` に同居）
- 画面キャプチャは Windows 側、データ永続化は Pi 側

## 有効化

`config.yaml` に以下を追加（`config.yaml.example` からコピー可）:

```yaml
tools:
  zzz_disc:
    enabled: true
    vlm_model: "gemma4"
    delegate_to: "windows"
    capture:
      backend: "mss"       # or "obs"
      mss:
        monitor: 1
```

有効化後、BOT 再起動（WebGUI の「コード更新」→ Portainer 再起動）。

## 使い方（基本フロー）

1. サイドバー Tools > `ZZZ Disc ↗` で別タブ起動
2. `#/capture`（既定）で「🎯 今の画面を解析」ボタン、または `Space` キー
3. ゲーム内でディスクを次に切り替え、もう一度「解析」
4. 右ペインのキューで `ready` になったものを順次クリック → 内容確認 → 保存
5. `#/presets` でキャラごとのプリセット（セット/メインステ/サブステ優先度）を登録
6. `#/conflicts` で候補競合ビュー（同一ディスクが複数キャラで奪い合っている場合に ⚠）

## OBS キャプチャを使う場合

Sub PC 側 OBS Studio に `obs-websocket` プラグイン（OBS 28+ は標準搭載）を入れ、
`ツール > WebSocket サーバー設定` で有効化 → パスワード生成 →
Sub PC の `.env` に `OBS_WEBSOCKET_PASSWORD=...` を設定。

`config.yaml` で:
```yaml
tools:
  zzz_disc:
    capture:
      backend: "obs"
      obs:
        source_name: "Game Capture"   # OBS 側のソース名
```

## トラブルシュート

- **VLM 抽出 JSON がパースエラー**: UI に手動入力フォームが出るので編集保存
- **セット名が認識されない**: 抽出結果のセット欄が空なら UI のプルダウンで手動選択
- **Windows Agent 不在**: Capture/Extract は 503 になる。`#/upload` の手動登録は利用可能
- **ジョブがスタック**: `#/capture` で該当ジョブを破棄（DELETE）して再投入

## 無効化（ロールバック）

```yaml
tools:
  zzz_disc:
    enabled: false
```

DB は残る（再有効化で復元可）。完全撤去はテーブル DROP 必要:
```sql
DROP TABLE zzz_extraction_jobs;
DROP TABLE zzz_presets;
DROP TABLE zzz_discs;
DROP TABLE zzz_set_masters;
DROP TABLE zzz_characters;
```
