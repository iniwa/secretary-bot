# Multi-PC Activity Detection 設計書

`docs/issues.md` の「Input-Relay から操作状況を読み取り、Main PC と Sub PC のどちらを触っているのかを認識」要件の実装設計。Sub PC で VS Code / Unity 等を使う時間も記録する。

## 前提

- Input-Relay は **常時稼働**する運用（Main PC sender、Sub PC receiver）。
- Input-Relay 本体のリポジトリは **`C:/Users/yamatoishida/Documents/git/input-relay`**（別リポ）。`secretary-bot` 側には `windows-agent/tools/input-relay/` として git submodule で取り込まれている。sender/receiver の変更はすべてこの外部リポで行い、submodule 更新で反映する。
- Input-Relay の sender には **Scroll Lock トグルで切り替わる `remote.mode`** が既に存在し、`GET http://localhost:8082/api/status` で `remote_mode` が公開されている。
- リモートモード中でもゲームパッド入力は Main PC のゲーム（例: SF6）にそのまま入る。「SF6 をコントローラで遊びながら Sub PC をキーマウで触る」ような **両 PC 同時操作**は日常的に発生するので、単一 `active_pc` では表現しない。

## 設計の要点

### 情報源: 入力デバイス別の最終イベント時刻

Input-Relay sender が、入力ソース別に最終イベント時刻を保持する:

| フィールド | 意味 |
|---|---|
| `last_kbd_mouse_ts` | キーボード/マウスの最新イベント時刻（Unix epoch 秒） |
| `last_gamepad_ts` | ゲームパッドの最新イベント時刻 |
| `remote_mode` | 現在リモート（Main の kbd/mouse を Sub に転送中）か |

### どの PC がアクティブかの判定

| 条件 | アクティブ PC |
|---|---|
| `last_gamepad_ts` が `idle_timeout_seconds` 以内 | **main**（ゲームパッドは物理的に Main 接続） |
| `remote_mode == False` かつ `last_kbd_mouse_ts` が `idle_timeout_seconds` 以内 | **main** |
| `remote_mode == True` かつ `last_kbd_mouse_ts` が `idle_timeout_seconds` 以内 | **sub** |

結果は単一値でなく **`active_pcs: list[str]`**（`["main"]` / `["sub"]` / `["main","sub"]` / `[]`）として扱う。`idle_timeout_seconds` は `config.yaml:activity.idle_timeout_seconds`（既定 120 秒）。

状態パターン:
- `["main"]` → 通常デスクトップ or ゲームパッドのみ
- `["sub"]` → リモートモードで Sub 操作
- `["main", "sub"]` → **SF6 + リモート Sub 同時操作** ← 今回の重要ケース
- `[]` → idle

## Phase A — Sub PC foreground 記録

Input-Relay 無関係。Sub PC のフォアグラウンドを記録するだけで「Sub PC で VSCode / Unity を使う時間」が見える。

### A-1. `windows-agent/agent.py` の `/activity` 拡張

現状 Main role のみ `get_game_activity()` を呼んでいる（`agent.py:260-262`）。Sub role でも `get_game_activity()` を呼ぶ。`game_detector.py:get_activity()` は OS 依存しない汎用で、Sub でも foreground / is_fullscreen が取れる（game は通常ヒットしない）。

```python
if role == "main":
    result.update(get_game_activity())
elif role == "sub":
    if _obs_manager:
        result.update(_obs_manager.get_status())
    else:
        result.update({"obs_connected": False, "obs_streaming": False, ...})
    # 追加: Sub でも foreground 取得
    fg = get_game_activity()
    result["foreground_process"] = fg.get("foreground_process")
    result["is_fullscreen"] = fg.get("is_fullscreen")
```

### A-2. DB スキーマ migration（`src/database.py`）

`_SCHEMA_VERSION` を **27** にインクリメントし、下記を追加:

```python
27: [
    "ALTER TABLE activity_samples ADD COLUMN pc TEXT NOT NULL DEFAULT 'main'",
    "ALTER TABLE foreground_sessions ADD COLUMN pc TEXT NOT NULL DEFAULT 'main'",
    "ALTER TABLE activity_samples ADD COLUMN active_pcs TEXT",  # Phase B で使う。カラムは先に作る
    "CREATE INDEX IF NOT EXISTS idx_foreground_sessions_pc_start ON foreground_sessions(pc, start_at)",
    "CREATE INDEX IF NOT EXISTS idx_activity_samples_pc_ts ON activity_samples(pc, ts)",
],
```

`game_sessions` は現状 Main のみ実用的なので `pc` は追加しない（必要になったら別 migration）。

### A-3. `src/activity/collector.py` を Main/Sub 両対応に

現状は Main PC の 1 つだけ poll（`collector.py:46-50`）。変更点:

1. 内部状態を `dict[pc, ...]` 化:
   ```python
   self._cur_game: dict[str, str | None] = {"main": None, "sub": None}
   self._cur_game_session_id: dict[str, int | None] = {"main": None, "sub": None}
   self._cur_fg: dict[str, str | None] = {"main": None, "sub": None}
   self._cur_fg_session_id: dict[str, int | None] = {"main": None, "sub": None}
   self._consecutive_failures: dict[str, int] = {"main": 0, "sub": 0}
   self._last_alive_ts: dict[str, str | None] = {"main": None, "sub": None}
   ```

2. `poll()` 内で Main / Sub を並列取得:
   ```python
   main_agent = next((a for a in agents if a.get("role") == "main"), None)
   sub_agent  = next((a for a in agents if a.get("role") == "sub"), None)
   main_data, sub_data = await asyncio.gather(
       monitor.fetch(main_agent) if main_agent else _noop(),
       monitor.fetch(sub_agent)  if sub_agent  else _noop(),
       return_exceptions=True,
   )
   ```
   それぞれについて、既存の `_open_*_session` / `_close_*_session` ロジックを `pc` 付きで回す。`_open_fg_session` / `_close_fg_session` は `pc` 引数を追加し INSERT 時に `pc` カラムに保存。

3. `cleanup_old_samples` / `restore_open_sessions` も `pc` 別に処理（`_handle` を pc ループで呼ぶ）。

4. `game_sessions` は Main のみ。Sub の `data["game"]` は無視する（実害なし）。

### A-4. `src/activity/detector.py` の `get_status()` 拡張

戻り値を下記のように拡張:

```python
{
  "main": {"foreground_process": str|None, "is_fullscreen": bool, "game": str|None},
  "sub":  {"foreground_process": str|None, "is_fullscreen": bool},
  # 旧互換キーも当面残す（呼び出し側が広範囲のため）:
  "gaming": {...},
  "foreground_process": main.foreground_process,
  "is_fullscreen": main.is_fullscreen,
  "obs_streaming": ..., "obs_recording": ..., ...,
  "blocked": ..., "block_reason": ...,
}
```

## Phase B — Input-Relay から active_pcs 判定

### B-1. Input-Relay sender の変更（外部リポ `C:/Users/yamatoishida/Documents/git/input-relay`）

対象ファイル: `sender/input_sender.py`

1. モジュールグローバルに追加:
   ```python
   _last_kbd_mouse_ts: float = 0.0
   _last_gamepad_ts:   float = 0.0
   _input_ts_lock = threading.Lock()
   ```

2. `_emit()` 内で source 別にタイムスタンプ更新。**ただし `_emit` は現状文字列しか受け取っていない**ので、キャプチャ側（`on_press` / `on_release` / `on_mouse_*` / `_on_raw_mouse_delta` / gamepad コールバック）からの呼び出し箇所で分岐する方が安全。具体的には:
   - `on_press` / `on_release` / `on_mouse_click` / `on_mouse_scroll` / `_on_raw_mouse_delta`: 呼び出し直前に `_touch_kbd_mouse()` を呼ぶ
   - `gamepad.py` から `_emit` に渡るパス: `_emit` をラップした `_emit_gamepad` を gamepad 側に渡し、そこで `_touch_gamepad()` を呼ぶ

   ```python
   def _touch_kbd_mouse():
       with _input_ts_lock:
           global _last_kbd_mouse_ts
           _last_kbd_mouse_ts = time.time()

   def _touch_gamepad():
       with _input_ts_lock:
           global _last_gamepad_ts
           _last_gamepad_ts = time.time()
   ```

3. `/api/status` に 2 フィールド追加:
   ```python
   elif path == "/api/status":
       with _input_ts_lock:
           kbd_ts = _last_kbd_mouse_ts
           gp_ts  = _last_gamepad_ts
       self._send_json({
           "ws_status": ws_status,
           "host": config.get("host", ""),
           "port": config.get("port", 8888),
           "selected_controller": _gamepad.selected_id() if _gamepad else 0,
           "remote_mode": remote.mode,
           "last_kbd_mouse_ts": kbd_ts,   # 追加（未観測は 0.0）
           "last_gamepad_ts":   gp_ts,    # 追加
           "server_time":       time.time(),  # 追加（クライアント側で時差吸収用）
       })
   ```

4. 既存 `/api/status` 利用者（sender_gui.html）への影響なし（キー追加だけ）。

5. 変更後、外部リポ側でコミット。secretary-bot 側は `git submodule update --remote windows-agent/tools/input-relay` でポインタ更新。

### B-2. `windows-agent/agent.py` から sender 状態を取得

Main role の `/activity` 内で `http://localhost:8082/api/status` を短 timeout（0.5s）で取得し、レスポンスにマージ:

```python
def _fetch_input_relay_status() -> dict:
    """ローカル sender の /api/status を取りに行く。失敗時は null。"""
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen("http://127.0.0.1:8082/api/status", timeout=0.5) as r:
            if r.status == 200:
                return _json.loads(r.read())
    except Exception:
        return None
    return None

@app.get("/activity")
async def activity(request: Request):
    ...
    if role == "main":
        result.update(get_game_activity())
        ir = _fetch_input_relay_status()
        if ir is not None:
            result["input_relay"] = {
                "remote_mode":       ir.get("remote_mode", False),
                "ws_status":         ir.get("ws_status", "disconnected"),
                "last_kbd_mouse_ts": ir.get("last_kbd_mouse_ts", 0.0),
                "last_gamepad_ts":   ir.get("last_gamepad_ts", 0.0),
                "server_time":       ir.get("server_time"),
            }
    ...
```

Sub role 側も必要なら receiver の `/api/remote_control` を叩いて `{enabled}` を確認できるが、Main sender の `remote_mode` が正（sender→receiver に通知済み）なので Phase B では省略。

### B-3. `src/activity/detector.py` に active_pcs 判定を追加

```python
def _evaluate_active_pcs(self, main_data: dict, timeout_sec: int) -> list[str]:
    ir = main_data.get("input_relay") or {}
    now = ir.get("server_time") or time.time()
    kbd_ts = ir.get("last_kbd_mouse_ts", 0.0) or 0.0
    gp_ts  = ir.get("last_gamepad_ts", 0.0) or 0.0
    remote = bool(ir.get("remote_mode", False))

    kbd_fresh = (now - kbd_ts) <= timeout_sec if kbd_ts else False
    gp_fresh  = (now - gp_ts)  <= timeout_sec if gp_ts  else False

    pcs: list[str] = []
    if gp_fresh:
        pcs.append("main")
    if kbd_fresh:
        pcs.append("sub" if remote else "main")
    # 重複除去しつつ順序維持
    seen = set()
    return [p for p in pcs if not (p in seen or seen.add(p))]
```

`get_status()` の戻り値に `"active_pcs": [...]` と `"input_relay": {...}` を含める。`timeout_sec` は `config.yaml:activity.idle_timeout_seconds`（既定 120）。

Input-Relay が起動していない（`input_relay == None`）場合は `active_pcs = []` とせず、従来互換で `["main"]`（Main PC agent が alive なら）でフォールバックしてもよい。ただし精度が落ちるのでログに WARN を 1 回出す程度にする。

### B-4. `src/activity/collector.py` で active_pcs を記録

Main PC `/activity` レスポンスに `input_relay` が入ってくるので、`poll()` 内で `active_pcs` を評価して `activity_samples.active_pcs` に CSV 形式で保存:

```python
active_pcs = self.bot.activity_detector._evaluate_active_pcs(
    {"input_relay": main_data.get("input_relay")},
    timeout_sec=self._poll_interval * 2,  # poll 間隔の 2 倍を既定タイムアウト
)
await self.bot.database.execute(
    "INSERT INTO activity_samples (ts, game, foreground_process, is_fullscreen, pc, active_pcs) VALUES (?, ?, ?, ?, ?, ?)",
    (ts, game, fg, is_fullscreen, "main", ",".join(active_pcs) if active_pcs else None),
)
```

Sub 側のサンプルは `pc="sub"` で同じく INSERT。`active_pcs` は Main のサンプルにだけ書く（一次情報源は Main sender）。Sub サンプルの `active_pcs` は NULL でよい。

## Phase C — WebGUI / 集計 / InnerMind 連携

### C-1. `src/web/app.py` の `/api/activity` 拡張

`ActivityDetector.get_status()` の新しい戻り値をそのままフロントに流す。シリアライズでリストを落とさないこと。

### C-2. `src/web/static/js/pages/activity.js`

1. 現在状態カード: Main / Sub の foreground を 2 列で並べる。`active_pcs` のバッジを表示（`["main","sub"]` なら両方ハイライト）。
2. タイムライン: Main active / Sub active の帯を 2 本並列表示。両方点灯する時間帯が「同時操作」として直感的に見える。
3. foreground セッション一覧: `pc` カラムを列追加し、Main / Sub でフィルタ可能にする。

### C-3. `src/activity/daily_summary.py`

foreground 集計を `pc` でグルーピング:

```sql
SELECT pc, process_name, SUM(COALESCE(duration_sec, 0)) AS sec, during_game
FROM foreground_sessions
WHERE start_at BETWEEN ? AND ? AND end_at IS NOT NULL
GROUP BY pc, process_name, during_game
HAVING sec > 0 ORDER BY sec DESC
```

プロンプトも PC 別に並べる。「同時操作」の算出は別途:

```sql
-- 両 PC アクティブだった時間（Main サンプルの active_pcs に 'sub' が含まれる数 × poll間隔）
SELECT COUNT(*) FROM activity_samples
WHERE pc='main' AND ts BETWEEN ? AND ?
  AND active_pcs LIKE '%main%' AND active_pcs LIKE '%sub%'
```

これを「SF6 中に Sub PC で VSCode 2h15m」のような文脈生成に活かす。

### C-4. `src/inner_mind/context_sources/activity.py`

現状 Main のフォアグラウンドと直近ゲームのみ返している。拡張:

```python
return {
    "current_game": cur_game_main,
    "current_foreground": {"main": cur_fg_main, "sub": cur_fg_sub},
    "active_pcs": await self._fetch_latest_active_pcs(),  # 直近サンプルから
    "recent_games": games,
    "recent_foreground_main": fg_top_main,
    "recent_foreground_sub":  fg_top_sub,
}
```

`format_for_prompt` もそれに合わせて「いにわのPC活動」セクションを Main / Sub 並列で出す。両方アクティブなら「（Main でゲーム中に Sub で作業）」と明示すると LLM が正しく文脈化できる。

### C-5. `src/activity/habit_detector.py`

ゲームは引き続き Main のみの想定なので大きな変更は不要。必要なら `game_sessions` 集計は現状のままで、将来 Sub でのゲーム記録が必要になった時点で `pc` カラムを追加。

## 設定追加（`config.yaml.example`）

```yaml
activity:
  enabled: true
  poll_interval_seconds: 60
  sample_retention_days: 7
  unreachable_close_polls: 3
  idle_timeout_seconds: 120      # 追加: active_pcs 判定のしきい値
  input_relay:                    # 追加
    sender_url: "http://127.0.0.1:8082"  # Main Agent からローカル sender を叩く
    timeout_seconds: 0.5
```

## 実装順の推奨

A → B → C の順で 1 PR ずつ切るのが安全:

1. **PR 1 (Phase A)**: Sub foreground 記録のみ。Input-Relay 側変更なし。DB migration 27 で `pc` / `active_pcs` カラムを先に全部作っておくと Phase B の migration が不要になる（`active_pcs` は Phase A 段階では常に NULL で書かれる）。
2. **PR 2 (Phase B)**: Input-Relay 外部リポで `/api/status` 拡張 → コミット → secretary-bot 側で submodule ポインタ更新 + Agent / detector / collector の active_pcs 連携。
3. **PR 3 (Phase C)**: WebGUI / daily_summary / InnerMind。

## 動作確認チェックリスト

- [ ] Phase A: Sub PC 側 `/activity` が `foreground_process` を返す（`curl http://192.168.1.211:7777/activity -H "X-Agent-Token: ..."`）
- [ ] Phase A: `foreground_sessions` テーブルに `pc='sub'` の行が記録されている
- [ ] Phase A: Main / Sub 片方の Agent が落ちても他方は記録継続（`_consecutive_failures` 独立）
- [ ] Phase B: `http://localhost:8082/api/status` が `remote_mode` / `last_kbd_mouse_ts` / `last_gamepad_ts` を返す
- [ ] Phase B: Scroll Lock 押下 → 5 秒以内に `/api/activity` の `active_pcs` が `["sub"]` になる
- [ ] Phase B: コントローラでゲーム中に Scroll Lock ON → `active_pcs == ["main", "sub"]`
- [ ] Phase B: 無操作 2 分以上 → `active_pcs == []`
- [ ] Phase C: WebGUI タイムラインで両 PC 同時アクティブ区間が視認できる
- [ ] Phase C: 日次サマリに「両 PC 同時操作時間」が含まれる

## 関連ファイル（ショートカット）

- `docs/issues.md` — 元の要件
- `docs/design/inner_mind_redesign.md` — 活動検出全般の上位設計
- `src/activity/{collector,detector,agent_monitor}.py`
- `src/database.py`
- `src/inner_mind/context_sources/activity.py`
- `src/activity/{daily_summary,habit_detector}.py`
- `src/web/app.py`, `src/web/static/js/pages/activity.js`
- `windows-agent/agent.py`, `windows-agent/activity/game_detector.py`
- `C:/Users/yamatoishida/Documents/git/input-relay/sender/input_sender.py` — 外部リポ
