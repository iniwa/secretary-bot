/** Memory page — tabbed interface for AI Memory and People Memory. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeTab = 'ai_memory';
let items = [];
let total = 0;
let offset = 0;
let hasMore = true;
let loading = false;

const PAGE_LIMIT = 20;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderMetadata(metadata) {
  if (!metadata || typeof metadata !== 'object') return '';
  // user_name があれば user_id を非表示にし、user_name を「User」ラベルで表示
  const display = { ...metadata };
  if (display.user_name) {
    delete display.user_id;
    display.user = display.user_name;
    delete display.user_name;
  }
  const entries = Object.entries(display).filter(([, v]) => v != null && v !== '');
  if (!entries.length) return '';
  return `<div class="mem-meta">${entries.map(([k, v]) =>
    `<span class="mem-meta-pair"><span class="mem-meta-key">${esc(k)}:</span> <span class="mem-meta-val">${esc(String(v))}</span></span>`
  ).join('')}</div>`;
}

function memoryCardHtml(item) {
  return `
  <div class="card mem-card" data-doc-id="${esc(item.id)}">
    <div class="mem-card-body">
      <pre class="mem-document">${esc(item.text || '')}</pre>
      ${renderMetadata(item.metadata)}
    </div>
    <div class="mem-card-footer">
      <span class="mem-doc-id">${esc(item.id)}</span>
      <button class="btn btn-sm btn-danger" data-action="delete">Delete</button>
    </div>
  </div>`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .mem-tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }
  .mem-tab {
    padding: 0.4rem 1rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 0.8125rem;
    font-weight: 500;
    transition: all var(--ease);
  }
  .mem-tab:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .mem-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .mem-tab-panel {
    display: none;
  }
  .mem-tab-panel.active {
    display: block;
  }
  .mem-stats {
    font-size: 0.8125rem;
    color: var(--text-muted);
    margin-bottom: 1rem;
  }
  .mem-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .mem-card {
    padding: 1rem 1.25rem;
  }
  .mem-card-body {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .mem-document {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: inherit;
    font-size: 0.9rem;
    line-height: 1.6;
    color: var(--text);
    background: none;
    border: none;
    padding: 0;
  }
  .mem-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.75rem;
  }
  .mem-meta-pair {
    font-size: 0.7rem;
    color: var(--text-muted);
  }
  .mem-meta-key {
    opacity: 0.7;
  }
  .mem-meta-val {
    color: var(--text-secondary);
  }
  .mem-card-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--border);
  }
  .mem-doc-id {
    font-size: 0.65rem;
    color: var(--text-muted);
    font-family: monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 60%;
  }
  .mem-empty {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
    font-size: 0.9rem;
  }
  .load-more-wrap {
    text-align: center;
    padding: 1rem 0;
  }

  @media (max-width: 600px) {
    .mem-card {
      padding: 0.75rem;
    }
  }
</style>

<div class="mem-page">
  <div class="mem-tabs">
    <button class="mem-tab active" data-tab="ai_memory">AI Memory</button>
    <button class="mem-tab" data-tab="people_memory">People Memory</button>
  </div>

  <!-- AI Memory Tab -->
  <div class="mem-tab-panel active" id="panel-ai_memory">
    <div class="mem-stats" id="mem-stats-ai_memory"></div>
    <div class="mem-list" id="mem-list-ai_memory">
      <div class="mem-empty">Loading...</div>
    </div>
    <div class="load-more-wrap" id="mem-more-wrap-ai_memory" style="display:none">
      <button class="btn btn-sm" id="mem-more-ai_memory">Load more</button>
    </div>
  </div>

  <!-- People Memory Tab -->
  <div class="mem-tab-panel" id="panel-people_memory">
    <div class="mem-stats" id="mem-stats-people_memory"></div>
    <div class="mem-list" id="mem-list-people_memory">
      <div class="mem-empty">Loading...</div>
    </div>
    <div class="load-more-wrap" id="mem-more-wrap-people_memory" style="display:none">
      <button class="btn btn-sm" id="mem-more-people_memory">Load more</button>
    </div>
  </div>
</div>`;
}

// ============================================================
// Per-tab state
// ============================================================
const tabState = {
  ai_memory:     { items: [], total: 0, offset: 0, hasMore: true, loading: false },
  people_memory: { items: [], total: 0, offset: 0, hasMore: true, loading: false },
};

function getState() {
  return tabState[activeTab];
}

// ============================================================
// Data loading
// ============================================================
async function loadMemories(collection, reset = false) {
  const st = tabState[collection];
  if (st.loading) return;
  if (reset) {
    st.offset = 0;
    st.items = [];
    st.hasMore = true;
  }
  if (!st.hasMore) return;

  st.loading = true;
  try {
    const params = { limit: PAGE_LIMIT, offset: st.offset };
    const data = await api(`/api/memory/${collection}`, { params });
    const list = data?.items || [];
    st.total = data?.total ?? 0;

    st.items = reset ? list : st.items.concat(list);
    st.offset += list.length;
    st.hasMore = st.offset < st.total;

    renderTab(collection);
  } catch (err) {
    toast('Failed to load memories: ' + err.message, 'error');
    console.error(err);
  } finally {
    st.loading = false;
  }
}

// ============================================================
// Rendering
// ============================================================
function renderTab(collection) {
  const st = tabState[collection];

  // Stats
  const statsEl = $(`mem-stats-${collection}`);
  if (statsEl) {
    statsEl.textContent = `${st.total} memories`;
  }

  // List
  const listEl = $(`mem-list-${collection}`);
  if (listEl) {
    if (!st.items.length) {
      listEl.innerHTML = '<div class="mem-empty">No memories found.</div>';
    } else {
      listEl.innerHTML = st.items.map(memoryCardHtml).join('');
    }
  }

  // Load more button
  const moreWrap = $(`mem-more-wrap-${collection}`);
  if (moreWrap) {
    moreWrap.style.display = st.hasMore ? '' : 'none';
  }
}

// ============================================================
// Delete
// ============================================================
async function deleteMemory(collection, docId) {
  if (!confirm('Delete this memory?')) return;
  try {
    await api(`/api/memory/${collection}/${docId}`, { method: 'DELETE' });
    toast('Memory deleted', 'success');
    await loadMemories(collection, true);
  } catch (err) {
    toast('Failed to delete memory: ' + err.message, 'error');
  }
}

// ============================================================
// Tab switching
// ============================================================
function switchTab(tab) {
  if (tab === activeTab) return;
  activeTab = tab;

  document.querySelectorAll('.mem-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  document.querySelectorAll('.mem-tab-panel').forEach(el => {
    el.classList.toggle('active', el.id === `panel-${tab}`);
  });

  const st = tabState[tab];
  if (st.items.length === 0 && st.hasMore) {
    loadMemories(tab, true);
  }
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  // Tab switching
  document.querySelectorAll('.mem-tab').forEach(el => {
    el.addEventListener('click', () => switchTab(el.dataset.tab));
  });

  // Delegated delete handler for both lists
  for (const collection of ['ai_memory', 'people_memory']) {
    $(`mem-list-${collection}`)?.addEventListener('click', e => {
      const btn = e.target.closest('[data-action="delete"]');
      if (!btn) return;
      const card = btn.closest('.mem-card');
      if (!card) return;
      deleteMemory(collection, card.dataset.docId);
    });

    // Load more
    $(`mem-more-${collection}`)?.addEventListener('click', () => {
      loadMemories(collection, false);
    });
  }

  // Initial load for active tab
  await loadMemories(activeTab, true);
}

export function unmount() {
  activeTab = 'ai_memory';
  for (const key of Object.keys(tabState)) {
    tabState[key] = { items: [], total: 0, offset: 0, hasMore: true, loading: false };
  }
}
