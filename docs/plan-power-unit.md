# PC電源管理ユニット（power） 実装計画

## 背景

DiscordやWebGUIから「メインPCを起動して」「PCをシャットダウン」のように自然言語でWindows PCの電源操作を行いたい。

既存資産:
- **WoLツール** (`D:\Git\WoL-tool-Claude`) — Pi上でDocker稼働中（ポート8090）、WoLパケット送信+Ping監視
- **Windows Agent** (`windows-agent/agent.py`) — 各PC上でFastAPI稼働（ポート7777）

## アーキテクチャ

```
ユーザー: 「メインPCを起動して」
  → SkillRouter → PowerUnit
  → action=wake → WoLツールAPI (Pi:8090)

ユーザー: 「PCをシャットダウンして」
  → SkillRouter → PowerUnit
  → action=shutdown → 確認プロンプト → ユーザー承認
  → Windows Agent API (対象PC:7777)
```

| アクション | 対象PCの状態 | ルーティング先 |
|-----------|------------|--------------|
| wake | オフ | WoLツール `POST /api/devices/{id}/wake` |
| shutdown | オン | Windows Agent `POST /shutdown` |
| restart | オン | Windows Agent `POST /restart` |
| status | 問わず | AgentPool健全性 + WoLツールPing |

**重要**: このユニットは `DELEGATE_TO` を使わない。Pi上で直接動作し、httpxで外部APIを呼ぶ（web_searchと同じパターン）。

## 新規ファイル

### `src/units/power.py`

```python
class PowerUnit(BaseUnit):
    UNIT_NAME = "power"
    UNIT_DESCRIPTION = "PCの電源管理。起動（WoL）・シャットダウン・再起動。「メインPCを起動して」「PCをシャットダウン」など。"
```

**LLM抽出プロンプト**で以下を取得:
```json
{"action": "wake|shutdown|restart|status", "target": "pc-main"}
```

`{pc_list}` は `config.yaml` の `windows_agents` から動的生成し、LLMに渡す。

**確認フロー** (shutdown/restart):
- ReminderUnitの `_pending_actions` パターンを踏襲
- `_pending_actions[channel]` に操作内容を保持 → 次メッセージで「はい/いいえ」判定
- 60秒で期限切れ（セッションタイムアウト120秒より短く）
- `session_done = False` で確認待ち、応答後 `True`

**ルーティング実装概要**:

```python
async def _wake_pc(self, target: str) -> str:
    wol_device_id = self._pc_to_wol_device.get(target)
    url = f"{self._wol_url}/api/devices/{wol_device_id}/wake"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url)
        resp.raise_for_status()
    return f"{name}にWoLパケットを送信しました。起動まで1〜2分かかります。"

async def _shutdown_pc(self, target: str, delay: int = 60) -> str:
    agent = self._find_agent(target)
    url = f"http://{agent['host']}:{agent['port']}/shutdown"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"delay": delay},
                                 headers={"X-Agent-Token": self._agent_token})
        resp.raise_for_status()
    return f"{name}を{delay}秒後にシャットダウンします。"
```

**プリフライトチェック**:
- shutdown/restart前: AgentPool経由でエージェント生存確認。不通なら即エラー
- wake前: エージェントが既に生存していたら「既に起動しています」を返す

**キャンセル対応**:
- shutdown/restart実行後の遅延中（デフォルト60秒）に「キャンセル」で `/cancel-shutdown` を呼ぶ

## Windows Agent 追加エンドポイント

`windows-agent/agent.py` に3つ追加:

### `POST /shutdown`
```python
@app.post("/shutdown")
async def shutdown_pc(request: Request):
    _verify_token(request)
    body = await request.json()
    delay = max(body.get("delay", 60), 10)  # 最低10秒
    subprocess.Popen(["shutdown", "/s", "/t", str(delay)],
                     creationflags=subprocess.CREATE_NO_WINDOW)
    return {"status": "scheduled", "delay": delay}
```

### `POST /restart`
```python
@app.post("/restart")
async def restart_pc(request: Request):
    _verify_token(request)
    body = await request.json()
    delay = max(body.get("delay", 60), 10)
    subprocess.Popen(["shutdown", "/r", "/t", str(delay)],
                     creationflags=subprocess.CREATE_NO_WINDOW)
    return {"status": "scheduled", "delay": delay}
```

### `POST /cancel-shutdown`
```python
@app.post("/cancel-shutdown")
async def cancel_shutdown(request: Request):
    _verify_token(request)
    result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
    return {"status": "cancelled" if result.returncode == 0 else "no_pending"}
```

**設計ポイント**:
- `subprocess.Popen` でHTTPレスポンス返却後にOSシャットダウン実行
- `CREATE_NO_WINDOW` でコンソールウィンドウ非表示
- 最低10秒の遅延を強制（安全策）

## デバイスIDマッピング

WoLツールは自動生成ID（ナノ秒タイムスタンプ）、config.yamlはセマンティックID（`pc-main`等）を使用。

**config.yamlに `wol_device_id` を追加**:

```yaml
windows_agents:
  - id: "pc-main"
    name: "メインPC"
    host: "192.168.1.101"
    port: 7777
    priority: 1
    metrics_instance: "192.168.1.101:9182"
    wol_device_id: ""    # WoLツールのデバイスID（GET /api/devices で確認）
```

WoLツールのデバイスIDは `curl http://localhost:8090/api/devices` で確認して手動設定。

## config.yaml 追加項目

```yaml
# WoLツールAPI
wol:
  url: "http://localhost:8090"

# unitsセクションに追加
units:
  power:
    enabled: true
    shutdown_delay: 60  # デフォルト遅延秒数
```

## ユニット登録

`src/units/__init__.py` の `_UNIT_MODULES` に追加:

```python
"power": "src.units.power",
```

## 安全機構

| 機構 | 内容 |
|------|------|
| 確認プロンプト | shutdown/restartは必ず確認を挟む |
| 最低遅延 | サーバー側で10秒未満を拒否 |
| キャンセル | 遅延中は `/cancel-shutdown` で中止可能 |
| プリフライト | 操作前にエージェント生存確認 |
| サーキットブレーカー | 3回連続失敗→60秒クールダウン |
| FlowTracker | CB_CHECK → UNIT_EXEC の標準パターン |

## 実装順序

1. Windows Agent エンドポイント追加 (`/shutdown`, `/restart`, `/cancel-shutdown`)
2. config.yaml に `wol` セクション・`wol_device_id` 追加
3. `src/units/power.py` 作成
4. `src/units/__init__.py` に登録
5. テスト（debug_runner.py にシナリオ追加）

## 検証方法

1. `debug_runner.py` で「メインPCを起動して」→ WoLツールAPIが呼ばれることを確認
2. 「PCをシャットダウンして」→ 確認プロンプト → 「はい」→ Agent `/shutdown` が呼ばれることを確認
3. 「キャンセル」→ `/cancel-shutdown` が呼ばれることを確認
4. エージェント不通時のエラーメッセージ確認
5. 既に起動中のPCへのwakeで適切なメッセージ確認
