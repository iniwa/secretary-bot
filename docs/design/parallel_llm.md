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

## 段階的実装

### Phase 1（最小限）
- `_available_url` → `_available_urls` リスト化
- Semaphore による排他制御
- Least-connections 分配

### Phase 2（改善）
- モデル別ルーティング（特定モデル指定時のフィルタ）
- インスタンス別の成功率・レイテンシ追跡
- WebGUI での Ollama 状態表示（どのPCが処理中か）

### Phase 3（発展）
- 優先度付きキュー（ユーザー会話 > InnerMind > RSS）
- GPU メモリ使用量に基づく動的ルーティング
