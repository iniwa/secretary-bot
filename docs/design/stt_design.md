# STT（音声テキスト化）設計書

## 概要
Main PCのマイク入力をキャプチャし、いにわの音声をテキスト化して保存・要約する機能。
モノローグやMemoryに活用し、「いにわが何を話していたか」をミミが把握できるようにする。

## 設計判断（確定）

### 音声取得方式
**採用: C — Windows Agent でマイク直接キャプチャ**
- OBS非依存で常時動作可能
- `sounddevice` の共有モードでアプリ競合なし
- ヘッドセット使用のため、マイクにはいにわの音声のみ入力される

> 却下案:
> - A（Discord Bot VC参加）: Bot常駐が必要、discord.pyの音声受信が不安定
> - B（OBS経由）: OBS WebSocketで音声ストリーム取得不可、OBS起動中のみ

### STTエンジン
**採用: kotoba-whisper（kotoba-tech/kotoba-whisper-v2.0）**
- 日本語特化の蒸留モデル（Whisper large-v2ベース）
- Whisper large-v3同等の精度、約6倍高速
- VRAM約2-3GB（float16）、Sub PCのRTX 5060 Ti (16GB) で余裕
- Apache 2.0ライセンス
- HuggingFace Transformers pipeline で利用

### 処理方式
**採用: バッチSTT（一定間隔で蓄積音声を処理）**
- Main PC Agent がマイクを常時監視、VADで発話区間のみバッファリング
- 一定間隔（デフォルト5分）でSub PCに送信しSTT処理
- リアルタイム処理は不要（モノローグ・Memory用途のため遅延許容）

### 発話検出
**採用: webrtcvad（+ 音量閾値の二重フィルタ）**
- webrtcvad: 人声パターンを識別（キーボード打鍵・ゲーム衝撃音を除外）
- 音量閾値: 極小音量を事前カット（webrtcvadの処理負荷軽減）
- 無音が一定秒数続いたら utterance として確定

### 生音声の扱い
**STT完了後に即削除。テキストのみ保存。**

## アーキテクチャ

```
[Main PC Agent: 常時バックグラウンド]
  sounddevice callback → 音声フレーム受信（16kHz, mono, 16bit）
  → 音量閾値チェック（極小音量をカット）
  → webrtcvad で発話判定
  → 発話区間をバッファに蓄積
  → 無音が silence_threshold 秒続いたら utterance 確定

[Main PC Agent: batch_interval 分間隔]
  → バッファ内の utterance を WAV にパック
  → Sub PC POST /stt に送信
  → レスポンスのテキストをローカルに蓄積（タイムスタンプ付き）
  → 送信済み音声データを削除

[Sub PC Agent: POST /stt]
  → 受信した WAV を kotoba-whisper で処理
  → テキストを返却
  → モデルは lazy load（初回リクエストでロード）
  → 一定時間未使用でVRAMから解放

[Pi: ハートビート or 定期タスク]
  → Main PC GET /stt/transcripts?since=<last_ts>
  → 新規テキストを SQLite に保存
  → 蓄積テキストが summary_threshold_chars を超えたら LLM 要約
  → 要約を ChromaDB に保存（collection: stt_summaries）

[Pi: InnerMind ContextSource]
  → 最近の未要約テキスト + 直近の要約をコンテキストに注入
  → 「いにわが最近話していたこと」としてモノローグの思考材料に
```

## ファイル構成

### Main PC Windows Agent
```
windows-agent/
  stt/
    __init__.py
    mic_capture.py     # sounddevice + webrtcvad、発話検出・バッファリング
    stt_client.py      # Sub PC /stt への送信、結果蓄積
```

### Sub PC Windows Agent
```
windows-agent/
  stt/
    __init__.py
    whisper_engine.py  # kotoba-whisper ラッパー（lazy load/unload）
```

### Pi（secretary-bot）
```
src/
  stt/
    __init__.py
    collector.py       # Main PC Agent から transcript 収集
    processor.py       # LLM 要約 + ChromaDB 保存
  inner_mind/
    context_sources/
      stt.py           # ContextSource: STTデータ注入
```

## エンドポイント

### Main PC Agent
| メソッド | パス | 用途 |
|----------|------|------|
| GET | `/stt/status` | キャプチャ状態（動作中/停止/バッファサイズ） |
| GET | `/stt/transcripts?since=<iso_ts>` | 指定時刻以降の文字起こし結果 |
| POST | `/stt/control` | `{"action": "start" \| "stop"}` キャプチャ制御 |

### Sub PC Agent
| メソッド | パス | 用途 |
|----------|------|------|
| POST | `/stt` | WAV音声を受信し、kotoba-whisperでSTT処理 |
| GET | `/stt/model/status` | モデルロード状態・VRAM使用量 |

## DBスキーマ（Pi側 SQLite）

```sql
CREATE TABLE stt_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_seconds REAL,
    collected_at TEXT DEFAULT (datetime('now')),
    summarized INTEGER DEFAULT 0
);

CREATE TABLE stt_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    transcript_ids TEXT NOT NULL,   -- JSON array [1, 2, 3]
    created_at TEXT DEFAULT (datetime('now'))
);
```

### ChromaDB
- Collection: `stt_summaries`
- document: 要約テキスト
- metadata: `{"transcript_ids": "1,2,3", "period_start": "...", "period_end": "..."}`

## config.yaml

```yaml
stt:
  enabled: true
  capture:
    device: null                        # null = デフォルトマイク
    sample_rate: 16000
    vad_aggressiveness: 2               # webrtcvad: 0-3（高いほど厳格）
    volume_threshold_rms: 300           # 音量閾値（RMS値、これ以下は無視）
    silence_threshold_seconds: 1.5      # 無音でutterance区切り
    min_utterance_seconds: 1.0          # 短すぎるutteranceを無視
  batch:
    interval_minutes: 5                 # バッチSTT間隔
  processing:
    summary_threshold_chars: 2000       # この文字数を超えたらLLM要約
  model:
    name: "kotoba-tech/kotoba-whisper-v2.0"
    device: "cuda"
    torch_dtype: "float16"
    unload_after_minutes: 10            # 未使用時にモデルをVRAMから解放
```

WebGUIから以下を変更可能:
- `stt.enabled`（ON/OFF）
- `stt.capture.vad_aggressiveness`
- `stt.capture.volume_threshold_rms`
- `stt.batch.interval_minutes`
- `stt.processing.summary_threshold_chars`

## 特記事項

### 片側音声の扱い
マイク直接キャプチャのため、いにわの発話のみが録れる。
対話相手の発話は含まれないため、文脈が欠ける場合がある。

対策:
- LLM要約プロンプトに「片側のみの発話データ」であることを明記
- Discord チャットログと時系列で突き合わせ可能（InnerMindが両方のContextSourceを持つ）
- 将来的に Discord VC の他参加者の音声も取得可能（要同意）

### アクティビティ判定との連携
- OBS配信中・録画中はSTT処理（Sub PC側）を後回しにできる
- Main PC側のキャプチャ自体は軽量なので常時動作OK
- config で連携ON/OFF可能

### モデルのライフサイクル（Sub PC）
```
[初回 /stt リクエスト]
  → モデルロード（約5-10秒）
  → STT処理
  → unload_after_minutes タイマー開始

[タイマー満了（未使用時間超過）]
  → モデルをVRAMから解放

[次回リクエスト]
  → 再ロード
```

## 依存パッケージ

### Main PC Agent
- `sounddevice` — マイクキャプチャ
- `webrtcvad` — 音声活動検出（軽量C実装）
- `numpy` — 音声データ処理

### Sub PC Agent
- `transformers` — kotoba-whisper
- `torch` — PyTorch（CUDA）
- `accelerate` — モデルロード最適化
- `soundfile` — WAV読み込み

### Pi
- 追加依存なし（Agent経由で全データ取得）

## 未決事項
- [ ] WebGUI でのSTTステータス表示・制御パネル
- [ ] Discord VCとの連動（VC参加中のみキャプチャ等）
- [ ] 他参加者の音声取得（将来・要同意）
