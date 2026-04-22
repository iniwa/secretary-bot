/** Weather subscriptions page. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let subscriptions = [];
let editingId = null;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function padTwo(n) {
  return String(n).padStart(2, '0');
}

function fmtTime(h, m) {
  return `${padTwo(h)}:${padTwo(m)}`;
}

function hourOptions(selected) {
  let html = '';
  for (let i = 0; i < 24; i++) {
    const sel = i === selected ? ' selected' : '';
    html += `<option value="${i}"${sel}>${padTwo(i)}</option>`;
  }
  return html;
}

function minuteOptions(selected) {
  let html = '';
  for (let i = 0; i < 60; i += 5) {
    const sel = i === selected ? ' selected' : '';
    html += `<option value="${i}"${sel}>${padTwo(i)}</option>`;
  }
  // If the current value is not on a 5-minute boundary, include it
  if (selected % 5 !== 0) {
    html += `<option value="${selected}" selected>${padTwo(selected)}</option>`;
  }
  return html;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .weather-page {
    max-width: 900px;
    margin: 0 auto;
  }
  .weather-page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1.25rem;
  }
  .weather-page-header h2 {
    font-size: 1.125rem;
    font-weight: 600;
    color: var(--text-primary);
    margin: 0;
  }
  .weather-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  @media (max-width: 768px) {
    .weather-grid {
      grid-template-columns: 1fr;
    }
  }

  /* Card */
  .weather-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    transition: border-color var(--ease);
    position: relative;
    overflow: hidden;
  }
  .weather-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(90deg, var(--accent), #60a5fa);
    opacity: 0.7;
  }
  .weather-card.inactive::before {
    background: var(--border);
    opacity: 0.4;
  }
  .weather-card:hover {
    border-color: var(--border-hover);
  }
  .weather-card.inactive {
    opacity: 0.6;
  }

  /* Card header */
  .weather-card-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .weather-location {
    font-size: 1.0625rem;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.3;
    word-break: break-word;
    flex: 1;
  }
  .weather-card.inactive .weather-location {
    color: var(--text-muted);
  }

  /* Toggle switch */
  .weather-toggle {
    position: relative;
    width: 40px;
    height: 22px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .weather-toggle input {
    opacity: 0;
    width: 0;
    height: 0;
    position: absolute;
  }
  .weather-toggle-track {
    position: absolute;
    inset: 0;
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    border-radius: 11px;
    transition: background 0.2s, border-color 0.2s;
  }
  .weather-toggle-track::after {
    content: '';
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    background: var(--text-muted);
    border-radius: 50%;
    transition: transform 0.2s, background 0.2s;
  }
  .weather-toggle input:checked + .weather-toggle-track {
    background: var(--accent);
    border-color: var(--accent);
  }
  .weather-toggle input:checked + .weather-toggle-track::after {
    transform: translateX(18px);
    background: #fff;
  }

  /* Notify time */
  .weather-notify-time {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .weather-time-icon {
    font-size: 0.8125rem;
    color: var(--text-muted);
  }
  .weather-time-value {
    font-family: 'Cascadia Code', 'Fira Code', 'SF Mono', monospace;
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: 0.04em;
  }
  .weather-card.inactive .weather-time-value {
    color: var(--text-muted);
  }
  .weather-time-label {
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  /* Coordinates */
  .weather-coords {
    font-size: 0.6875rem;
    color: var(--text-muted);
    font-family: 'Cascadia Code', 'Fira Code', 'SF Mono', monospace;
  }

  /* Card actions */
  .weather-card-actions {
    display: flex;
    gap: 0.35rem;
    justify-content: flex-end;
    margin-top: auto;
  }

  /* Edit form */
  .weather-edit-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .weather-edit-field {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .weather-edit-time-row {
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }
  .weather-edit-time-row select {
    width: 70px;
  }
  .weather-edit-time-sep {
    font-size: 1rem;
    font-weight: 700;
    color: var(--text-secondary);
  }
  .weather-edit-buttons {
    display: flex;
    gap: 0.35rem;
    justify-content: flex-end;
  }

  /* Empty state */
  .weather-empty {
    text-align: center;
    padding: 4rem 2rem;
    color: var(--text-muted);
    font-size: 0.9375rem;
  }
  .weather-empty-icon {
    font-size: 2.5rem;
    margin-bottom: 0.75rem;
    opacity: 0.4;
  }
</style>

<div class="weather-page">
  <div class="weather-page-header">
    <h2>Weather Subscriptions</h2>
    <button class="btn btn-sm" id="weather-refresh">Refresh</button>
  </div>
  <div id="weather-container">
    <div class="weather-empty">Loading...</div>
  </div>
</div>`;
}

// ============================================================
// Card rendering
// ============================================================
function renderCard(item) {
  const isEditing = editingId === item.id;
  const inactiveClass = item.active ? '' : ' inactive';

  if (isEditing) {
    return `
      <div class="weather-card" data-wid="${item.id}">
        <div class="weather-edit-form">
          <div class="weather-edit-field">
            <label class="form-label">Location</label>
            <input type="text" class="form-input" id="edit-loc-${item.id}"
                   value="${esc(item.location || '')}" placeholder="Location name">
          </div>
          <div class="weather-edit-field">
            <label class="form-label">Notification Time</label>
            <div class="weather-edit-time-row">
              <select class="form-input" id="edit-hour-${item.id}">
                ${hourOptions(item.notify_hour)}
              </select>
              <span class="weather-edit-time-sep">:</span>
              <select class="form-input" id="edit-min-${item.id}">
                ${minuteOptions(item.notify_minute)}
              </select>
            </div>
          </div>
          <div class="weather-edit-buttons">
            <button class="btn btn-primary btn-sm" data-save="${item.id}">Save</button>
            <button class="btn btn-sm" data-cancel="${item.id}">Cancel</button>
          </div>
        </div>
      </div>`;
  }

  const coordsHtml = (item.latitude != null && item.longitude != null)
    ? `<div class="weather-coords">${item.latitude.toFixed(2)}, ${item.longitude.toFixed(2)}</div>`
    : '';

  return `
    <div class="weather-card${inactiveClass}" data-wid="${item.id}">
      <div class="weather-card-header">
        <div class="weather-location">${esc(item.location || 'Unknown')}</div>
        <label class="weather-toggle" title="${item.active ? 'Active' : 'Inactive'}">
          <input type="checkbox" ${item.active ? 'checked' : ''} data-toggle="${item.id}">
          <span class="weather-toggle-track"></span>
        </label>
      </div>
      <div class="weather-notify-time">
        <div>
          <div class="weather-time-value">${fmtTime(item.notify_hour, item.notify_minute)}</div>
          <div class="weather-time-label">Daily notification</div>
        </div>
      </div>
      ${coordsHtml}
      <div class="weather-card-actions">
        <button class="btn btn-sm" data-edit="${item.id}">Edit</button>
        <button class="btn btn-sm btn-danger" data-delete="${item.id}">Delete</button>
      </div>
    </div>`;
}

function renderCards() {
  const container = $('weather-container');
  if (!container) return;

  if (subscriptions.length === 0) {
    container.innerHTML = `
      <div class="weather-empty">
        <div class="weather-empty-icon">&#9925;</div>
        <div>No weather subscriptions</div>
      </div>`;
    return;
  }

  container.innerHTML = `<div class="weather-grid">${subscriptions.map(renderCard).join('')}</div>`;
  attachCardHandlers();
}

// ============================================================
// Data loading
// ============================================================
async function loadSubscriptions() {
  try {
    const data = await api('/api/units/weather');
    subscriptions = data?.items || [];
    renderCards();
  } catch (err) {
    console.error('Failed to load weather subscriptions:', err);
    toast('Failed to load weather subscriptions', 'error');
    const container = $('weather-container');
    if (container) {
      container.innerHTML = '<div class="weather-empty">Failed to load</div>';
    }
  }
}

// ============================================================
// Actions
// ============================================================
async function handleToggle(wid) {
  try {
    const res = await api(`/api/units/weather/${wid}/toggle`, { method: 'POST' });
    const item = subscriptions.find(s => s.id === wid);
    if (item) item.active = res.active;
    renderCards();
    toast(res.active ? 'Subscription activated' : 'Subscription paused', 'success');
  } catch (err) {
    console.error('Failed to toggle subscription:', err);
    toast('Failed to toggle subscription', 'error');
    await loadSubscriptions();
  }
}

function handleEdit(wid) {
  editingId = wid;
  renderCards();
  const locInput = $(`edit-loc-${wid}`);
  if (locInput) locInput.focus();
}

async function handleSave(wid) {
  const locInput = $(`edit-loc-${wid}`);
  const hourSelect = $(`edit-hour-${wid}`);
  const minSelect = $(`edit-min-${wid}`);
  if (!locInput || !hourSelect || !minSelect) return;

  const location = locInput.value.trim();
  const notify_hour = parseInt(hourSelect.value, 10);
  const notify_minute = parseInt(minSelect.value, 10);

  if (!location) {
    toast('Location is required', 'error');
    return;
  }

  try {
    await api(`/api/units/weather/${wid}`, {
      method: 'PUT',
      body: { location, notify_hour, notify_minute },
    });
    toast('Subscription updated', 'success');
    editingId = null;
    await loadSubscriptions();
  } catch (err) {
    console.error('Failed to update subscription:', err);
    toast('Failed to update subscription', 'error');
  }
}

function handleCancel() {
  editingId = null;
  renderCards();
}

async function handleDelete(wid) {
  if (!confirm('Delete this weather subscription? This cannot be undone.')) return;
  try {
    await api(`/api/units/weather/${wid}`, { method: 'DELETE' });
    toast('Subscription deleted', 'success');
    editingId = null;
    await loadSubscriptions();
  } catch (err) {
    console.error('Failed to delete subscription:', err);
    toast('Failed to delete subscription', 'error');
  }
}

// ============================================================
// Event delegation
// ============================================================
function attachCardHandlers() {
  const container = $('weather-container');
  if (!container) return;

  container.onclick = (e) => {
    const btn = e.target.closest('button');
    if (btn) {
      if (btn.dataset.edit) handleEdit(Number(btn.dataset.edit));
      else if (btn.dataset.save) handleSave(Number(btn.dataset.save));
      else if (btn.dataset.cancel) handleCancel();
      else if (btn.dataset.delete) handleDelete(Number(btn.dataset.delete));
      return;
    }

    // Toggle switch
    const toggle = e.target.closest('input[data-toggle]');
    if (toggle) {
      e.preventDefault();
      handleToggle(Number(toggle.dataset.toggle));
    }
  };
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  $('weather-refresh')?.addEventListener('click', async () => {
    await loadSubscriptions();
    toast('Subscriptions refreshed', 'info');
  });

  await loadSubscriptions();
}

export function unmount() {
  subscriptions = [];
  editingId = null;
}
