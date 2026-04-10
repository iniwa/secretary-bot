# 並列 LLM 処理設計

## 背景

複数の Windows PC（SubPC, MainPC）で Ollama を稼働させ、LLM リクエストを並列処理する。
現状は 1台目に応答した Ollama のみを使用し、2台目以降はフォールバック用途でしか機能しない。

## 現状のアーキテクチャ

```
[各種呼び出し元]
  ├── InnerMind.think()        ← ハートビート（3段階の連続LLM呼び出し）
  ├── UnitRouter                ← ユーザーメッセージのルーティング
  ├── Chat Unit                 ← 会話応答
  ├── RSS Processor             ← 記事要約
  ├── PeopleMemory / AiMemory   ← 記憶抽出
  └── Heartbeat (context圧縮)   ← コンテキスト圧縮
        ↓
[LLMRouter.generate()]
        ↓
[OllamaClient] → 1台のみ使用（_available_url）
        ↓
[Gemini] ← フォールバック
```

### 問題点
1. `OllamaClient._available_url` が1つだけ → 1台しか使わない
2. Ollama は1モデル1リクエストずつ処理（GPU 排他） → 2リクエスト同時だと片方が待機
3. InnerMind の3段階思考中にユーザーが話しかけると、InnerMind 完了まで応答がブロック

## 設計方針

### コア変更: OllamaClient のマルチインスタンス化

```
[OllamaClient]
  _available_urls: list[str]        # 利用可能な全URL
  _semaphores: dict[str, Semaphore] # URL → Semaphore(1)
  _active_count: dict[str, int]     # URL → アクティブリクエスト数
```

#### リクエスト分配アルゴリズム

1. **Least-connections**: `_active_count` が最小の URL を選択
2. **Semaphore 制御**: 各 URL に `asyncio.Semaphore(1)` を割り当て
   - Ollama はモデルごとに1リクエストしか並列処理できない
   - Semaphore が空いている URL があればそこへ即座にディスパッチ
   - 全 Semaphore がロック中なら、最初に空く URL を待機
3. **フォールバック**: 全 URL がタイムアウトした場合のみ Gemini へ

#### generate() の新フロー

```python
async def generate(self, prompt, system=None, model=None):
    url = await self._acquire_instance()  # Semaphore待機含む
    try:
        return await self._do_generate(url, prompt, system, model)
    except:
        self._mark_unavailable(url)
        # 他のインスタンスでリトライ
        url2 = await self._acquire_instance(exclude=[url])
        if url2:
            return await self._do_generate(url2, prompt, system, model)
        raise
    finally:
        self._release_instance(url)
```

### LLMRouter 側の変更

LLMRouter 自体は変更不要。OllamaClient の内部改善のみで並列化が実現する。
`LLMRouter.generate()` を複数箇所から同時に呼んでも、OllamaClient が自動的に
空いているインスタンスに分配する。

### ヘルスチェックの改善

```python
async def check_availability(self) -> bool:
    # 全URLを並列チェック → 利用可能なもの全てを _available_urls に
    results = await asyncio.gather(*[self._check_one(url) for url in self.urls])
    self._available_urls = [url for url, ok in zip(self.urls, results) if ok]
    return len(self._available_urls) > 0
```

## 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/llm/ollama_client.py` | マルチインスタンス化・Semaphore制御・least-connections分配 |
| `src/llm/router.py` | `ollama_available` を `bool` → 利用可能数も返せるように（任意） |
| `config.yaml.example` | `llm.ollama_concurrency` 設定追加（各インスタンスの同時実行数、デフォルト1） |

## 並列処理の具体例

### シナリオ: InnerMind 思考中にユーザーが話しかける

**Before（現状）:**
```
SubPC Ollama: [InnerMind Phase1] [InnerMind Phase2] [UserChat] ← 直列
MainPC Ollama: idle
```

**After（並列化後）:**
```
SubPC Ollama:  [InnerMind Phase1] [InnerMind Phase2]
MainPC Ollama: [UserChat]                              ← 空いてるのでここに回る
```

### シナリオ: RSS 要約 + ユーザー会話

**Before:**
```
SubPC Ollama: [RSS要約1] [RSS要約2] [UserChat] ← 直列
```

**After:**
```
SubPC Ollama:  [RSS要約1] [UserChat]
MainPC Ollama: [RSS要約2]
```

## モデル互換性の考慮

- 両 PC で同じモデルが利用可能であることを前提とする
- 特定モデル指定時（`ollama_model` パラメータ）は、そのモデルが存在する PC にのみルーティング
  - `check_availability()` 時に各 URL のモデル一覧をキャッシュ
  - `_acquire_instance(model=...)` でフィルタリング

## アプリケーション層の並列化

インフラ層（OllamaClient マルチインスタンス）だけでも同時リクエストが自動分配されるが、
アプリケーション層で独立タスクを `asyncio.gather()` で並列実行すると効果が最大化する。

### 対象タスクと並列化パターン

#### InnerMind の ContextSource 収集
現状: 各 ContextSource を直列に収集 → LLM 呼び出しも直列
改善: 独立した ContextSource の LLM 呼び出しを `asyncio.gather()` で並列化

```python
# Before（直列）
rss_summary = await rss_source.collect()      # LLM呼び出し含む
stt_summary = await stt_source.collect()       # LLM呼び出し含む
chat_summary = await chat_source.collect()     # LLM呼び出し含む
# → 3回分の直列待ち

# After（並列）
rss_summary, stt_summary, chat_summary = await asyncio.gather(
    rss_source.collect(),
    stt_source.collect(),
    chat_source.collect(),
)
# → 2台なら2回分の待ちで済む（3タスク中2つが同時実行）
```

#### ハートビート処理
現状: `on_heartbeat()` 各ユニットを直列呼び出し
改善: 独立したユニットの heartbeat を並列化

#### 具体的な効果試算（2台構成）

```
現状（直列・1台）:
  [RSS要約] → [STT要約] → [チャット要約] → [Monologue]
  合計: 4T（T = 1回のLLM呼び出し時間）

並列化後（2台）:
  SubPC:  [RSS要約  ] → [Monologue]
  MainPC: [STT要約  ]
  SubPC:             [チャット要約] ↗
  合計: ≈ 2.5T（約40%短縮）

3台構成なら:
  PC-1: [RSS要約  ] → [Monologue]
  PC-2: [STT要約  ]
  PC-3: [チャット要約] ↗
  合計: ≈ 2T（50%短縮）
```

### 並列化の注意点

- **依存関係のあるタスクは直列のまま**: Monologue は全 ContextSource の結果が必要 → gather 後に実行
- **書き込み競合の回避**: 同一リソース（DB の同一テーブル等）への同時書き込みは aiosqlite の WAL モードで安全
- **エラーハンドリング**: `asyncio.gather(return_exceptions=True)` で1つ失敗しても他は継続

## 段階的実装

### Phase 1: OllamaClient マルチインスタンス化
- `_available_url` → `_available_urls` リスト化
- Semaphore による排他制御
- Least-connections 分配
- 変更: `src/llm/ollama_client.py` のみ

### Phase 2: アプリケーション層の並列化
- InnerMind の ContextSource 収集を `asyncio.gather()` で並列化
- heartbeat の on_heartbeat 並列化
- 変更: `src/inner_mind/core.py`, `src/heartbeat.py`

### Phase 3: 高度な最適化
- モデル別ルーティング（特定モデル指定時のフィルタ）
- インスタンス別の成功率・レイテンシ追跡
- 優先度付きキュー（ユーザー会話 > InnerMind > RSS）
- WebGUI での Ollama 状態表示（どのPCが処理中か）
- GPU メモリ使用量に基づく動的ルーティング
