# WebGUI API リファレンス

> 最終更新: 2026-04-23
> 実装: `src/web/app.py`（アプリ組み立て）+ `src/web/routes/*.py`（カテゴリ別ルート）

## 概要

WebGUI は `FastAPI` 1 アプリで、ルートは `src/web/routes/` 配下のカテゴリ別モジュールに分割されている。
`src/web/app.py` の `create_web_app(bot)` が全ルートを `register_all_routes()` でマウントし、
その後 `/tools/zzz-disc/*`（`src/tools/zzz_disc`）、`/tools/image-gen/*`（`src/tools/image_gen_console`）の
同居ツールを登録する。

- 認証: Basic 認証（`_context.WebContext.verify`）。`/health` と `/api/flow/stream` など一部 SSE は認証なし。
- 静的配信: `/static/*` と同居ツールの `/static/*` には `Cache-Control: no-cache, must-revalidate` を
  付与するミドルウェア（`app.py` 内）。`index.html` は md5 ベースの `?v=` を自動付与する。
- PWA: `/service-worker.js`, `/manifest.webmanifest` をルート配下で配信。

エンドポイントは非常に多いので、以下は「カテゴリと代表パス」のみ列挙する。
完全なシグネチャは `src/web/routes/<name>.py` を参照のこと（ファイル名＝カテゴリ名と対応）。

---

## 1. Core & Health ― `routes/core.py`

| Method | Path | 説明 |
|--------|------|------|
| GET | `/health` | ヘルスチェック（認証不要） |
| POST | `/api/chat` | WebGUI チャット送信（`message`, `reply_unit`任意） |
| GET | `/api/logs` | 会話ログ（`limit/offset/keyword/channel/bot_only`） |
| GET | `/api/status` | 総合ステータス（version/uptime/ollama/agents/db/memory） |
| GET | `/api/version` | コミットハッシュ |

## 2. System / Maintenance ― `routes/system.py`

Ollama・Agent・コード更新・GPU ステータス系。

- `POST /api/ollama-recheck` / `GET /api/ollama-models` / `GET /api/ollama-status`
- `POST /api/delegation-mode`（Agent 委任モード "allow"/"deny"/"auto"）
- `POST /api/agents/{agent_id}/pause` / `DELETE /api/agents/{agent_id}/pause`
- `POST /api/update-code` / `POST /api/restart`
- `POST /api/agents/restart-all` / `POST /api/agents/{agent_id}/restart`
- `GET /api/agents/versions`
- `GET /api/gpu-status/live` / `GET /api/gpu-status/logs` / `GET /api/gpu-status/ollama-server-log`

## 3. Config ― `routes/config.py`

LLM・Gemini・ペルソナ・設定系全般。

- `GET|POST /api/llm-config`（`ollama_model`, `ollama_timeout`, `gemini_model`, `unit_models` …）
- `GET /api/debug/llm-state`
- `GET|POST /api/gemini-config`, `GET|POST /api/unit-gemini`
- `GET|POST /api/heartbeat-config`
- `GET|POST /api/chat-config`, `GET|POST /api/rakuten-config`, `GET /api/debug/rakuten-search`
- `GET|POST /api/persona`
- `GET|POST /api/settings`（汎用 Key-Value）
- `GET /api/logs/llm`, `GET /api/debug/heartbeat-logs`

## 4. Units CRUD ― `routes/units.py`

Reminder / Todo / Memo / Weather / Timer / Unit 状態。

- Reminder: `GET/PUT/DELETE/POST /done` — `/api/units/reminders[/{rid}]`
- Todo: `GET/PUT/DELETE/POST /done` — `/api/units/todos[/{tid}]`
- Memo: `GET/PUT/DELETE/POST /append` — `/api/units/memos[/{mid}]`
- Weather: `GET/PUT/DELETE/POST /toggle` — `/api/units/weather[/{wid}]`
- Timer: `GET /api/units/timers`
- Loaded units: `GET /api/units/loaded`

## 5. Memory ― `routes/memory.py`

ChromaDB 検索・削除。`collection` は `ai_memory` / `people_memory` / `conversation_log` 等。

- `GET /api/memory/{collection}`（`limit`, `offset`）
- `GET /api/memory/{collection}/search`
- `DELETE /api/memory/{collection}/{doc_id}`

## 6. Inner Mind & Pending ― `routes/inner_mind.py`

- Monologue/Status/Context: `GET /api/monologue`, `GET /api/inner-mind/status`, `GET /api/inner-mind/context`
- Settings: `GET|POST /api/inner-mind/settings`
- Dispatches/Autonomy: `GET /api/inner-mind/dispatches`, `GET|POST /api/inner-mind/autonomy`, `GET /api/inner-mind/autonomy/units`
- Pending Actions（承認待ちキュー）:
  - `GET /api/pending`, `GET /api/pending/unread-count`, `GET /api/pending/{pid}`
  - `POST /api/pending/{pid}/approve` / `reject` / `cancel`

## 7. Flow ― `routes/flow.py`

- `GET /api/flow/state` — `flow_tracker` の現在状態
- `GET /api/flow/stream` — SSE（リアルタイム更新）

## 8. Activity ― `routes/activity.py`

ゲーム・OBS・PC アクティビティ統合ダッシュボード。

- `GET /api/activity/main`（Main PC の生状態）
- `GET /api/activity/stats` / `summary` / `daily` / `sessions`
- `GET /api/activity/diary`（当日）/ `GET /api/activity/diary/list`
- `POST /api/activity/diary/regenerate`

## 9. Docker Log Monitor ― `routes/docker_monitor.py`

- `GET /api/docker-monitor/errors`
- `POST /api/docker-monitor/errors/{error_id}/dismiss` / `dismiss-all`
- `DELETE /api/docker-monitor/errors/{error_id}`
- `GET|POST /api/docker-monitor/exclusions`, `DELETE /api/docker-monitor/exclusions/{exc_id}`

## 10. RSS ― `routes/rss.py`

- `GET|POST /api/rss/feeds`, `DELETE /api/rss/feeds/{feed_id}`
- `POST /api/rss/feeds/{feed_id}/toggle`
- `GET /api/rss/articles`, `POST /api/rss/fetch`
- `POST /api/rss/articles/{article_id}/feedback`

## 11. STT ― `routes/stt.py`

- `GET|POST /api/stt-config`
- `GET /api/stt/status`, `GET /api/stt/devices`, `POST /api/stt/control`
- `GET /api/stt/model/status`, `GET /api/stt/transcripts`, `GET /api/stt/summaries`
- `POST /api/stt/resummarize`

## 12. OBS ― `routes/obs.py`

- `GET|POST /api/obs/games`（game_processes.json / game_groups.json の管理）
- `GET /api/obs/status`, `GET /api/obs/logs`

## 13. Input Relay ― `routes/input_relay.py`

Windows Agent ツール `input-relay` の操作。

- `POST /api/tools/input-relay/update`
- `GET /api/tools/input-relay/status`
- `GET /api/tools/input-relay/logs/{role}`
- `POST /api/tools/input-relay/{start|stop|restart}/{role}`

## 14. Image Generation ― `routes/image_gen.py`（最大）

ジョブ投入・ギャラリー・ワークフロー・プリセット・プロンプト・Agent 管理を網羅。
主要グループのみ抜粋（実装は 64KB 超）:

- 旧API（互換）: `POST /api/image/generate`, `GET /api/image/jobs[/stream|/{id}]`, `POST /cancel`
- ジョブ（新パス）: `POST /api/generation/submit`, `GET /api/generation/jobs[/stream|/{id}]`,
  `POST /api/generation/jobs/{id}/cancel`, `DELETE` / bulk-delete / bulk-favorite / bulk-tags
- ギャラリー: `GET /api/image/gallery`, `GET /api/generation/gallery`,
  `GET /api/generation/gallery/similar/{job_id}`, `GET /api/generation/gallery/tags`
- セクション: `GET|POST|PATCH|DELETE /api/generation/section-categories[/{id}]`,
  `/api/generation/sections[/{id}]`, `/api/generation/section-presets[/{id}]`,
  `POST /api/generation/compose-preview`
- Wildcards: `GET /api/generation/wildcards[/bulk|/{name}]`, `POST /expand`, `PUT|DELETE /{name}`
- Checkpoints/Workflows: `GET /api/generation/checkpoints`,
  `GET|POST|DELETE /api/image/workflows[/{id}]`
- プロンプト編集（prompt_crafter）: `GET /api/image/prompts[/active]`, `POST /api/image/prompts/craft`,
  `DELETE /api/image/prompts/active` / `/{session_id}`
- Agent 制御: `GET /api/image/agents`,
  `GET /api/image/agents/{id}/comfyui/status`, `POST /api/image/agents/{id}/comfyui/{start|stop}`,
  `GET /api/image/agents/{id}/comfyui/history`
- ファイル配信: `GET /api/image/file`（NAS 上の output を転送）
- コレクション: `GET|POST|PATCH|DELETE /api/generation/collections[/{id}[/jobs]]`

## 15. LoRA 学習 ― `routes/lora_train.py`

- `GET|POST|PATCH|DELETE /api/lora/projects[/{id}]`
- データセット: 画像 list/upload/delete/キャプション編集
- ジョブ: 投入 / 一覧 / 状態 / キャンセル / ログ / EDL/プレビュー配信
- テンプレート: `GET /api/lora/config-templates`, 適用/削除

## 16. Clip Pipeline（自動切り抜き）― `routes/clip_pipeline.py`

- `POST /api/clip-pipeline/jobs`（投入）, `GET /api/clip-pipeline/jobs[/stream|/{id}]`, `POST /cancel`
- `GET /api/clip-pipeline/jobs/{id}/edl`（生成 EDL 取得）
- `GET /api/clip-pipeline/capability`（Agent の Whisper/Ollama 状態）
- `GET /api/clip-pipeline/inputs`（NAS inputs/ の候補列挙）

## 17. 同居ツール

`src/web/app.py` からそれぞれ別モジュールの `register(app, bot)` が呼ばれる。

- `/tools/zzz-disc/*` — `src/tools/zzz_disc`（HoYoLAB 連携ディスク Codex）
- `/tools/image-gen/*` — `src/tools/image_gen_console`（画像生成コンソール SPA）

---

## 実装上の約束

- ルート追加時は `src/web/routes/__init__.py::register_all_routes()` に登録する。
- 長期実行 / SSE ストリームは Basic 認証を免除する設計（`dependencies=[Depends(ctx.verify)]` を
  付けない）。ブラウザ EventSource は Basic 認証ヘッダを送れないため。
- Agent 通信は `ctx.bot.unit_manager.agent_pool` 経由。Agent 再起動中は `_agent_restart_ts` を見て
  restarting 扱いする（`app.py` 参照）。
