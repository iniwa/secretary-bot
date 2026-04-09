/** Todo page. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeFilter = 'undone'; // 'undone' | 'done' | 'all'
let items = [];

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mi}`;
}

function fmtDateShort(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const y = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${mm}-${dd}`;
}

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function dueDateClass(dueDateStr) {
  if (!dueDateStr) return '';
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(dueDateStr);
  const dueDay = new Date(due.getFullYear(), due.getMonth(), due.getDate());
  if (dueDay < today) return 'due-overdue';
  if (dueDay.getTime() === today.getTime()) return 'due-today';
  return 'due-future';
}

function dueDateLabel(dueDateStr) {
  if (!dueDateStr) return '';
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(dueDateStr);
  const dueDay = new Date(due.getFullYear(), due.getMonth(), due.getDate());
  const diff = Math.round((dueDay - today) / 86400000);
  if (diff < 0) return `${Math.abs(diff)}d overdue`;
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  return `in ${diff}d`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .todo-tabs {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 1rem;
  }
  .todo-tab {
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
  .todo-tab:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .todo-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .todo-tab .tab-count {
    margin-left: 0.35rem;
    font-size: 0.6875rem;
    opacity: 0.75;
  }
  .todo-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .todo-item {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 0.85rem 1rem;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    transition: border-color var(--ease);
  }
  .todo-item:hover {
    border-color: var(--border-hover);
  }
  .todo-checkbox {
    flex-shrink: 0;
    width: 20px;
    height: 20px;
    margin-top: 1px;
    border: 2px solid var(--border-hover);
    border-radius: 4px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all var(--ease);
    background: transparent;
    padding: 0;
  }
  .todo-checkbox:hover {
    border-color: var(--accent);
    background: var(--accent-muted);
  }
  .todo-checkbox.checked {
    background: var(--accent);
    border-color: var(--accent);
  }
  .todo-checkbox.checked::after {
    content: '';
    display: block;
    width: 5px;
    height: 9px;
    border: solid #fff;
    border-width: 0 2px 2px 0;
    transform: rotate(45deg);
    margin-bottom: 2px;
  }
  .todo-body {
    flex: 1;
    min-width: 0;
  }
  .todo-title {
    font-size: 0.9rem;
    color: var(--text-primary);
    line-height: 1.4;
    word-break: break-word;
  }
  .todo-item.is-done .todo-title {
    text-decoration: line-through;
    color: var(--text-muted);
  }
  .todo-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.3rem;
    font-size: 0.75rem;
    color: var(--text-muted);
  }
  .todo-due {
    font-weight: 500;
  }
  .due-overdue { color: var(--error); }
  .due-today { color: var(--warning); }
  .due-future { color: var(--text-secondary); }
  .todo-actions {
    display: flex;
    gap: 0.35rem;
    flex-shrink: 0;
    margin-top: 1px;
  }
  .todo-actions .btn {
    padding: 0.25rem 0.55rem;
    font-size: 0.75rem;
  }
  .todo-item.is-done .todo-actions .btn-edit {
    display: none;
  }
  /* Inline edit */
  .todo-edit-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    width: 100%;
  }
  .todo-edit-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .todo-edit-row .form-input {
    font-size: 0.8125rem;
  }
  .todo-edit-row .form-input[type="text"] {
    flex: 1;
  }
  .todo-edit-row .form-input[type="date"] {
    width: auto;
    max-width: 160px;
  }
  .todo-edit-actions {
    display: flex;
    gap: 0.35rem;
  }
  .todo-edit-actions .btn {
    padding: 0.25rem 0.55rem;
    font-size: 0.75rem;
  }
  /* Empty state */
  .todo-empty {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
    font-size: 0.875rem;
  }
  .todo-empty-icon {
    font-size: 2rem;
    margin-bottom: 0.5rem;
    opacity: 0.5;
  }
  @media (max-width: 600px) {
    .todo-item {
      flex-wrap: wrap;
    }
    .todo-actions {
      width: 100%;
      justify-content: flex-end;
      margin-top: 0.5rem;
    }
  }
</style>

<div class="todo-page">
  <div class="todo-tabs" id="todo-tabs">
    <button class="todo-tab active" data-filter="undone">Todo<span class="tab-count" id="cnt-undone"></span></button>
    <button class="todo-tab" data-filter="done">Done<span class="tab-count" id="cnt-done"></span></button>
    <button class="todo-tab" data-filter="all">All<span class="tab-count" id="cnt-all"></span></button>
  </div>
  <div class="todo-list" id="todo-list"></div>
</div>`;
}

// ============================================================
// List rendering
// ============================================================
function renderItem(item) {
  const doneClass = item.done ? 'is-done' : '';
  const checkClass = item.done ? 'checked' : '';
  const dueCls = item.done ? '' : dueDateClass(item.due_date);
  const dueText = item.due_date ? fmtDateShort(item.due_date) : '';
  const dueLbl = item.done ? '' : dueDateLabel(item.due_date);

  return `<div class="todo-item ${doneClass}" data-id="${item.id}">
    <button class="todo-checkbox ${checkClass}" data-action="toggle" data-id="${item.id}" title="${item.done ? 'Mark undone' : 'Mark done'}"></button>
    <div class="todo-body">
      <div class="todo-title">${esc(item.title)}</div>
      <div class="todo-meta">
        ${dueText ? `<span class="todo-due ${dueCls}">${esc(dueText)}${dueLbl ? ' (' + esc(dueLbl) + ')' : ''}</span>` : ''}
        <span>created ${fmtDate(item.created_at)}</span>
        ${item.done_at ? `<span>done ${fmtDate(item.done_at)}</span>` : ''}
      </div>
    </div>
    <div class="todo-actions">
      <button class="btn btn-sm btn-edit" data-action="edit" data-id="${item.id}">Edit</button>
      <button class="btn btn-sm btn-danger" data-action="delete" data-id="${item.id}">Delete</button>
    </div>
  </div>`;
}

function renderEditForm(item) {
  const dueVal = item.due_date ? fmtDateShort(item.due_date) : '';
  return `<div class="todo-item" data-id="${item.id}">
    <button class="todo-checkbox ${item.done ? 'checked' : ''}" disabled style="opacity:0.5"></button>
    <div class="todo-edit-form">
      <div class="todo-edit-row">
        <input type="text" class="form-input" id="edit-title-${item.id}" value="${esc(item.title)}">
        <input type="date" class="form-input" id="edit-due-${item.id}" value="${esc(dueVal)}">
      </div>
      <div class="todo-edit-actions">
        <button class="btn btn-sm btn-primary" data-action="save" data-id="${item.id}">Save</button>
        <button class="btn btn-sm" data-action="cancel" data-id="${item.id}">Cancel</button>
      </div>
    </div>
  </div>`;
}

function renderList() {
  const el = $('todo-list');
  if (!el) return;
  if (items.length === 0) {
    const msgs = {
      undone: 'No pending todos.',
      done: 'No completed todos.',
      all: 'No todos yet.',
    };
    el.innerHTML = `<div class="todo-empty">
      <div class="todo-empty-icon">---</div>
      <div>${msgs[activeFilter]}</div>
    </div>`;
    return;
  }
  el.innerHTML = items.map(renderItem).join('');
}

function updateCounts() {
  // counts are not available from filtered endpoints — show count of loaded items only
  const cnt = $('cnt-' + activeFilter);
  if (cnt) cnt.textContent = items.length > 0 ? `(${items.length})` : '';
}

// ============================================================
// Data fetching
// ============================================================
async function fetchItems() {
  const params = {};
  if (activeFilter === 'undone') params.done = 0;
  else if (activeFilter === 'done') params.done = 1;

  try {
    const data = await api('/api/units/todos', { params });
    items = data.items || [];
  } catch (e) {
    toast('Failed to load todos: ' + e.message, 'error');
    items = [];
  }
  renderList();
  updateCounts();
}

// ============================================================
// Actions
// ============================================================
let editingId = null;

async function toggleDone(id) {
  try {
    await api(`/api/units/todos/${id}/done`, { method: 'POST' });
    toast('Todo updated', 'success');
    await fetchItems();
  } catch (e) {
    toast('Failed to update: ' + e.message, 'error');
  }
}

function startEdit(id) {
  editingId = id;
  const el = $('todo-list');
  if (!el) return;
  const item = items.find(i => i.id === id);
  if (!item) return;

  // Replace only the target item's DOM
  const nodes = el.querySelectorAll('.todo-item');
  for (const node of nodes) {
    if (String(node.dataset.id) === String(id)) {
      node.outerHTML = renderEditForm(item);
      break;
    }
  }

  // Focus title input
  const titleInput = document.getElementById(`edit-title-${id}`);
  if (titleInput) {
    titleInput.focus();
    titleInput.select();
  }
}

function cancelEdit() {
  editingId = null;
  renderList();
}

async function saveEdit(id) {
  const titleEl = document.getElementById(`edit-title-${id}`);
  const dueEl = document.getElementById(`edit-due-${id}`);
  if (!titleEl) return;

  const title = titleEl.value.trim();
  if (!title) {
    toast('Title cannot be empty', 'error');
    return;
  }

  const body = { title, due_date: dueEl?.value || null };
  try {
    await api(`/api/units/todos/${id}`, { method: 'PUT', body });
    toast('Todo saved', 'success');
    editingId = null;
    await fetchItems();
  } catch (e) {
    toast('Failed to save: ' + e.message, 'error');
  }
}

async function deleteItem(id) {
  if (!confirm('Delete this todo?')) return;
  try {
    await api(`/api/units/todos/${id}`, { method: 'DELETE' });
    toast('Todo deleted', 'success');
    await fetchItems();
  } catch (e) {
    toast('Failed to delete: ' + e.message, 'error');
  }
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  // Tab switching
  const tabsEl = $('todo-tabs');
  if (tabsEl) {
    tabsEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.todo-tab');
      if (!btn) return;
      const filter = btn.dataset.filter;
      if (filter === activeFilter) return;
      activeFilter = filter;
      tabsEl.querySelectorAll('.todo-tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      editingId = null;
      fetchItems();
    });
  }

  // Delegated click handler for todo actions
  const listEl = $('todo-list');
  if (listEl) {
    listEl.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;
      const id = btn.dataset.id;
      if (!id) return;

      switch (action) {
        case 'toggle':
          toggleDone(id);
          break;
        case 'edit':
          startEdit(id);
          break;
        case 'delete':
          deleteItem(id);
          break;
        case 'save':
          saveEdit(id);
          break;
        case 'cancel':
          cancelEdit();
          break;
      }
    });

    // Save on Enter in edit mode
    listEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && editingId !== null) {
        const target = e.target;
        if (target.tagName === 'INPUT' && target.id?.startsWith('edit-')) {
          e.preventDefault();
          saveEdit(editingId);
        }
      }
      if (e.key === 'Escape' && editingId !== null) {
        cancelEdit();
      }
    });
  }

  // Initial fetch
  await fetchItems();
}
