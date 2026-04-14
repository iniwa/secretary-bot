/** Settings page — 10セクションのアコーディオン構成。
 * 各セクションは details/summary ベースで独立開閉。
 * 値の保存は /api/settings（汎用）または既存の個別エンドポイントに寄せる。
 */
import { api, apiBatch } from '../api.js';
import { toast } from '../app.js';

function $(id) { return document.getElementById(id); }
function val(id) { return $(id)?.value ?? ''; }
function numVal(id) { const v = val(id); return v === '' ? null : Number(v); }
function boolVal(id) { return !!$(id)?.checked; }
function setVal(id, v) { const el = $(id); if (el) el.value = (v ?? ''); }
function setNum(id, v) { const el = $(id); if (el) el.value = (v ?? ''); }
function setBool(id, v) { const el = $(id); if (el) el.checked = !!v; }

// 折りたたみ状態を localStorage に保持（リロード後も維持）
const OPEN_KEY = 'settings.openSections';
function loadOpen() {
  try { return new Set(JSON.parse(localStorage.getItem(OPEN_KEY) || '[]')); }
  catch { return new Set(); }
}
function saveOpen(set) {
  try { localStorage.setItem(OPEN_KEY, JSON.stringify([...set])); } catch {}
}

export function render() {
  return `
<div class="accordion" id="settings-accordion">

  <!-- 1. LLM & モデル -->
  <details class="accordion-item" data-section="llm">
    <summary class="accordion-summary">LLM & モデル<span class="acc-hint">Ollama / Gemini</span></summary>
    <div class="accordion-body">
      <div class="acc-grid">
        <div class="form-group">
          <label class="form-label">Ollama Model</label>
          <select id="s-ollama-model" class="form-input"></select>
        </div>
        <div class="form-group">
          <label class="form-label">Ollama Timeout (sec)</label>
          <input type="number" id="s-ollama-timeout" class="form-input" min="1" step="1">
        </div>
        <div class="form-group">
          <label class="form-label">Ollama URL</label>
          <input type="text" id="s-ollama-url" class="form-input" placeholder="http://...:11434">
        </div>
        <div class="form-group">
          <label class="form-label">Gemini Model</label>
          <input type="text" id="s-gemini-model" class="form-input" placeholder="gemini-...">
        </div>
      </div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-llm-save">Save</button></div>
    </div>
  </details>

  <!-- 2. Gemini -->
  <details class="accordion-item" data-section="gemini">
    <summary class="accordion-summary">Gemini<span class="acc-hint">課金注意</span></summary>
    <div class="accordion-body">
      <div class="warning-text">Gemini API usage incurs costs. Enable only what you need.</div>
      <div class="acc-grid">
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Conversation</label><label class="toggle-switch"><input type="checkbox" id="s-gemini-conversation"><span class="toggle-slider"></span></label></div></div>
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Memory Extraction</label><label class="toggle-switch"><input type="checkbox" id="s-gemini-memory"><span class="toggle-slider"></span></label></div></div>
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Unit Routing</label><label class="toggle-switch"><input type="checkbox" id="s-gemini-routing"><span class="toggle-slider"></span></label></div></div>
        <div class="form-group"><label class="form-label">Monthly Token Limit</label><input type="number" id="s-gemini-token-limit" class="form-input" min="0" step="1000"></div>
      </div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-gemini-save">Save</button></div>
    </div>
  </details>

  <!-- 3. Heartbeat -->
  <details class="accordion-item" data-section="heartbeat">
    <summary class="accordion-summary">Heartbeat<span class="acc-hint">tick 間隔</span></summary>
    <div class="accordion-body">
      <div class="acc-grid">
        <div class="form-group"><label class="form-label">Interval with Ollama (min)</label><input type="number" id="s-hb-with" class="form-input" min="1" step="1"></div>
        <div class="form-group"><label class="form-label">Interval without Ollama (min)</label><input type="number" id="s-hb-without" class="form-input" min="1" step="1"></div>
        <div class="form-group"><label class="form-label">Compact Threshold (messages)</label><input type="number" id="s-hb-compact" class="form-input" min="1" step="1"></div>
      </div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-hb-save">Save</button></div>
    </div>
  </details>

  <!-- 4. InnerMind 基本 -->
  <details class="accordion-item" data-section="innermind">
    <summary class="accordion-summary">InnerMind 基本<span class="acc-hint">自律思考の入口</span></summary>
    <div class="accordion-body">
      <div class="acc-grid">
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Enabled</label><label class="toggle-switch"><input type="checkbox" id="s-im-enabled"><span class="toggle-slider"></span></label></div></div>
        <div class="form-group"><label class="form-label">Thinking interval (ticks)</label><input type="number" id="s-im-ticks" class="form-input" min="1" step="1"></div>
        <div class="form-group"><label class="form-label">Min speak interval (min)</label><input type="number" id="s-im-min-speak" class="form-input" min="0" step="1"></div>
        <div class="form-group"><label class="form-label">Active threshold (min)</label><input type="number" id="s-im-active" class="form-input" min="0" step="1"></div>
        <div class="form-group"><label class="form-label">Speak channel ID</label><input type="text" id="s-im-channel" class="form-input"></div>
        <div class="form-group"><label class="form-label">Target user ID</label><input type="text" id="s-im-user" class="form-input"></div>
      </div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-im-save">Save</button></div>
    </div>
  </details>

  <!-- 5. InnerMind 自律アクション（Phase 2 で本実装） -->
  <details class="accordion-item" data-section="autonomy" open>
    <summary class="accordion-summary">InnerMind 自律アクション<span class="acc-hint">OK/NG 承認制</span></summary>
    <div class="accordion-body">
      <div class="warning-text">Phase 2 で本実装予定。現在はUI枠のみ表示しています。</div>
      <div class="acc-grid">
        <div class="form-group">
          <label class="form-label">自律モード</label>
          <select id="s-auto-mode" class="form-input" disabled>
            <option value="off">off</option>
            <option value="observe_only">observe_only (T0のみ)</option>
            <option value="proposal">proposal (T0+T1+承認経由のT2/T3)</option>
            <option value="full">full (T4以外自動)</option>
          </select>
        </div>
        <div class="form-group"><label class="form-label">承認タイムアウト (分)</label><input type="number" id="s-auto-timeout" class="form-input" min="1" step="1" disabled value="30"></div>
        <div class="form-group"><label class="form-label">T2 日次上限 (0=無制限)</label><input type="number" id="s-auto-t2-limit" class="form-input" min="0" step="1" disabled value="0"></div>
        <div class="form-group"><label class="form-label">T3 日次上限 (0=無制限)</label><input type="number" id="s-auto-t3-limit" class="form-input" min="0" step="1" disabled value="0"></div>
        <div class="form-group">
          <label class="form-label">同時 pending 処理</label>
          <select id="s-auto-concurrent" class="form-input" disabled>
            <option value="single">single (1件のみ)</option>
            <option value="queue" selected>queue (順次承認)</option>
            <option value="prefer_new">prefer_new (新規優先)</option>
          </select>
        </div>
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">確認メッセージに理由を含める</label><label class="toggle-switch"><input type="checkbox" id="s-auto-show-reasoning" disabled checked><span class="toggle-slider"></span></label></div></div>
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">承認待ちを通知</label><label class="toggle-switch"><input type="checkbox" id="s-auto-notify" disabled checked><span class="toggle-slider"></span></label></div></div>
      </div>
    </div>
  </details>

  <!-- 6. InnerMind 外部情報 -->
  <details class="accordion-item" data-section="im-external">
    <summary class="accordion-summary">InnerMind 外部情報<span class="acc-hint">GitHub / Tavily</span></summary>
    <div class="accordion-body">
      <div class="acc-sub-title">GitHub</div>
      <div class="acc-grid">
        <div class="form-group"><label class="form-label">Username</label><input type="text" id="s-gh-user" class="form-input"></div>
        <div class="form-group"><label class="form-label">Lookback hours</label><input type="number" id="s-gh-hours" class="form-input" min="1" step="1"></div>
        <div class="form-group"><label class="form-label">Max items</label><input type="number" id="s-gh-max" class="form-input" min="1" step="1"></div>
      </div>
      <div class="acc-sub">
        <div class="acc-sub-title">Tavily News</div>
        <div class="acc-grid">
          <div class="form-group"><label class="form-label">Queries (comma-separated)</label><input type="text" id="s-tav-queries" class="form-input" placeholder="生成AI,VTuber"></div>
          <div class="form-group"><label class="form-label">Results per query</label><input type="number" id="s-tav-max" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Lookback days</label><input type="number" id="s-tav-days" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Topic</label><input type="text" id="s-tav-topic" class="form-input" placeholder="news"></div>
        </div>
      </div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-im-ext-save">Save</button></div>
    </div>
  </details>

  <!-- 7. キャラクター -->
  <details class="accordion-item" data-section="character">
    <summary class="accordion-summary">キャラクター<span class="acc-hint">ペルソナ</span></summary>
    <div class="accordion-body">
      <div class="acc-grid">
        <div class="form-group"><label class="form-label">Name</label><input type="text" id="s-char-name" class="form-input"></div>
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Ollama only</label><label class="toggle-switch"><input type="checkbox" id="s-char-ollama-only"><span class="toggle-slider"></span></label></div></div>
      </div>
      <div class="form-group"><label class="form-label">Persona</label><textarea id="s-persona-text" class="form-input persona-textarea" placeholder="Persona text..."></textarea></div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-char-save">Save</button></div>
    </div>
  </details>

  <!-- 8. Chat -->
  <details class="accordion-item" data-section="chat">
    <summary class="accordion-summary">Chat<span class="acc-hint">会話履歴</span></summary>
    <div class="accordion-body">
      <div class="acc-grid">
        <div class="form-group"><label class="form-label">History Window (min)</label><input type="number" id="s-chat-history" class="form-input" min="0" step="1"><div class="form-hint">0 = unlimited</div></div>
      </div>
      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-chat-save">Save</button></div>
    </div>
  </details>

  <!-- 9. 外部サービス -->
  <details class="accordion-item" data-section="external">
    <summary class="accordion-summary">外部サービス<span class="acc-hint">RSS / 天気 / 検索 / STT / 委託</span></summary>
    <div class="accordion-body">

      <div class="acc-sub-title">RSS</div>
      <div class="acc-grid">
        <div class="form-group"><label class="form-label">Fetch interval (min)</label><input type="number" id="s-rss-interval" class="form-input" min="1" step="1"></div>
        <div class="form-group"><label class="form-label">Digest hour</label><input type="number" id="s-rss-digest" class="form-input" min="0" max="23" step="1"></div>
        <div class="form-group"><label class="form-label">Retention days</label><input type="number" id="s-rss-retain" class="form-input" min="1" step="1"></div>
        <div class="form-group"><label class="form-label">Max per category</label><input type="number" id="s-rss-max" class="form-input" min="1" step="1"></div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">Weather</div>
        <div class="acc-grid">
          <div class="form-group"><label class="form-label">Default location</label><input type="text" id="s-w-loc" class="form-input"></div>
          <div class="form-group"><label class="form-label">Umbrella threshold (%)</label><input type="number" id="s-w-umb" class="form-input" min="0" max="100" step="1"></div>
        </div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">SearXNG</div>
        <div class="acc-grid">
          <div class="form-group"><label class="form-label">URL</label><input type="text" id="s-sx-url" class="form-input"></div>
          <div class="form-group"><label class="form-label">Max results</label><input type="number" id="s-sx-max" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Fetch pages</label><input type="number" id="s-sx-pages" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Max chars / page</label><input type="number" id="s-sx-chars" class="form-input" min="100" step="100"></div>
        </div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">Rakuten Search</div>
        <div class="acc-grid">
          <div class="form-group"><label class="form-label">Max results</label><input type="number" id="s-rk-max" class="form-input" min="1" step="1"></div>
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Fetch details</label><label class="toggle-switch"><input type="checkbox" id="s-rk-details"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><label class="form-label">Detail concurrency</label><input type="number" id="s-rk-conc" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Detail max desc chars</label><input type="number" id="s-rk-desc" class="form-input" min="50" step="50"></div>
        </div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">STT</div>
        <div class="acc-grid">
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Enabled</label><label class="toggle-switch"><input type="checkbox" id="s-stt-enabled"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><label class="form-label">Polling interval (min)</label><input type="number" id="s-stt-poll" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Summary threshold (chars)</label><input type="number" id="s-stt-sum" class="form-input" min="100" step="100"></div>
        </div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">Delegation (閾値)</div>
        <div class="acc-grid">
          <div class="form-group"><label class="form-label">CPU %</label><input type="number" id="s-dlg-cpu" class="form-input" min="0" max="100" step="1"></div>
          <div class="form-group"><label class="form-label">Memory %</label><input type="number" id="s-dlg-mem" class="form-input" min="0" max="100" step="1"></div>
          <div class="form-group"><label class="form-label">GPU %</label><input type="number" id="s-dlg-gpu" class="form-input" min="0" max="100" step="1"></div>
        </div>
      </div>

      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-ext-save">Save</button></div>
    </div>
  </details>

  <!-- 10. メモリ & アクティビティ -->
  <details class="accordion-item" data-section="system">
    <summary class="accordion-summary">メモリ & アクティビティ<span class="acc-hint">sweep / 検出ルール</span></summary>
    <div class="accordion-body">

      <div class="acc-sub-title">Memory</div>
      <div class="acc-grid">
        <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Sweep enabled</label><label class="toggle-switch"><input type="checkbox" id="s-mem-sweep"><span class="toggle-slider"></span></label></div></div>
        <div class="form-group"><label class="form-label">Sweep stale days</label><input type="number" id="s-mem-stale" class="form-input" min="1" step="1"></div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">Activity</div>
        <div class="acc-grid">
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Enabled</label><label class="toggle-switch"><input type="checkbox" id="s-act-enabled"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Block: OBS streaming</label><label class="toggle-switch"><input type="checkbox" id="s-act-obs-stream"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Block: OBS recording</label><label class="toggle-switch"><input type="checkbox" id="s-act-obs-rec"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Block: OBS replay buffer</label><label class="toggle-switch"><input type="checkbox" id="s-act-obs-rep"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Block: gaming on main</label><label class="toggle-switch"><input type="checkbox" id="s-act-gaming"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Block: Discord VC</label><label class="toggle-switch"><input type="checkbox" id="s-act-vc"><span class="toggle-slider"></span></label></div></div>
        </div>
      </div>

      <div class="acc-sub">
        <div class="acc-sub-title">Docker Monitor</div>
        <div class="acc-grid">
          <div class="form-group"><div class="form-row"><label class="form-label" style="margin-bottom:0">Enabled</label><label class="toggle-switch"><input type="checkbox" id="s-dm-enabled"><span class="toggle-slider"></span></label></div></div>
          <div class="form-group"><label class="form-label">Check interval (sec)</label><input type="number" id="s-dm-interval" class="form-input" min="10" step="10"></div>
          <div class="form-group"><label class="form-label">Cooldown (min)</label><input type="number" id="s-dm-cool" class="form-input" min="1" step="1"></div>
          <div class="form-group"><label class="form-label">Max lines / check</label><input type="number" id="s-dm-lines" class="form-input" min="10" step="10"></div>
        </div>
      </div>

      <div class="card-footer"><button class="btn btn-primary btn-sm" id="s-sys-save">Save</button></div>
    </div>
  </details>

</div>`;
}

// ---- helpers ----

async function save(path, body, label) {
  try {
    await api(path, { method: 'POST', body });
    toast(`${label} saved`, 'success');
  } catch (err) {
    console.error(`Save ${label}:`, err);
    toast(`Failed to save ${label}`, 'error');
  }
}

async function saveSettings(pairs, label) {
  try {
    await api('/api/settings', { method: 'POST', body: pairs });
    toast(`${label} saved`, 'success');
  } catch (err) {
    console.error(`Save ${label}:`, err);
    toast(`Failed to save ${label}`, 'error');
  }
}

// ---- mount ----

export async function mount() {
  // 折りたたみ状態を復元
  const openSet = loadOpen();
  document.querySelectorAll('#settings-accordion .accordion-item').forEach(el => {
    const key = el.dataset.section;
    if (openSet.has(key)) el.open = true;
    el.addEventListener('toggle', () => {
      const cur = loadOpen();
      if (el.open) cur.add(key); else cur.delete(key);
      saveOpen(cur);
    });
  });

  // URLハッシュで該当セクション展開
  const m = location.hash.match(/section=([a-z-]+)/);
  if (m) {
    const target = document.querySelector(`.accordion-item[data-section="${m[1]}"]`);
    if (target) { target.open = true; target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }

  // Fetch all config in parallel
  const [llm, gemini, persona, heartbeat, chatCfg, rakuten, ollamaModels, imSettings, genericSettings] = await apiBatch([
    ['/api/llm-config'],
    ['/api/gemini-config'],
    ['/api/persona'],
    ['/api/heartbeat-config'],
    ['/api/chat-config'],
    ['/api/rakuten-config'],
    ['/api/ollama-models'],
    ['/api/inner-mind/settings'],
    ['/api/settings'],
  ]);

  // 1. LLM
  const modelSelect = $('s-ollama-model');
  if (ollamaModels?.models) {
    ollamaModels.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      modelSelect.appendChild(opt);
    });
  }
  if (llm) {
    modelSelect.value = llm.ollama_model || '';
    if (modelSelect.value !== (llm.ollama_model || '') && llm.ollama_model) {
      const opt = document.createElement('option');
      opt.value = llm.ollama_model; opt.textContent = llm.ollama_model;
      modelSelect.prepend(opt); modelSelect.value = llm.ollama_model;
    }
    setNum('s-ollama-timeout', llm.ollama_timeout);
    setVal('s-gemini-model', llm.gemini_model || '');
  }
  setVal('s-ollama-url', genericSettings?.['llm.ollama_url'] ?? '');

  // 2. Gemini
  if (gemini) {
    setBool('s-gemini-conversation', gemini.conversation);
    setBool('s-gemini-memory', gemini.memory_extraction);
    setBool('s-gemini-routing', gemini.unit_routing);
    setNum('s-gemini-token-limit', gemini.monthly_token_limit);
  }

  // 3. Heartbeat
  if (heartbeat) {
    setNum('s-hb-with', heartbeat.interval_with_ollama_minutes);
    setNum('s-hb-without', heartbeat.interval_without_ollama_minutes);
    setNum('s-hb-compact', heartbeat.compact_threshold_messages);
  }

  // 4. InnerMind 基本
  if (imSettings) {
    setBool('s-im-enabled', imSettings.enabled);
    setNum('s-im-ticks', imSettings.thinking_interval_ticks);
    setNum('s-im-min-speak', imSettings.min_speak_interval_minutes);
    setVal('s-im-channel', imSettings.speak_channel_id || '');
    setVal('s-im-user', imSettings.target_user_id || '');
  }
  setNum('s-im-active', genericSettings?.['inner_mind.active_threshold_minutes']);

  // 6. InnerMind 外部情報
  const g = genericSettings || {};
  setVal('s-gh-user', g['inner_mind.github.username'] ?? '');
  setNum('s-gh-hours', g['inner_mind.github.lookback_hours']);
  setNum('s-gh-max', g['inner_mind.github.max_items']);
  setVal('s-tav-queries', (imSettings?.tavily_queries || []).join(','));
  setNum('s-tav-max', g['inner_mind.tavily_news.max_results_per_query']);
  setNum('s-tav-days', g['inner_mind.tavily_news.lookback_days']);
  setVal('s-tav-topic', g['inner_mind.tavily_news.topic'] ?? '');

  // 7. キャラクター
  setVal('s-char-name', g['character.name'] ?? '');
  setBool('s-char-ollama-only', g['character.ollama_only']);
  if (persona) setVal('s-persona-text', persona.persona || '');

  // 8. Chat
  if (chatCfg) setNum('s-chat-history', chatCfg.history_minutes);

  // 9. 外部サービス
  setNum('s-rss-interval', g['rss.fetch_interval_minutes']);
  setNum('s-rss-digest', g['rss.digest_hour']);
  setNum('s-rss-retain', g['rss.article_retention_days']);
  setNum('s-rss-max', g['rss.max_articles_per_category']);
  setVal('s-w-loc', g['weather.default_location'] ?? '');
  setNum('s-w-umb', g['weather.umbrella_threshold']);
  setVal('s-sx-url', g['searxng.url'] ?? '');
  setNum('s-sx-max', g['searxng.max_results']);
  setNum('s-sx-pages', g['searxng.fetch_pages']);
  setNum('s-sx-chars', g['searxng.max_chars_per_page']);
  if (rakuten) {
    setNum('s-rk-max', rakuten.max_results);
    setBool('s-rk-details', rakuten.fetch_details);
  }
  setNum('s-rk-conc', g['rakuten_search.detail_concurrency']);
  setNum('s-rk-desc', g['rakuten_search.detail_max_desc_chars']);
  setBool('s-stt-enabled', g['stt.enabled']);
  setNum('s-stt-poll', g['stt.polling_interval_minutes']);
  setNum('s-stt-sum', g['stt.processing.summary_threshold_chars']);
  setNum('s-dlg-cpu', g['delegation.thresholds.cpu_percent']);
  setNum('s-dlg-mem', g['delegation.thresholds.memory_percent']);
  setNum('s-dlg-gpu', g['delegation.thresholds.gpu_percent']);

  // 10. メモリ & アクティビティ
  setBool('s-mem-sweep', g['memory.sweep_enabled']);
  setNum('s-mem-stale', g['memory.sweep_stale_days']);
  setBool('s-act-enabled', g['activity.enabled']);
  setBool('s-act-obs-stream', g['activity.block_rules.obs_streaming']);
  setBool('s-act-obs-rec', g['activity.block_rules.obs_recording']);
  setBool('s-act-obs-rep', g['activity.block_rules.obs_replay_buffer']);
  setBool('s-act-gaming', g['activity.block_rules.gaming_on_main']);
  setBool('s-act-vc', g['activity.block_rules.discord_vc']);
  setBool('s-dm-enabled', g['docker_monitor.enabled']);
  setNum('s-dm-interval', g['docker_monitor.check_interval_seconds']);
  setNum('s-dm-cool', g['docker_monitor.cooldown_minutes']);
  setNum('s-dm-lines', g['docker_monitor.max_lines_per_check']);

  // ---- Save handlers ----

  $('s-llm-save').addEventListener('click', async () => {
    await save('/api/llm-config', {
      ollama_model: modelSelect.value,
      ollama_timeout: numVal('s-ollama-timeout'),
      gemini_model: val('s-gemini-model'),
    }, 'LLM config');
    // ollama_url は汎用 settings に保存
    await saveSettings({ 'llm.ollama_url': val('s-ollama-url') }, 'Ollama URL');
  });

  $('s-gemini-save').addEventListener('click', () => {
    save('/api/gemini-config', {
      conversation: boolVal('s-gemini-conversation'),
      memory_extraction: boolVal('s-gemini-memory'),
      unit_routing: boolVal('s-gemini-routing'),
      monthly_token_limit: numVal('s-gemini-token-limit'),
    }, 'Gemini config');
  });

  $('s-hb-save').addEventListener('click', () => {
    save('/api/heartbeat-config', {
      interval_with_ollama_minutes: numVal('s-hb-with'),
      interval_without_ollama_minutes: numVal('s-hb-without'),
      compact_threshold_messages: numVal('s-hb-compact'),
    }, 'Heartbeat config');
  });

  $('s-im-save').addEventListener('click', async () => {
    await save('/api/inner-mind/settings', {
      enabled: boolVal('s-im-enabled'),
      min_speak_interval_minutes: numVal('s-im-min-speak'),
      speak_channel_id: val('s-im-channel'),
      target_user_id: val('s-im-user'),
    }, 'InnerMind');
    await saveSettings({
      'inner_mind.thinking_interval_ticks': numVal('s-im-ticks'),
      'inner_mind.active_threshold_minutes': numVal('s-im-active'),
    }, 'InnerMind misc');
  });

  $('s-im-ext-save').addEventListener('click', async () => {
    await saveSettings({
      'inner_mind.github.username': val('s-gh-user'),
      'inner_mind.github.lookback_hours': numVal('s-gh-hours'),
      'inner_mind.github.max_items': numVal('s-gh-max'),
      'inner_mind.tavily_news.max_results_per_query': numVal('s-tav-max'),
      'inner_mind.tavily_news.lookback_days': numVal('s-tav-days'),
      'inner_mind.tavily_news.topic': val('s-tav-topic'),
    }, 'InnerMind external');
    // Tavily queries はリスト形式で別エンドポイント
    await save('/api/inner-mind/settings', {
      tavily_queries: val('s-tav-queries'),
    }, 'Tavily queries');
  });

  $('s-char-save').addEventListener('click', async () => {
    await save('/api/persona', { persona: val('s-persona-text') }, 'Persona');
    await saveSettings({
      'character.name': val('s-char-name'),
      'character.ollama_only': boolVal('s-char-ollama-only'),
    }, 'Character');
  });

  $('s-chat-save').addEventListener('click', () => {
    save('/api/chat-config', { history_minutes: numVal('s-chat-history') }, 'Chat');
  });

  $('s-ext-save').addEventListener('click', async () => {
    await save('/api/rakuten-config', {
      max_results: numVal('s-rk-max'),
      fetch_details: boolVal('s-rk-details'),
    }, 'Rakuten');
    await saveSettings({
      'rss.fetch_interval_minutes': numVal('s-rss-interval'),
      'rss.digest_hour': numVal('s-rss-digest'),
      'rss.article_retention_days': numVal('s-rss-retain'),
      'rss.max_articles_per_category': numVal('s-rss-max'),
      'weather.default_location': val('s-w-loc'),
      'weather.umbrella_threshold': numVal('s-w-umb'),
      'searxng.url': val('s-sx-url'),
      'searxng.max_results': numVal('s-sx-max'),
      'searxng.fetch_pages': numVal('s-sx-pages'),
      'searxng.max_chars_per_page': numVal('s-sx-chars'),
      'rakuten_search.detail_concurrency': numVal('s-rk-conc'),
      'rakuten_search.detail_max_desc_chars': numVal('s-rk-desc'),
      'stt.enabled': boolVal('s-stt-enabled'),
      'stt.polling_interval_minutes': numVal('s-stt-poll'),
      'stt.processing.summary_threshold_chars': numVal('s-stt-sum'),
      'delegation.thresholds.cpu_percent': numVal('s-dlg-cpu'),
      'delegation.thresholds.memory_percent': numVal('s-dlg-mem'),
      'delegation.thresholds.gpu_percent': numVal('s-dlg-gpu'),
    }, 'External services');
  });

  $('s-sys-save').addEventListener('click', () => {
    saveSettings({
      'memory.sweep_enabled': boolVal('s-mem-sweep'),
      'memory.sweep_stale_days': numVal('s-mem-stale'),
      'activity.enabled': boolVal('s-act-enabled'),
      'activity.block_rules.obs_streaming': boolVal('s-act-obs-stream'),
      'activity.block_rules.obs_recording': boolVal('s-act-obs-rec'),
      'activity.block_rules.obs_replay_buffer': boolVal('s-act-obs-rep'),
      'activity.block_rules.gaming_on_main': boolVal('s-act-gaming'),
      'activity.block_rules.discord_vc': boolVal('s-act-vc'),
      'docker_monitor.enabled': boolVal('s-dm-enabled'),
      'docker_monitor.check_interval_seconds': numVal('s-dm-interval'),
      'docker_monitor.cooldown_minutes': numVal('s-dm-cool'),
      'docker_monitor.max_lines_per_check': numVal('s-dm-lines'),
    }, 'System');
  });
}
