/** Monologue timeline page. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// Constants
// ============================================================
const INITIAL_LIMIT = 20;
const LOAD_MORE_STEP = 20;

const MOOD_BADGE = {
  curious:   'badge-info',
  calm:      'badge-success',
  talkative: 'badge-accent',
  concerned: 'badge-warning',
  idle:      'badge-muted',
};

// ============================================================
// State
// ============================================================
let currentLimit = INITIAL_LIMIT;
let monologues = [];
let loading = false;
let hasMore = true;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function timeAgo(isoStr) {
  if (!isoStr) return '---';
  const diff = Date.now() - new Date(isoStr).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function fullDatetime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const da = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${y}-${mo}-${da} ${hh}:${mi}:${ss}`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .mono-page {
    max-width: 720px;
    margin: 0 auto;
  }

  .mono-timeline {
    position: relative;
    padding-left: 1.5rem;
  }

  /* Vertical accent line */
  .mono-timeline::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--border-hover);
    border-radius: 1px;
  }

  .mono-entry {
    position: relative;
    margin-bottom: 1rem;
  }

  /* Dot on the timeline line */
  .mono-entry::before {
    content: '';
    position: absolute;
    left: -1.5rem;
    top: 1.35rem;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--border-hover);
    transform: translateX(-3px);
  }

  .mono-entry.spoke::before {
    background: var(--accent);
    box-shadow: 0 0 6px var(--accent-muted);
  }

  .mono-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1rem 1.25rem;
    transition: border-color var(--ease);
  }

  .mono-card:hover {
    border-color: var(--border-hover);
  }

  .mono-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
  }

  .mono-time {
    font-size: 0.75rem;
    color: var(--text-muted);
    cursor: default;
  }

  .mono-spoke-badge {
    font-size: 0.6875rem;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    background: var(--accent-muted);
    color: var(--accent-hover);
    font-weight: 500;
  }

  .mono-text {
    font-size: 0.9rem;
    line-height: 1.6;
    color: var(--text-primary);
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }

  .mono-notified {
    margin-top: 0.625rem;
    padding: 0.625rem 0.875rem;
    border-left: 3px solid var(--accent);
    background: var(--bg-raised);
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    font-size: 0.825rem;
    color: var(--text-secondary);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .mono-notified-label {
    font-size: 0.6875rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.25rem;
    font-weight: 500;
  }

  .mono-load-more {
    text-align: center;
    padding: 1.5rem 0;
  }

  .mono-empty {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
    font-size: 0.9rem;
  }

  .mono-context {
    margin-top: 0.625rem;
  }

  .mono-context summary {
    font-size: 0.75rem;
    color: var(--text-muted);
    cursor: pointer;
    user-select: none;
    font-weight: 500;
    letter-spacing: 0.02em;
  }

  .mono-context summary:hover {
    color: var(--text-secondary);
  }

  .mono-context-list {
    margin-top: 0.5rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-raised);
    border-radius: var(--radius-sm);
    font-size: 0.8rem;
  }

  .mono-context-source {
    margin-bottom: 0.5rem;
  }

  .mono-context-source:last-child {
    margin-bottom: 0;
  }

  .mono-context-source-name {
    font-weight: 600;
    color: var(--text-secondary);
    font-size: 0.75rem;
    margin-bottom: 0.15rem;
  }

  .mono-context-source-text {
    color: var(--text-muted);
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
    max-height: 200px;
    overflow-y: auto;
  }

  .mono-action {
    margin-top: 0.5rem;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.8rem;
  }

  .mono-reasoning {
    color: var(--text-muted);
    font-size: 0.78rem;
    line-height: 1.4;
  }

  .mono-skip-reason {
    font-size: 0.72rem;
    color: var(--text-muted);
    background: var(--bg-raised);
    padding: 0.1rem 0.45rem;
    border-radius: var(--radius-sm);
    font-family: ui-monospace, SFMono-Regular, monospace;
  }

  @media (max-width: 768px) {
    .mono-timeline {
      padding-left: 1.25rem;
    }
    .mono-entry::before {
      left: -1.25rem;
    }
    .mono-card {
      padding: 0.75rem 1rem;
    }
  }
</style>

<div class="mono-page">
  <div class="mono-timeline" id="mono-timeline">
    <div class="mono-empty">Loading...</div>
  </div>
  <div class="mono-load-more" id="mono-load-more" style="display:none">
    <button class="btn btn-sm" id="mono-load-more-btn">Load more</button>
  </div>
</div>`;
}

// ============================================================
// Rendering helpers
// ============================================================
function renderContextBlock(contextJson) {
  if (!contextJson) return '';
  let sources;
  try {
    sources = JSON.parse(contextJson);
  } catch {
    return '';
  }
  if (!Array.isArray(sources) || sources.length === 0) return '';

  const items = sources.map(s =>
    `<div class="mono-context-source">
      <div class="mono-context-source-name">${esc(s.name)}</div>
      <div class="mono-context-source-text">${esc(s.text)}</div>
    </div>`
  ).join('');

  return `<details class="mono-context">
    <summary>コンテキスト</summary>
    <div class="mono-context-list">${items}</div>
  </details>`;
}

function parseActionResult(jsonStr) {
  if (!jsonStr) return null;
  try {
    const parsed = JSON.parse(jsonStr);
    return (parsed && typeof parsed === 'object') ? parsed : null;
  } catch {
    return null;
  }
}

function statusBadgeClass(status) {
  switch (status) {
    case 'executed': return 'badge-accent';
    case 'queued':   return 'badge-warning';
    case 'skipped':  return 'badge-muted';
    case 'failed':   return 'badge-warning';
    default:         return 'badge-muted';
  }
}

function renderEntry(m) {
  const moodClass = MOOD_BADGE[m.mood] || 'badge-muted';
  const spokeClass = m.did_notify ? ' spoke' : '';
  const spokeBadge = m.did_notify
    ? '<span class="mono-spoke-badge">Spoke</span>'
    : '';
  const notifiedBlock = m.notified_message
    ? `<div class="mono-notified">
        <div class="mono-notified-label">Notified</div>
        ${esc(m.notified_message)}
      </div>`
    : '';
  const contextBlock = renderContextBlock(m.context_json);

  let actionBlock = '';
  if (m.action && m.action !== 'no_op') {
    const result = parseActionResult(m.action_result);
    const status = result?.status || '';
    const reason = result?.reason || '';
    const statusBadge = status
      ? `<span class="badge ${statusBadgeClass(status)}">${esc(status)}</span>`
      : '';
    const reasonBadge = reason
      ? `<span class="mono-skip-reason" title="${esc(reason)}">${esc(reason)}</span>`
      : '';
    actionBlock = `<div class="mono-action">
      <span class="badge badge-accent">${esc(m.action)}</span>
      ${statusBadge}
      ${reasonBadge}
      ${m.reasoning ? `<span class="mono-reasoning">${esc(m.reasoning)}</span>` : ''}
      ${m.pending_id ? `<span class="badge badge-warning">pending #${m.pending_id}</span>` : ''}
    </div>`;
  }

  return `<div class="mono-entry${spokeClass}">
    <div class="mono-card">
      <div class="mono-header">
        <span class="badge ${moodClass}">${esc(m.mood || 'unknown')}</span>
        ${spokeBadge}
        <span class="mono-time" title="${esc(fullDatetime(m.created_at))}">${timeAgo(m.created_at)}</span>
      </div>
      <p class="mono-text">${esc(m.monologue)}</p>
      ${actionBlock}
      ${notifiedBlock}
      ${contextBlock}
    </div>
  </div>`;
}

function renderEntries(list) {
  if (!list.length) {
    return '<div class="mono-empty">No monologues yet.</div>';
  }
  return list.map(renderEntry).join('');
}

// ============================================================
// Data loading
// ============================================================
async function loadMonologues(reset = false) {
  if (loading) return;

  if (reset) {
    currentLimit = INITIAL_LIMIT;
    monologues = [];
    hasMore = true;
  }

  loading = true;
  const btn = $('mono-load-more-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Loading...';
  }

  try {
    const data = await api('/api/monologue', { params: { limit: currentLimit } });
    const list = data?.monologues || [];

    // The API returns all results up to limit, so we compare count
    hasMore = list.length >= currentLimit;
    monologues = list;

    const timeline = $('mono-timeline');
    if (timeline) {
      timeline.innerHTML = renderEntries(monologues);
    }

    const moreWrap = $('mono-load-more');
    if (moreWrap) moreWrap.style.display = hasMore ? '' : 'none';
  } catch (err) {
    toast('Failed to load monologues', 'error');
    console.error(err);
  } finally {
    loading = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Load more';
    }
  }
}

async function loadMore() {
  currentLimit += LOAD_MORE_STEP;
  await loadMonologues();
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  $('mono-load-more-btn')?.addEventListener('click', loadMore);
  await loadMonologues(true);
}

export function unmount() {
  currentLimit = INITIAL_LIMIT;
  monologues = [];
  loading = false;
  hasMore = true;
}
