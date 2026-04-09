/** Reminder page. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeFilter = 'active';
let editingId = null;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function fmtTime(iso) {
  if (!iso) return '---';
  const d = new Date(iso);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${yyyy}/${mm}/${dd} ${hh}:${mi}`;
}

function toDatetimeLocal(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function statusBadge(item) {
  if (item.done_at) return '<span class="badge badge-muted">Done</span>';
  if (item.snoozed_until) return '<span class="badge badge-info">Snoozed</span>';
  if (item.notified) return '<span class="badge badge-warning">Notified</span>';
  if (item.active) return '<span class="badge badge-success">Active</span>';
  return '<span class="badge badge-muted">Inactive</span>';
}

function repeatLabel(item) {
  if (!item.repeat_type || item.repeat_type === 'none') return '';
  let label = item.repeat_type;
  if (item.repeat_interval && item.repeat_interval > 1) {
    label += ` (x${item.repeat_interval})`;
  }
  return `<span class="badge badge-accent">${esc(label)}</span>`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .reminder-tabs {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 1rem;
  }
  .reminder-tab {
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
  .reminder-tab:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .reminder-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .reminder-actions {
    display: flex;
    gap: 0.35rem;
    flex-wrap: nowrap;
  }
  .snooze-info {
    font-size: 0.6875rem;
    color: var(--info);
    margin-top: 0.2rem;
  }
  .edit-row td {
    background: var(--bg-base);
  }
  .edit-fields {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .edit-fields .form-input {
    width: auto;
    min-width: 200px;
  }
  .edit-fields .edit-msg-input {
    flex: 1;
    min-width: 200px;
  }
  .edit-buttons {
    display: flex;
    gap: 0.3rem;
  }
  .reminder-message-cell {
    max-width: 360px;
    word-break: break-word;
  }
  .empty-state {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
    font-size: 0.875rem;
  }
</style>

<div class="reminder-page">
  <div class="reminder-tabs">
    <button class="reminder-tab active" data-filter="active">Active</button>
    <button class="reminder-tab" data-filter="all">All</button>
  </div>

  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Message</th>
            <th>Scheduled</th>
            <th>Repeat</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="rem-tbody">
          <tr><td colspan="5" class="empty-state">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>`;
}

// ============================================================
// Render rows
// ============================================================
function renderRows(items) {
  if (!items || items.length === 0) {
    return '<tr><td colspan="5" class="empty-state">No reminders found</td></tr>';
  }

  return items.map(item => {
    const isEditing = editingId === item.id;

    if (isEditing) {
      return `<tr class="edit-row" data-rid="${item.id}">
        <td colspan="5">
          <div class="edit-fields">
            <input type="text" class="form-input edit-msg-input" id="edit-msg-${item.id}" value="${esc(item.message || '')}">
            <input type="datetime-local" class="form-input" id="edit-time-${item.id}" value="${toDatetimeLocal(item.remind_at)}">
            <div class="edit-buttons">
              <button class="btn btn-primary btn-sm" data-save="${item.id}">Save</button>
              <button class="btn btn-sm" data-cancel="${item.id}">Cancel</button>
            </div>
          </div>
        </td>
      </tr>`;
    }

    const snoozeHtml = item.snoozed_until
      ? `<div class="snooze-info">Snoozed x${item.snooze_count || 1} until ${fmtTime(item.snoozed_until)}</div>`
      : '';

    const isDone = !!item.done_at;

    return `<tr data-rid="${item.id}">
      <td>${statusBadge(item)}</td>
      <td class="reminder-message-cell">${esc(item.message || '')}${snoozeHtml}</td>
      <td class="mono text-xs">${fmtTime(item.remind_at)}</td>
      <td>${repeatLabel(item)}</td>
      <td>
        <div class="reminder-actions">
          ${isDone ? '' : `<button class="btn btn-sm" data-edit="${item.id}">Edit</button>`}
          ${isDone ? '' : `<button class="btn btn-sm btn-primary" data-done="${item.id}">Done</button>`}
          <button class="btn btn-sm btn-danger" data-delete="${item.id}">Delete</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// ============================================================
// Data loading
// ============================================================
async function loadReminders() {
  const tbody = $('rem-tbody');
  if (!tbody) return;

  try {
    const params = {};
    if (activeFilter === 'active') params.active = 1;
    const data = await api('/api/units/reminders', { params });
    const items = data?.items || [];
    tbody.innerHTML = renderRows(items);
    attachRowHandlers();
  } catch (err) {
    console.error('Failed to load reminders:', err);
    toast('Failed to load reminders', 'error');
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">Failed to load</td></tr>';
  }
}

// ============================================================
// Actions
// ============================================================
async function handleEdit(rid) {
  editingId = rid;
  await loadReminders();
  // Focus the message input after re-render
  const msgInput = $(`edit-msg-${rid}`);
  if (msgInput) msgInput.focus();
}

async function handleSave(rid) {
  const msgInput = $(`edit-msg-${rid}`);
  const timeInput = $(`edit-time-${rid}`);
  if (!msgInput || !timeInput) return;

  const message = msgInput.value.trim();
  const remindAt = timeInput.value;

  if (!message) {
    toast('Message is required', 'error');
    return;
  }
  if (!remindAt) {
    toast('Scheduled time is required', 'error');
    return;
  }

  try {
    await api(`/api/units/reminders/${rid}`, {
      method: 'PUT',
      body: { message, remind_at: new Date(remindAt).toISOString() },
    });
    toast('Reminder updated', 'success');
    editingId = null;
    await loadReminders();
  } catch (err) {
    console.error('Failed to update reminder:', err);
    toast('Failed to update reminder', 'error');
  }
}

function handleCancel() {
  editingId = null;
  loadReminders();
}

async function handleDone(rid) {
  if (!confirm('Mark this reminder as done?')) return;
  try {
    await api(`/api/units/reminders/${rid}/done`, { method: 'POST' });
    toast('Reminder marked as done', 'success');
    await loadReminders();
  } catch (err) {
    console.error('Failed to mark reminder done:', err);
    toast('Failed to mark reminder as done', 'error');
  }
}

async function handleDelete(rid) {
  if (!confirm('Delete this reminder? This cannot be undone.')) return;
  try {
    await api(`/api/units/reminders/${rid}`, { method: 'DELETE' });
    toast('Reminder deleted', 'success');
    await loadReminders();
  } catch (err) {
    console.error('Failed to delete reminder:', err);
    toast('Failed to delete reminder', 'error');
  }
}

// ============================================================
// Event delegation
// ============================================================
function attachRowHandlers() {
  const tbody = $('rem-tbody');
  if (!tbody) return;

  tbody.onclick = (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;

    if (btn.dataset.edit) handleEdit(Number(btn.dataset.edit));
    else if (btn.dataset.save) handleSave(Number(btn.dataset.save));
    else if (btn.dataset.cancel) handleCancel();
    else if (btn.dataset.done) handleDone(Number(btn.dataset.done));
    else if (btn.dataset.delete) handleDelete(Number(btn.dataset.delete));
  };
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  // Tab click handlers
  document.querySelectorAll('.reminder-tab').forEach(el => {
    el.addEventListener('click', () => {
      const filter = el.dataset.filter;
      if (filter === activeFilter) return;
      activeFilter = filter;

      document.querySelectorAll('.reminder-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.filter === filter);
      });

      editingId = null;
      loadReminders();
    });
  });

  // Initial load
  await loadReminders();
}

export function unmount() {
  activeFilter = 'active';
  editingId = null;
}
