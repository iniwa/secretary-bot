/** Memo page. */
import { api } from '../api.js';
import { toast } from '../app.js';

function $(id) { return document.getElementById(id); }

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderTags(tags) {
  if (!tags) return '';
  const list = tags.split(',').map(t => t.trim()).filter(Boolean);
  if (!list.length) return '';
  return list.map(t => `<span class="badge badge-accent memo-tag">${escapeHtml(t)}</span>`).join('');
}

function memoCardHtml(memo) {
  return `
  <div class="card memo-card" data-memo-id="${memo.id}">
    <div class="memo-card-body">
      <div class="memo-content-area" data-view="display">
        <pre class="memo-content">${escapeHtml(memo.content || '')}</pre>
        <div class="memo-tags">${renderTags(memo.tags)}</div>
      </div>
      <div class="memo-meta">
        <span class="memo-date">${formatDate(memo.created_at)}</span>
      </div>
    </div>
    <div class="memo-actions">
      <button class="btn btn-sm btn-edit" data-action="edit">Edit</button>
      <button class="btn btn-sm btn-append" data-action="append">Append</button>
      <button class="btn btn-sm btn-danger btn-delete" data-action="delete">Delete</button>
    </div>
  </div>`;
}

export function render() {
  return `
<style>
  .memo-search {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.25rem;
  }
  .memo-search .form-input {
    flex: 1;
  }
  .memo-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .memo-card {
    padding: 1rem 1.25rem;
  }
  .memo-card-body {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .memo-content {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: inherit;
    font-size: 0.9rem;
    line-height: 1.6;
    color: var(--text);
    max-height: 20rem;
    overflow-y: auto;
    background: none;
    border: none;
    padding: 0;
  }
  .memo-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .memo-tag {
    font-size: 0.7rem;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
  }
  .memo-meta {
    display: flex;
    justify-content: flex-end;
  }
  .memo-date {
    font-size: 0.7rem;
    color: var(--text-muted);
  }
  .memo-actions {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--border);
  }
  .memo-actions .btn-edit,
  .memo-actions .btn-append {
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    color: var(--text);
  }
  .memo-actions .btn-edit:hover,
  .memo-actions .btn-append:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  /* Edit / Append forms */
  .memo-edit-form,
  .memo-append-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .memo-edit-form textarea,
  .memo-append-form textarea {
    width: 100%;
    min-height: 6rem;
    resize: vertical;
    font-size: 0.875rem;
  }
  .memo-append-form textarea {
    min-height: 3.5rem;
  }
  .memo-form-row {
    display: flex;
    gap: 0.4rem;
    align-items: center;
  }
  .memo-form-row .form-input {
    flex: 1;
  }
  .memo-form-actions {
    display: flex;
    gap: 0.4rem;
    justify-content: flex-end;
  }

  /* Empty state */
  .memo-empty {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
    font-size: 0.95rem;
  }

  @media (max-width: 600px) {
    .memo-card {
      padding: 0.75rem;
    }
    .memo-actions {
      flex-wrap: wrap;
    }
  }
</style>

<div class="memo-page">
  <div class="memo-search">
    <input class="form-input" type="text" id="memo-keyword" placeholder="Search memos..." />
    <button class="btn btn-primary" id="memo-search-btn">Search</button>
  </div>
  <div class="memo-list" id="memo-list">
    <div class="memo-empty">Loading...</div>
  </div>
</div>`;
}

/** State */
let memos = [];

async function loadMemos(keyword) {
  const params = {};
  if (keyword) params.keyword = keyword;
  try {
    const data = await api('/api/units/memos', { params });
    memos = data.items || [];
    renderList();
  } catch (e) {
    toast('Failed to load memos: ' + e.message, 'error');
  }
}

function renderList() {
  const container = $('memo-list');
  if (!container) return;
  if (!memos.length) {
    container.innerHTML = '<div class="memo-empty">No memos found.</div>';
    return;
  }
  container.innerHTML = memos.map(memoCardHtml).join('');
}

function getCardAndMemo(target) {
  const card = target.closest('.memo-card');
  if (!card) return {};
  const id = Number(card.dataset.memoId);
  const memo = memos.find(m => m.id === id);
  return { card, id, memo };
}

function cancelAllForms() {
  document.querySelectorAll('.memo-edit-form, .memo-append-form').forEach(f => f.remove());
  document.querySelectorAll('.memo-content-area[style]').forEach(el => el.style.display = '');
}

function startEdit(card, memo) {
  cancelAllForms();
  const contentArea = card.querySelector('.memo-content-area');
  contentArea.style.display = 'none';

  const form = document.createElement('div');
  form.className = 'memo-edit-form';
  form.innerHTML = `
    <label class="form-label">Content</label>
    <textarea class="form-input memo-edit-content">${escapeHtml(memo.content || '')}</textarea>
    <div class="memo-form-row">
      <label class="form-label" style="margin:0;white-space:nowrap;">Tags</label>
      <input class="form-input memo-edit-tags" type="text" value="${escapeHtml(memo.tags || '')}" placeholder="comma separated" />
    </div>
    <div class="memo-form-actions">
      <button class="btn btn-sm btn-cancel" data-action="cancel-edit">Cancel</button>
      <button class="btn btn-sm btn-primary btn-save" data-action="save-edit">Save</button>
    </div>`;
  contentArea.parentNode.insertBefore(form, contentArea.nextSibling);
}

function startAppend(card) {
  cancelAllForms();

  const form = document.createElement('div');
  form.className = 'memo-append-form';
  form.innerHTML = `
    <label class="form-label">Append text</label>
    <textarea class="form-input memo-append-content" placeholder="Additional text..."></textarea>
    <div class="memo-form-actions">
      <button class="btn btn-sm btn-cancel" data-action="cancel-append">Cancel</button>
      <button class="btn btn-sm btn-primary btn-do-append" data-action="do-append">Append</button>
    </div>`;
  const body = card.querySelector('.memo-card-body');
  body.appendChild(form);
}

async function saveEdit(card, id) {
  const content = card.querySelector('.memo-edit-content')?.value;
  const tags = card.querySelector('.memo-edit-tags')?.value;
  if (content == null) return;
  try {
    await api(`/api/units/memos/${id}`, { method: 'PUT', body: { content, tags } });
    toast('Memo updated', 'success');
    await loadMemos($('memo-keyword')?.value || '');
  } catch (e) {
    toast('Failed to update memo: ' + e.message, 'error');
  }
}

async function doAppend(card, id) {
  const content = card.querySelector('.memo-append-content')?.value;
  if (!content) { toast('Enter text to append', 'info'); return; }
  try {
    await api(`/api/units/memos/${id}/append`, { method: 'POST', body: { content } });
    toast('Text appended', 'success');
    await loadMemos($('memo-keyword')?.value || '');
  } catch (e) {
    toast('Failed to append: ' + e.message, 'error');
  }
}

async function deleteMemo(id) {
  if (!confirm('Delete this memo?')) return;
  try {
    await api(`/api/units/memos/${id}`, { method: 'DELETE' });
    toast('Memo deleted', 'success');
    await loadMemos($('memo-keyword')?.value || '');
  } catch (e) {
    toast('Failed to delete memo: ' + e.message, 'error');
  }
}

export async function mount() {
  // Search
  const searchBtn = $('memo-search-btn');
  const keywordInput = $('memo-keyword');

  searchBtn?.addEventListener('click', () => loadMemos(keywordInput?.value || ''));
  keywordInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter') loadMemos(keywordInput.value || '');
  });

  // Delegated event handling on memo list
  $('memo-list')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const { card, id, memo } = getCardAndMemo(btn);
    if (!card) return;

    switch (action) {
      case 'edit':
        startEdit(card, memo);
        break;
      case 'append':
        startAppend(card);
        break;
      case 'delete':
        deleteMemo(id);
        break;
      case 'save-edit':
        saveEdit(card, id);
        break;
      case 'do-append':
        doAppend(card, id);
        break;
      case 'cancel-edit':
      case 'cancel-append':
        cancelAllForms();
        break;
    }
  });

  // Initial load
  await loadMemos('');
}
