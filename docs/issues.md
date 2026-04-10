# 改善・追加実装案
## バグ？不具合？
- [x] Maintenanceタブに移動してすぐは「Update Code」といったボタンが押せない。
  - `mount()` でイベントリスナー登録をデータロードより先に実行するよう修正
- [x] すばやくタブを切り替えると `Failed to load **` というエラーが発生する
  - `app.js` にナビゲーション世代カウンター、`maintenance.js` に `_active` フラグ + `unmount()` を追加

## Agent Managementの強化  
現在、Autoにはなっているもののあまり活用できていない。特にMainPC
### 両PCで共通
~~条件に1つでも合致すれば動かさない、というor形式での実装にする。~~
~~また、どの条件に接してるのかをwebGUI上で確認できるようにする。~~
→ 実装済み: `select_agent()` でOR判定、ブロック理由を `_block_reasons` に記録、WebGUI Maintenance タブで表示

- [x] GPU･CPUの使用率をみて判断
  - VictoriaMetrics API経由でCPU/メモリ/GPU使用率をチェック（`agent_pool._is_idle_detailed()`）
  - GPU: `nvidia_smi_exporter` の `nvidia_smi_utilization_gpu_ratio` メトリクスを使用
### MainPC用のカスタマイズ
MainPCでもollamaを稼働させ、効率的に作業したい。
チャットベースでの動作をしながら、ハートビートやmonologue生成を行える、みたいな使い方。基本的にはSubPC優先

- [x] ゲームプレイ中かを判別し、プレイ中はNGに。プレイしていないのであればollamaも利用可能にする
  - `ActivityDetector` + `agent_pool._is_activity_ok()` で実装済み（role="main" 時にゲーム検出）
### SubPC用のカスタマイズ
基本的には動作するような状態にしておきたい。
配信中等、負荷が困る状況でのみ動かさない。

- [x] OBSにて録画中･配信中の時は動かさない
  - `_is_activity_ok()` で OBS配信/録画/リプレイバッファを検出してブロック（role="sub"）
- [x] 「今から◯時間は動かさない」といったボタンの設置
  - WebGUI Maintenance タブに一時停止ボタン（30分/1時間/3時間）を設置
  - `agent_pool.pause_agent()` / `unpause_agent()` + API エンドポイント実装済み


## その他

- [ ] `docs/design/inner_mind_improvements.md` の「5. 未実装：追加 ContextSource 候補」も参照
