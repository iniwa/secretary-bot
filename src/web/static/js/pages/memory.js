/** Memory page — tabbed interface for AI Memory, People Memory, Conversation Log. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeTab = 'ai_memory';

const TABS = ['ai_memory', 'people_memory', 'conversation_log'];
const TAB_LABELS = {
  ai_memory: 'AI Memory',
  people_memory: 'People Memory',
  conversation_log: 'Conversation Log',
};

const PAGE_LIMIT = 20;

const tabState = {};
function resetTabState(tab) {
  tabState[tab] = {
    items: [], total: 0, offset: 0, hasMore: true, loading: false,
    searchQuery: '', searchMode: false, filterSource: '',
  };
}
TABS.forEach(t => resetTabState(t));

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatDate(str) {
  if (!str) return '';
  try {
    const d = new Date(str.replace(' ', 'T'));
    return d.toLocaleDateString('ja-JP') + ' ' + d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
  } catch { return str; }
}

function renderMetadata(metadata, collection) {
  if (!metadata || typeof metadata !== 'object') return '';
  const display = { ...metadata };
  // user_name があれば user_id を非表示にし、user_name を「User」ラベルで表示
  if (display.user_name) {
    delete display.user_id;
    display.user = display.user_name;
    delete display.user_name;
  }
  // created_at を読みやすい形式に
  if (display.created_at) {
    display.created_at = formatDate(display.created_at);
  }
  const entries = Object.entries(display).filter(([, v]) => v != null && v !== '');
  if (!entries.length) return '';
  return `<div class="mem-meta">${entries.map(([k, v]) =>
    `<span class="mem-meta-pair"><span class="mem-meta-key">${esc(k)}:</span> <span class="mem-meta-val">${esc(String(v))}</span></span>`
  ).join('')}</div>`;
}

function memoryCardHtml(item, collection) {
  const distHtml = item.distance != null
    ? `<span class="mem-distance" title="類似度スコア（低いほど近い）">score: ${item.distance.toFixed(3)}</span>`
    : '';
  return `
  <div class="card mem-card" data-doc-id="${esc(item.id)}">
    <div class="mem-card-body">
      <pre class="mem-document">${esc(item.text || '')}</pre>
      ${renderMetadata(item.metadata, collection)}
    </div>
    <div class="mem-card-footer">
      <span class="mem-doc-id">${esc(item.id)}${distHtml ? ' · ' + distHtml : ''}</span>
      <button class="btn btn-sm btn-danger" data-action="delete">Delete</button>
    </div>
  </div>`;
}

// ============================================================
// Source options per collection
// ============================================================
function getSourceOptions(collection) {
  if (collection === 'ai_memory') return ['inner_mind', 'conversation'];
  if (collection === 'conversation_log') return ['sqlite_sync', 'inner_mind'];
  return [];
}

// ============================================================
// Render
// ============================================================
export function render() {
  const tabsHtml = TABS.map(t =>
    `<button class="mem-tab${t === activeTab ? ' active' : ''}" data-tab="${t}">${TAB_LABELS[t]}</button>`
  ).join('');

  const panelsHtml = TABS.map(t => {
    const sourceOpts = getSourceOptions(t);
    const filterHtml = sourceOpts.length > 0
      ? `<select class="mem-filter-source" id="mem-filter-source-${t}">
           <option value="">All sources</option>
           ${sourceOpts.map(s => `<option value="${s}">${s}</option>`).join('')}
         </select>`
      : '';

    return `
    <div class="mem-tab-panel${t === activeTab ? ' active' : ''}" id="panel-${t}">
      <div class="mem-toolbar">
        <div class="mem-search-wrap">
          <input type="text" class="mem-search" id="mem-search-${t}"
                 placeholder="セマンティック検索..." />
          <button class="btn btn-sm" id="mem-search-btn-${t}">検索</button>
          <button class="btn btn-sm btn-muted mem-search-clear" id="mem-clear-${t}" style="display:none">クリア</button>
        </div>
        ${filterHtml}
      </div>
      <div class="mem-stats" id="mem-stats-${t}"></div>
      <div class="mem-list" id="mem-list-${t}">
        <div class="mem-empty">Loading...</div>
      </div>
      <div class="load-more-wrap" id="mem-more-wrap-${t}" style="display:none">
        <button class="btn btn-sm" id="mem-more-${t}">Load more</button>
      </div>
    </div>`;
  }).join('');

  return `
<style>
  .mem-tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
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
  .mem-toolbar {
    display: flex;
    gap: 0.75rem;
    margin-bottom: 1rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .mem-search-wrap {
    display: flex;
    gap: 0.4rem;
    flex: 1;
    min-width: 200px;
  }
  .mem-search {
    flex: 1;
    padding: 0.4rem 0.75rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--text);
    font-size: 0.8125rem;
  }
  .mem-search:focus {
    outline: none;
    border-color: var(--accent);
  }
  .mem-filter-source {
    padding: 0.4rem 0.6rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--text);
    font-size: 0.8125rem;
    cursor: pointer;
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
    max-width: 70%;
  }
  .mem-distance {
    color: var(--accent);
    font-weight: 500;
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
  .btn-muted {
    opacity: 0.6;
  }
  .btn-muted:hover {
    opacity: 1;
  }

  @media (max-width: 600px) {
    .mem-card {
      padding: 0.75rem;
    }
    .mem-toolbar {
      flex-direction: column;
    }
    .mem-search-wrap {
      width: 100%;
    }
  }
</style>

<div class="mem-page">
  <div class="mem-tabs">${tabsHtml}</div>
  ${panelsHtml}
</div>`;
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
    if (st.searchMode && st.searchQuery) {
      // Semantic search
      const data = await api(`/api/memory/${collection}/search`, {
        params: { q: st.searchQuery, n: 50 },
      });
      let items = data?.items || [];
      // Client-side source filter
      if (st.filterSource) {
        items = items.filter(it => (it.metadata?.source) === st.filterSource);
      }
      st.items = items;
      st.total = items.length;
      st.hasMore = false;
    } else {
      // Normal listing
      const params = { limit: PAGE_LIMIT, offset: st.offset };
      const data = await api(`/api/memory/${collection}`, { params });
      let list = data?.items || [];
      // Client-side source filter
      if (st.filterSource) {
        list = list.filter(it => (it.metadata?.source) === st.filterSource);
      }
      st.total = data?.total ?? 0;
      st.items = reset ? list : st.items.concat(list);
      st.offset += (data?.items || []).length; // use unfiltered count for offset
      st.hasMore = st.offset < st.total;
    }

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
    const mode = st.searchMode ? `"${st.searchQuery}" の検索結果: ` : '';
    const filterInfo = st.filterSource ? ` (source: ${st.filterSource})` : '';
    statsEl.textContent = `${mode}${st.items.length}${st.searchMode ? '' : ' / ' + st.total} memories${filterInfo}`;
  }

  // List
  const listEl = $(`mem-list-${collection}`);
  if (listEl) {
    if (!st.items.length) {
      listEl.innerHTML = `<div class="mem-empty">${st.searchMode ? '検索結果がありません。' : 'No memories found.'}</div>`;
    } else {
      listEl.innerHTML = st.items.map(it => memoryCardHtml(it, collection)).join('');
    }
  }

  // Load more button
  const moreWrap = $(`mem-more-wrap-${collection}`);
  if (moreWrap) {
    moreWrap.style.display = (st.hasMore && !st.searchMode) ? '' : 'none';
  }

  // Clear button visibility
  const clearBtn = $(`mem-clear-${collection}`);
  if (clearBtn) {
    clearBtn.style.display = st.searchMode ? '' : 'none';
  }
}

// ============================================================
// Search
// ============================================================
function doSearch(collection) {
  const input = $(`mem-search-${collection}`);
  const query = input?.value?.trim() || '';
  const st = tabState[collection];
  if (!query) {
    clearSearch(collection);
    return;
  }
  st.searchQuery = query;
  st.searchMode = true;
  loadMemories(collection, true);
}

function clearSearch(collection) {
  const input = $(`mem-search-${collection}`);
  if (input) input.value = '';
  const st = tabState[collection];
  st.searchQuery = '';
  st.searchMode = false;
  loadMemories(collection, true);
}

// ============================================================
// Delete
// ============================================================
async function deleteMemory(collection, docId) {
  if (!confirm('Delete this memory?')) return;
  try {
    await api(`/api/memory/${collection}/${docId}`, { method: 'DELETE' });
    toast('Memory deleted', 'success');
    const st = tabState[collection];
    if (st.searchMode) {
      st.items = st.items.filter(it => it.id !== docId);
      st.total = st.items.length;
      renderTab(collection);
    } else {
      await loadMemories(collection, true);
    }
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

  for (const collection of TABS) {
    // Delete handler
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

    // Search button
    $(`mem-search-btn-${collection}`)?.addEventListener('click', () => {
      doSearch(collection);
    });

    // Search on Enter
    $(`mem-search-${collection}`)?.addEventListener('keydown', e => {
      if (e.key === 'Enter') doSearch(collection);
    });

    // Clear button
    $(`mem-clear-${collection}`)?.addEventListener('click', () => {
      clearSearch(collection);
    });

    // Source filter
    $(`mem-filter-source-${collection}`)?.addEventListener('change', e => {
      const st = tabState[collection];
      st.filterSource = e.target.value;
      loadMemories(collection, true);
    });
  }

  // Initial load for active tab
  await loadMemories(activeTab, true);
}

export function unmount() {
  activeTab = 'ai_memory';
  TABS.forEach(t => resetTabState(t));
}
