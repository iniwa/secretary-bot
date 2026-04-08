# アクティビティ判定 設計書

## 概要
ユーザーのアクティビティ状態を複数ソースから判定し、重い処理（LLM要約等）の実行可否を制御する。
判断軸は「Sub PC（Ollama稼働PC）のリソースが空いているか」。

## 2PC構成

| PC | IP | ロール | 役割 |
|----|-----|--------|------|
| Main PC | 192.168.1.210 | `main` | ゲーム |
| Sub PC | 192.168.1.211 | `sub` | OBS + Ollama |

## 判定ソースと優先度

| 優先度 | ソース | 検出方法 | デフォルト |
|--------|--------|---------|-----------|
| 1 | OBS 配信中 | Windows Agent (Sub) `/activity` | **ブロック** |
| 2 | OBS 録画中 | Windows Agent (Sub) `/activity` | **ブロック** |
| 3 | OBS リプレイバッファ | Windows Agent (Sub) `/activity` | 許可 |
| 4 | Main PCでゲーム中 | Windows Agent (Main) `/activity` | 許可 |
| 5 | Discord VC接続中 | Discord Bot | 許可 |

- 全項目 config.yaml で ON/OFF 切替可能（WebGUIからも変更可能）
- OBS WebSocket 接続失敗時 → **許可扱い**（OBS未起動 = 空いている）

## Windows Agent `/activity` エンドポイント

各PCのWindows Agentがロールに応じた情報を返す。

### Main PC (role=main)
ゲーム検出を担当。2pc-obs の `main_pc/agent.py` から移植。

```
GET /activity
→ {
    "role": "main",
    "game": "VALORANT",
    "foreground_process": "VALORANT-Win64-Shipping.exe",
    "is_fullscreen": true
  }
```

#### ゲーム検出方式（ハイブリッド）
1. `config/game_processes.json` のプロセスリストでマッチ → 確実な検出
2. マッチしない場合 → フルスクリーン検出で補助判定
3. フルスクリーンかつ未知プロセスの場合、ログに記録（手動で game_processes.json に追加を促す）

移植元:
- `2pc-obs/main_pc/agent.py` → `detect_game()`, `get_foreground_process_name()`
- `2pc-obs/config/game_processes.json` → `config/game_processes.json`
- `2pc-obs/config/game_groups.json` → `config/game_groups.json`

### Sub PC (role=sub)
OBS状態取得を担当。OBS WebSocket v5 に接続。

```
GET /activity
→ {
    "role": "sub",
    "obs_connected": true,
    "obs_streaming": false,
    "obs_recording": true,
    "obs_replay_buffer": true
  }
```

移植元:
- `2pc-obs/sub_pc/agent.py` → `connect_obs()`, `_obs_alive()`

### ロール判定
config.yaml の `windows_agents` でIPとロールを定義（WebGUIから変更可能）。
Windows Agent起動時に自身のIPから自動判定、または環境変数 `AGENT_ROLE` で明示指定。

## Windows Agent 構造（統合後）

```
windows-agent/
  agent.py              # FastAPI（既存 + /activity エンドポイント）
  activity/
    __init__.py
    game_detector.py     # ゲームプロセス検出（2pc-obs由来）
    obs_monitor.py       # OBS WebSocket接続・状態取得（2pc-obs由来）
  requirements.txt       # psutil, obsws-python, pywin32 追加
  start_agent.bat
```

## Pi側アーキテクチャ

```
src/
  activity/
    __init__.py
    detector.py          # ActivityDetector: 各ソースを統合判定
    agent_monitor.py     # Windows Agent /activity 問い合わせ
    discord_monitor.py   # Discord VC 状態取得
```

OBS WebSocketにはPiから直接接続しない。Sub PCのWindows Agentが仲介する。
→ Pi側の依存に `obsws-python` が不要（arm64互換の心配なし）。

### ActivityDetector

```python
class ActivityDetector:
    async def is_blocked() -> bool:
        """重い処理をブロックすべきかを返す"""

    async def get_status() -> dict:
        """全ソースの現在状態を返す（WebGUI表示用）"""
        # {
        #   "obs_streaming": False,
        #   "obs_recording": True,
        #   "obs_replay_buffer": True,
        #   "gaming": {"active": True, "game": "VALORANT"},
        #   "discord_vc": False,
        #   "blocked": True,
        #   "block_reason": "OBS録画中"
        # }

    async def get_current_game() -> str | None:
        """現在プレイ中のゲーム名を返す
        OBSファイル整理等、他機能からも利用される公開API"""
```

### 利用側

```python
# ハートビート・RSSダイジェスト等から
if await self.bot.activity.is_blocked():
    log.info("ユーザーアクティブのためスキップ")
    return

# OBSファイル整理から（間借り）
game = await self.bot.activity.get_current_game()
```

## config.yaml 構造

```yaml
activity:
  enabled: true
  block_rules:
    obs_streaming: true       # 配信中 → ブロック
    obs_recording: true       # 録画中 → ブロック
    obs_replay_buffer: false  # リプレイバッファ → 許可
    gaming_on_main: false     # MainPCゲーム中 → 許可（Ollama on Subは動かしてOK）
    discord_vc: false         # VC接続中 → 許可

windows_agents:
  - host: "192.168.1.210"
    port: 7777
    role: main
    priority: 1
  - host: "192.168.1.211"
    port: 7777
    role: sub
    priority: 2
```

OBS WebSocketパスワードは `.env`（`OBS_WEBSOCKET_PASSWORD`）。

### WebGUI設定
以下の項目をWebGUIから変更可能にする：
- `windows_agents` のIP・ポート・ロール設定
- `block_rules` の各項目ON/OFF

## 処理フロー

```
[ハートビート / RSSダイジェスト / モノローグ]
  → ActivityDetector.is_blocked()
    → agent_monitor: Sub PC /activity → OBS状態取得
    → agent_monitor: Main PC /activity → ゲーム検出
    → discord_monitor: VC接続状態取得
    → block_rules に照らして総合判定
    → True(ブロック) / False(許可) を返す
```

## 依存パッケージ

### Windows Agent側（両PC共通）
- `psutil` — プロセス情報取得
- `pywin32` — フォアグラウンドウィンドウ・フルスクリーン検出

### Windows Agent側（Sub PCのみ使用）
- `obsws-python` — OBS WebSocket v5 クライアント

### Pi側
- 追加依存なし（Windows Agent経由で全情報取得）

## ユーザー作業リスト

1. **`.env` に追加**: `OBS_WEBSOCKET_PASSWORD=（OBSで設定したパスワード）`
2. **Sub PC**: `pip install obsws-python psutil pywin32`
3. **Main PC**: `pip install psutil pywin32`
4. **両PCの Windows Agent を再起動**

## 未決事項
- [ ] WebGUI でのアクティビティ状態表示
- [ ] game_editor.py のWebGUI統合（将来）
- [ ] ゲーム検出の未知プロセス自動学習（将来）
