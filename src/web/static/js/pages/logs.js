/** Logs page — tabbed interface for conversation, LLM, and heartbeat logs. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeTab = 'conversation';
let convState = { logs: [], offset: 0, loading: false, hasMore: true };
let llmState  = { logs: [], offset: 0, loading: false, hasMore: true };
let hbState   = { logs: [], loaded: false };

const CONV_LIMIT = 50;
const LLM_LIMIT  = 30;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function fmtTime(iso) {
  if (!iso) return '---';
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mi}`;
}

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function truncate(str, max = 120) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '...' : str;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .log-tabs {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 1rem;
  }
  .log-tab {
    padding: 0.45rem 1rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg-surface);
    color: var(--text-secondary);
    font-size: 0.8125rem;
    font-weight: 500;
    cursor: pointer;
    transition: all var(--ease);
  }
  .log-tab:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .log-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .log-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.75rem;
  }
  .log-filters .form-input,
  .log-filters select.form-input {
    width: auto;
    min-width: 120px;
  }
  .log-filters label {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.8125rem;
    color: var(--text-secondary);
    cursor: pointer;
  }
  .log-content-cell {
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .log-expand-row td {
    padding: 0;
    border-bottom: 1px solid var(--border);
  }
  .log-expand-row pre {
    margin: 0;
    padding: 0.75rem 1rem;
    background: var(--bg-base);
    font-size: 0.75rem;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--text-secondary);
    max-height: 300px;
    overflow-y: auto;
  }
  .log-expand-label {
    font-size: 0.6875rem;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.5rem 1rem 0.15rem;
    background: var(--bg-base);
  }
  .log-tab-panel { display: none; }
  .log-tab-panel.active { display: block; }
  .load-more-wrap {
    text-align: center;
    padding: 1rem 0;
  }
  .clickable-row { cursor: pointer; }
  .clickable-row:hover td { background: var(--bg-overlay); }
  .hb-list {
    max-height: 600px;
    overflow-y: auto;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.75rem 1rem;
  }
  .hb-entry {
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.8125rem;
    color: var(--text-secondary);
    line-height: 1.5;
  }
  .hb-entry:last-child { border-bottom: none; }
  .tps-value { font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 0.75rem; }
</style>

<div class="logs-page">
  <div class="log-tabs">
    <button class="log-tab active" data-tab="conversation">Conversation</button>
    <button class="log-tab" data-tab="llm">LLM</button>
    <button class="log-tab" data-tab="heartbeat">Heartbeat</button>
  </div>

  <!-- Conversation Logs -->
  <div class="log-tab-panel active" id="panel-conversation">
    <div class="log-filters">
      <input type="text" class="form-input" id="conv-keyword" placeholder="Search keyword..." style="min-width:180px">
      <select class="form-input" id="conv-channel">
        <option value="">All channels</option>
        <option value="discord">Discord</option>
        <option value="discord_dm">Discord DM</option>
        <option value="webgui">WebGUI</option>
      </select>
      <label>
        <input type="checkbox" id="conv-botonly"> Bot only
      </label>
      <button class="btn btn-sm btn-primary" id="conv-search">Search</button>
    </div>
    <div class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Channel</th>
              <th>Role</th>
              <th>Unit</th>
              <th>Content</th>
            </tr>
          </thead>
          <tbody id="conv-tbody"></tbody>
        </table>
      </div>
      <div class="load-more-wrap" id="conv-more-wrap" style="display:none">
        <button class="btn btn-sm" id="conv-more">Load more</button>
      </div>
    </div>
  </div>

  <!-- LLM Logs -->
  <div class="log-tab-panel" id="panel-llm">
    <div class="log-filters">
      <select class="form-input" id="llm-provider">
        <option value="">All providers</option>
        <option value="ollama">Ollama</option>
        <option value="gemini">Gemini</option>
      </select>
    </div>
    <div class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Provider</th>
              <th>Model</th>
              <th>Purpose</th>
              <th>Prompt</th>
              <th>Response</th>
              <th>Duration</th>
              <th>TPS</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="llm-tbody"></tbody>
        </table>
      </div>
      <div class="load-more-wrap" id="llm-more-wrap" style="display:none">
        <button class="btn btn-sm" id="llm-more">Load more</button>
      </div>
    </div>
  </div>

  <!-- Heartbeat Logs -->
  <div class="log-tab-panel" id="panel-heartbeat">
    <div class="card">
      <div class="card-header">
        <h3>Heartbeat Logs</h3>
        <button class="btn btn-sm" id="hb-refresh">Refresh</button>
      </div>
      <div class="hb-list" id="hb-list">
        <div class="text-muted text-sm">Loading...</div>
      </div>
    </div>
  </div>
</div>`;
}

// ============================================================
// Conversation tab
// ============================================================
function renderConvRows(logs) {
  return logs.map(l => {
    const roleBadge = l.role === 'assistant'
      ? '<span class="badge badge-accent">bot</span>'
      : '<span class="badge badge-muted">user</span>';
    const unitBadge = l.unit
      ? `<span class="badge badge-info">${esc(l.unit)}</span>`
      : '';
    const channelLabel = l.channel_name || l.channel || '';
    return `<tr>
      <td class="mono text-xs">${fmtTime(l.timestamp)}</td>
      <td>${esc(channelLabel)}</td>
      <td>${roleBadge}</td>
      <td>${unitBadge}</td>
      <td class="log-content-cell" title="${esc(l.content)}">${esc(truncate(l.content))}</td>
    </tr>`;
  }).join('');
}

async function loadConversation(reset = false) {
  if (convState.loading) return;
  if (reset) {
    convState.offset = 0;
    convState.logs = [];
    convState.hasMore = true;
  }
  if (!convState.hasMore) return;

  convState.loading = true;
  try {
    const params = { limit: CONV_LIMIT, offset: convState.offset };
    const keyword = $('conv-keyword')?.value?.trim();
    const channel = $('conv-channel')?.value;
    const botOnly = $('conv-botonly')?.checked;
    if (keyword) params.keyword = keyword;
    if (channel) params.channel = channel;
    if (botOnly) params.bot_only = '1';

    const data = await api('/api/logs', { params });
    const logs = data?.logs || [];

    convState.logs = reset ? logs : convState.logs.concat(logs);
    convState.offset += logs.length;
    convState.hasMore = logs.length >= CONV_LIMIT;

    const tbody = $('conv-tbody');
    if (tbody) {
      if (reset) {
        tbody.innerHTML = renderConvRows(convState.logs);
      } else {
        tbody.insertAdjacentHTML('beforeend', renderConvRows(logs));
      }
    }
    if (convState.logs.length === 0 && reset) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:2rem">No logs found</td></tr>';
    }

    const moreWrap = $('conv-more-wrap');
    if (moreWrap) moreWrap.style.display = convState.hasMore ? '' : 'none';
  } catch (err) {
    toast('Failed to load conversation logs', 'error');
    console.error(err);
  } finally {
    convState.loading = false;
  }
}

// ============================================================
// LLM tab
// ============================================================
function renderLlmRows(logs) {
  return logs.map(l => {
    const statusBadge = l.success
      ? '<span class="badge badge-success">OK</span>'
      : `<span class="badge badge-error">ERR</span>`;
    const dur = l.duration_ms != null ? `${(l.duration_ms / 1000).toFixed(1)}s` : '---';
    const tps = l.tokens_per_sec != null ? l.tokens_per_sec.toFixed(1) : '---';
    const provBadge = l.provider === 'ollama'
      ? '<span class="badge badge-info">ollama</span>'
      : '<span class="badge badge-warning">gemini</span>';

    const mainRow = `<tr class="clickable-row" data-llm-id="${l.id}">
      <td class="mono text-xs">${fmtTime(l.timestamp)}</td>
      <td>${provBadge}</td>
      <td class="text-xs">${esc(l.model || '---')}</td>
      <td>${esc(l.purpose || '---')}</td>
      <td class="mono text-xs">${l.prompt_len ?? '---'}</td>
      <td class="mono text-xs">${l.response_len ?? '---'}</td>
      <td class="mono text-xs">${dur}</td>
      <td class="tps-value">${tps}</td>
      <td>${statusBadge}</td>
    </tr>`;

    const expandContent = [];
    if (l.prompt_text) {
      expandContent.push(
        `<div class="log-expand-label">Prompt</div><pre>${esc(l.prompt_text)}</pre>`
      );
    }
    if (l.response_text) {
      expandContent.push(
        `<div class="log-expand-label">Response</div><pre>${esc(l.response_text)}</pre>`
      );
    }
    if (l.error) {
      expandContent.push(
        `<div class="log-expand-label">Error</div><pre>${esc(l.error)}</pre>`
      );
    }

    const expandRow = expandContent.length > 0
      ? `<tr class="log-expand-row" data-expand-for="${l.id}" style="display:none"><td colspan="9">${expandContent.join('')}</td></tr>`
      : '';

    return mainRow + expandRow;
  }).join('');
}

function toggleLlmExpand(id) {
  const row = document.querySelector(`tr[data-expand-for="${id}"]`);
  if (!row) return;
  row.style.display = row.style.display === 'none' ? '' : 'none';
}

async function loadLlm(reset = false) {
  if (llmState.loading) return;
  if (reset) {
    llmState.offset = 0;
    llmState.logs = [];
    llmState.hasMore = true;
  }
  if (!llmState.hasMore) return;

  llmState.loading = true;
  try {
    const params = { limit: LLM_LIMIT, offset: llmState.offset };
    const provider = $('llm-provider')?.value;
    if (provider) params.provider = provider;

    const data = await api('/api/logs/llm', { params });
    const logs = data?.logs || [];

    llmState.logs = reset ? logs : llmState.logs.concat(logs);
    llmState.offset += logs.length;
    llmState.hasMore = logs.length >= LLM_LIMIT;

    const tbody = $('llm-tbody');
    if (tbody) {
      if (reset) {
        tbody.innerHTML = renderLlmRows(llmState.logs);
      } else {
        tbody.insertAdjacentHTML('beforeend', renderLlmRows(logs));
      }
      attachLlmClickHandlers();
    }
    if (llmState.logs.length === 0 && reset) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-muted" style="text-align:center;padding:2rem">No logs found</td></tr>';
    }

    const moreWrap = $('llm-more-wrap');
    if (moreWrap) moreWrap.style.display = llmState.hasMore ? '' : 'none';
  } catch (err) {
    toast('Failed to load LLM logs', 'error');
    console.error(err);
  } finally {
    llmState.loading = false;
  }
}

function attachLlmClickHandlers() {
  document.querySelectorAll('#llm-tbody .clickable-row').forEach(row => {
    row.onclick = () => toggleLlmExpand(row.dataset.llmId);
  });
}

// ============================================================
// Heartbeat tab
// ============================================================
async function loadHeartbeat() {
  try {
    const data = await api('/api/debug/heartbeat-logs');
    const logs = data?.logs || [];
    hbState.logs = logs;
    hbState.loaded = true;

    const list = $('hb-list');
    if (!list) return;

    if (logs.length === 0) {
      list.innerHTML = '<div class="text-muted text-sm" style="padding:1rem;text-align:center">No heartbeat logs</div>';
      return;
    }

    list.innerHTML = logs.map(entry => {
      if (typeof entry === 'string') {
        return `<div class="hb-entry">${esc(entry)}</div>`;
      }
      // Object format — show timestamp + summary
      const time = entry.timestamp ? fmtTime(entry.timestamp) : '';
      const text = entry.message || entry.text || JSON.stringify(entry);
      return `<div class="hb-entry"><span class="mono text-xs">${time}</span> ${esc(text)}</div>`;
    }).join('');
  } catch (err) {
    toast('Failed to load heartbeat logs', 'error');
    console.error(err);
    const list = $('hb-list');
    if (list) list.innerHTML = '<div class="text-muted text-sm" style="padding:1rem;text-align:center">Failed to load</div>';
  }
}

// ============================================================
// Tab switching
// ============================================================
function switchTab(tab) {
  if (tab === activeTab) return;
  activeTab = tab;

  // Update tab buttons
  document.querySelectorAll('.log-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });

  // Show/hide panels
  document.querySelectorAll('.log-tab-panel').forEach(el => {
    el.classList.toggle('active', el.id === `panel-${tab}`);
  });

  // Load data on tab switch
  if (tab === 'conversation' && convState.logs.length === 0) {
    loadConversation(true);
  } else if (tab === 'llm' && llmState.logs.length === 0) {
    loadLlm(true);
  } else if (tab === 'heartbeat' && !hbState.loaded) {
    loadHeartbeat();
  }
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  // Tab click handlers
  document.querySelectorAll('.log-tab').forEach(el => {
    el.addEventListener('click', () => switchTab(el.dataset.tab));
  });

  // Conversation filters
  $('conv-search')?.addEventListener('click', () => loadConversation(true));
  $('conv-keyword')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadConversation(true);
  });
  $('conv-channel')?.addEventListener('change', () => loadConversation(true));
  $('conv-botonly')?.addEventListener('change', () => loadConversation(true));
  $('conv-more')?.addEventListener('click', () => loadConversation(false));

  // LLM filters
  $('llm-provider')?.addEventListener('change', () => loadLlm(true));
  $('llm-more')?.addEventListener('click', () => loadLlm(false));

  // Heartbeat refresh
  $('hb-refresh')?.addEventListener('click', () => {
    hbState.loaded = false;
    loadHeartbeat();
  });

  // Load default tab data
  await loadConversation(true);
}

export function unmount() {
  // Reset state for clean re-mount
  activeTab = 'conversation';
  convState = { logs: [], offset: 0, loading: false, hasMore: true };
  llmState  = { logs: [], offset: 0, loading: false, hasMore: true };
  hbState   = { logs: [], loaded: false };
}
