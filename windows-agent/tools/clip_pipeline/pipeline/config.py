# デフォルト設定（WebGUIで上書き可能）

# モデル設定
WHISPER_MODEL_TEST = "base"
WHISPER_MODEL_NORMAL = "large-v3"
OLLAMA_MODEL_TEST = "gemma4:e4b"
OLLAMA_MODEL_NORMAL = "gemma4"
OLLAMA_URL = "http://localhost:11434"

# 負荷設定
SLEEP_BETWEEN_STEPS = 2       # 処理ステップ間のスリープ秒数
OLLAMA_REQUEST_DELAY = 1      # Ollamaへのリクエスト間隔（秒）

# クリップ設定
MIN_CLIP_SEC = 30
MAX_CLIP_SEC = 180
HIGHLIGHT_TOP_N = 0          # 0 = 無制限

# 音声前処理設定
MIC_TRACK_INDEX = 1           # OBS録画のマイクトラック番号 (0始まり)
DEMUCS_MODEL = "htdemucs"    # Demucsモデル名

# 音声分析設定
AUDIO_SEGMENT_SEC = 5         # 音声特徴量の分析単位（秒）

# チャンク設定（LLMに送るセグメント数）
LLM_CHUNK_SIZE = 50

# 分散処理設定
WORKERS = [
    {"name": "Sub PC", "url": "http://192.168.1.211:8766"},
    {"name": "Main PC", "url": "http://192.168.1.210:8766"},
]
WORKER_POLL_INTERVAL = 1.0    # ワーカーポーリング間隔（秒）
WORKER_HEALTH_TIMEOUT = 3.0   # ヘルスチェックタイムアウト（秒）
WORKER_CRASH_RETRY = 30       # クラッシュ時のリトライ回数（再起動待ち）
