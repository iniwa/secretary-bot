# Input Relay 統合 設計書

## 概要
OBS配信用の入力表示ツール「input-relay」を secretary-bot の Windows Agent でホスト管理する。
input-relay は単体公開リポジトリとして互換性を維持しつつ、git submodule として統合する。

## input-relay とは
キーボード/ゲームパッド入力を OBS のブラウザソースとして表示するツール。
SF6 配信向けに、レバーレス/Hitbox レイアウトと入力履歴表示に対応。

- リポジトリ: `https://github.com/iniwa/input-relay`
- 2PCモード: Main PC (sender) → Sub PC (receiver) にWebSocket転送

## 設計判断（確定）

### 統合方式
**採用: git submodule**
- `windows-agent/tools/input-relay` に配置
- input-relay 側のコード変更は不要（単体互換性維持）
- WebGUI「コード更新」ボタンでサブモジュールも一括更新

### プロセス管理
**採用: Windows Agent のサブプロセスとして起動**
- Agent 起動時（lifespan）に自動起動
- 死活監視ループで自動再起動（10秒間隔チェック）
- stdout/stderr をリングバッファ（500行）でキャプチャ → WebGUI表示

### 管理者権限
- sender（Main PC）: pynput のゲームフックに管理者権限が必要
- receiver（Sub PC）: ファイアウォールルール追加に管理者権限が必要（初回のみ）
- **Windows Agent 自体を管理者権限で起動する**

## ロール判定

Windows Agent はロールに応じて異なるツールを起動する。

```
判定フロー:
1. 環境変数 AGENT_ROLE が設定されていればそれを使用
2. 未設定の場合、自身のIPアドレスから判定:
   - 192.168.1.210 → main
   - 192.168.1.211 → sub
3. どちらにも該当しない → unknown（ツール起動なし）
```

| ロール | 起動するプロセス | コマンド |
|--------|----------------|---------|
| main | input-relay sender | `python sender/input_sender.py` |
| sub | input-relay receiver | `python receiver/input_server.py --http-port 8081` |

## ファイアウォールルール

Agent 起動時に自動設定（既存なら追加しない）。

| ロール | ルール名 | ポート | 用途 |
|--------|---------|--------|------|
| main | InputSender GUI HTTP | 8082 | Sender 設定GUI |
| main | InputSender Monitor WS | 8083 | Sender モニタWS |
| sub | InputDisplay-WS | 8888 | Receiver WebSocket |
| sub | InputDisplay-HTTP | 8081 | Receiver HTTP/設定GUI |

## アーキテクチャ

```
Windows Agent (Main PC, role=main, :7777)
  ├── FastAPI
  ├── lifespan → ToolManager
  │     └── ToolProcess: input-relay sender
  │           ├── subprocess: python sender/input_sender.py
  │           ├── ファイアウォール設定
  │           └── ログキャプチャ（リングバッファ）
  └── 死活監視タスク（10秒間隔）

Windows Agent (Sub PC, role=sub, :7777)
  ├── FastAPI
  ├── lifespan → ToolManager
  │     └── ToolProcess: input-relay receiver
  │           ├── subprocess: python receiver/input_server.py --http-port 8081
  │           ├── ファイアウォール設定
  │           └── ログキャプチャ（リングバッファ）
  └── 死活監視タスク（10秒間隔）

Pi WebGUI
  └── Tools > Input Relay ページ
        ├── ステータス表示（両Agent分）
        ├── ログ表示（sender/receiver 切替）
        ├── 管理GUI リンクボタン
        └── 再起動ボタン
```

## ファイル構成

### Windows Agent
```
windows-agent/
  agent.py                    # lifespan追加、/tools/* エンドポイント追加、ロール判定
  tools/
    tool_manager.py            # ToolProcess, ToolManager, create_tool_manager
    input-relay/               # git submodule (iniwa/input-relay)
```

### Pi側
```
src/web/
  app.py                       # /api/tools/input-relay/* エンドポイント追加
  static/index.html            # Tools > Input Relay ページ追加
```

## Windows Agent エンドポイント（追加分）

| メソッド | パス | 用途 |
|----------|------|------|
| GET | `/tools/input-relay/status` | プロセス状態（running/stopped/pid） |
| GET | `/tools/input-relay/logs?lines=100` | 直近ログ（リングバッファ） |
| POST | `/tools/input-relay/restart` | プロセス再起動 |

## Pi側 WebGUI エンドポイント（追加分）

| メソッド | パス | 用途 |
|----------|------|------|
| GET | `/api/tools/input-relay/status` | 両Agentの状態をまとめて返却 |
| GET | `/api/tools/input-relay/logs/{role}` | Agent経由でログ取得 |
| POST | `/api/tools/input-relay/restart/{role}` | Agent経由で再起動 |

## コード更新フロー

WebGUI「コード更新」ボタン押下時:
```
Pi: git pull → git submodule update --init --recursive
  → 全 Windows Agent に POST /update
    → 各Agent: git pull → git submodule update --init --recursive
```

## 将来の拡張
- ToolManager は汎用設計のため、input-relay 以外のツールも同様に追加可能
- `create_tool_manager()` にロール別のツール登録を追加するだけ
