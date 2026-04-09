/** OBS page — connection status, game configuration, and logs. */
import { api, apiBatch } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let obsConnected = false;
let games = [];
let groups = [];
let logs = [];
let editingGame = null; // game object being edited, or null

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mi}:${ss}`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .obs-page {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }

  /* Status card */
  .obs-status-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 0;
  }
  .obs-status-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .obs-status-dot.connected {
    background: var(--success, #22c55e);
    box-shadow: 0 0 6px var(--success, #22c55e);
  }
  .obs-status-dot.disconnected {
    background: var(--error, #ef4444);
    box-shadow: 0 0 6px var(--error, #ef4444);
  }
  .obs-status-label {
    font-size: 0.9375rem;
    font-weight: 600;
    color: var(--text-primary);
  }

  /* Games table */
  .obs-games-toolbar {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .obs-group-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    font-size: 0.6875rem;
    font-weight: 500;
    background: var(--bg-overlay);
    color: var(--text-secondary);
    border: 1px solid var(--border);
  }
  .obs-row-actions {
    display: flex;
    gap: 0.25rem;
    align-items: center;
  }
  .obs-row-actions button {
    padding: 0.2rem 0.4rem;
    min-width: unset;
  }
  .btn-icon-sm {
    padding: 0.15rem 0.35rem;
    font-size: 0.75rem;
    line-height: 1;
    min-width: unset;
  }
  .btn-danger-sm {
    background: transparent;
    color: var(--error, #ef4444);
    border: 1px solid var(--error, #ef4444);
  }
  .btn-danger-sm:hover {
    background: var(--error, #ef4444);
    color: #fff;
  }

  /* Edit modal overlay */
  .obs-edit-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .obs-edit-panel {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1.5rem;
    width: 90%;
    max-width: 520px;
    max-height: 80vh;
    overflow-y: auto;
  }
  .obs-edit-panel h3 {
    margin: 0 0 1rem;
    font-size: 1rem;
    font-weight: 600;
    color: var(--text-primary);
  }
  .obs-edit-field {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    margin-bottom: 0.75rem;
  }
  .obs-edit-field label {
    font-size: 0.8125rem;
    color: var(--text-secondary);
    font-weight: 500;
  }
  .obs-edit-buttons {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
    margin-top: 1rem;
  }

  /* Logs */
  .obs-logs-toolbar {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .obs-logs-toolbar select {
    width: auto;
    min-width: 80px;
  }
  .obs-log-container {
    max-height: 450px;
    overflow-y: auto;
    background: var(--bg-base);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.75rem 1rem;
    font-family: 'Cascadia Code', 'Fira Code', 'SF Mono', monospace;
    font-size: 0.75rem;
    line-height: 1.6;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .obs-log-entry {
    padding: 0.2rem 0;
    border-bottom: 1px solid var(--border);
  }
  .obs-log-entry:last-child {
    border-bottom: none;
  }
  .obs-log-ts {
    color: var(--text-muted);
    margin-right: 0.5rem;
  }
  .obs-log-empty {
    text-align: center;
    padding: 2rem 1rem;
    color: var(--text-muted);
    font-size: 0.875rem;
    font-family: inherit;
  }

  /* Groups summary */
  .obs-groups-wrap {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .obs-group-chip {
    padding: 0.3rem 0.75rem;
    border-radius: var(--radius-md);
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    font-size: 0.8125rem;
    color: var(--text-secondary);
  }
  .obs-group-chip strong {
    color: var(--text-primary);
    font-weight: 600;
  }

  @media (max-width: 640px) {
    .obs-edit-panel {
      width: 95%;
      padding: 1rem;
    }
  }
</style>

<div class="obs-page">
  <!-- Status Card -->
  <div class="card">
    <div class="card-header">
      <h3>OBS Status</h3>
      <button class="btn btn-sm" id="obs-status-refresh">Refresh</button>
    </div>
    <div class="obs-status-row" id="obs-status-row">
      <div class="obs-status-dot disconnected" id="obs-status-dot"></div>
      <span class="obs-status-label" id="obs-status-label">Checking...</span>
    </div>
  </div>

  <!-- Games Configuration Card -->
  <div class="card">
    <div class="card-header">
      <h3>Games Configuration</h3>
      <div style="display:flex;gap:0.5rem">
        <button class="btn btn-sm btn-primary" id="obs-games-add">+ Add</button>
        <button class="btn btn-sm" id="obs-games-refresh">Refresh</button>
      </div>
    </div>
    <div class="obs-groups-wrap" id="obs-groups-wrap"></div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Process</th>
            <th>Group</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="obs-games-tbody">
          <tr><td colspan="4" class="obs-log-empty">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Logs Card -->
  <div class="card">
    <div class="card-header">
      <h3>OBS Logs</h3>
      <div class="obs-logs-toolbar">
        <select class="form-input" id="obs-log-lines">
          <option value="25">25</option>
          <option value="50" selected>50</option>
          <option value="100">100</option>
          <option value="200">200</option>
        </select>
        <button class="btn btn-sm" id="obs-logs-refresh">Refresh</button>
      </div>
    </div>
    <div class="obs-log-container" id="obs-log-container">
      <div class="obs-log-empty">Loading...</div>
    </div>
  </div>
</div>

<!-- Edit modal (rendered dynamically) -->
<div id="obs-edit-root"></div>`;
}

// ============================================================
// Status
// ============================================================
async function loadStatus() {
  try {
    const data = await api('/api/obs/status');
    obsConnected = !!data?.obs_connected;
  } catch {
    obsConnected = false;
  }
  renderStatus();
}

function renderStatus() {
  const dot = $('obs-status-dot');
  const label = $('obs-status-label');
  if (dot) {
    dot.className = `obs-status-dot ${obsConnected ? 'connected' : 'disconnected'}`;
  }
  if (label) {
    label.textContent = obsConnected ? 'Connected' : 'Disconnected';
  }
}

// ============================================================
// Games
// ============================================================
async function loadGames() {
  try {
    const data = await api('/api/obs/games');
    games = data?.games || [];
    groups = data?.groups || [];
  } catch (err) {
    toast('Failed to load game configuration', 'error');
    console.error(err);
    games = [];
    groups = [];
  }
  renderGroups();
  renderGamesTable();
}

function renderGroups() {
  const wrap = $('obs-groups-wrap');
  if (!wrap) return;
  if (groups.length === 0) {
    wrap.innerHTML = '';
    return;
  }
  wrap.innerHTML = groups.map(g => {
    const name = typeof g === 'string' ? g : (g.name || g.id || JSON.stringify(g));
    return `<div class="obs-group-chip"><strong>${esc(String(name))}</strong></div>`;
  }).join('');
}

function renderGamesTable() {
  const tbody = $('obs-games-tbody');
  if (!tbody) return;

  if (games.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="obs-log-empty">No games configured</td></tr>';
    return;
  }

  const last = games.length - 1;
  tbody.innerHTML = games.map((g, idx) => {
    const name = esc(g.name || '---');
    const proc = esc(g.process || '---');
    const group = g.group
      ? `<span class="obs-group-badge">${esc(String(g.group))}</span>`
      : '<span class="text-muted">---</span>';
    const upDisabled = idx === 0 ? ' disabled' : '';
    const downDisabled = idx === last ? ' disabled' : '';
    return `<tr>
      <td>${name}</td>
      <td class="mono text-xs">${proc}</td>
      <td>${group}</td>
      <td>
        <div class="obs-row-actions">
          <button class="btn btn-sm btn-icon-sm" data-move-up="${idx}" title="Move up"${upDisabled}>&uarr;</button>
          <button class="btn btn-sm btn-icon-sm" data-move-down="${idx}" title="Move down"${downDisabled}>&darr;</button>
          <button class="btn btn-sm" data-edit-game="${idx}">Edit</button>
          <button class="btn btn-sm btn-icon-sm btn-danger-sm" data-delete-game="${idx}" title="Delete">&times;</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// ============================================================
// Edit game modal
// ============================================================
function openEditModal(idx) {
  const game = games[idx];
  if (!game) return;
  editingGame = { ...game, _index: idx, _isNew: false };
  renderEditModal();
}

function openAddModal() {
  editingGame = { name: '', process: '', group: '', _index: -1, _isNew: true };
  renderEditModal();
}

function renderEditModal() {
  const root = $('obs-edit-root');
  if (!root || !editingGame) { if (root) root.innerHTML = ''; return; }

  const g = editingGame;
  const isNew = g._isNew;
  const groupOptions = groups.map(gr => {
    const gName = typeof gr === 'string' ? gr : (gr.name || gr.id || '');
    const sel = String(g.group) === String(gName) ? ' selected' : '';
    return `<option value="${esc(String(gName))}"${sel}>${esc(String(gName))}</option>`;
  }).join('');

  const deleteBtn = isNew ? '' : '<button class="btn btn-sm btn-danger-sm" id="edit-game-delete">Delete</button>';

  root.innerHTML = `
    <div class="obs-edit-overlay" id="obs-edit-overlay">
      <div class="obs-edit-panel">
        <h3>${isNew ? 'Add Game' : 'Edit Game'}</h3>
        <div class="obs-edit-field">
          <label>Name</label>
          <input type="text" class="form-input" id="edit-game-name" value="${esc(g.name || '')}" placeholder="e.g. Minecraft">
        </div>
        <div class="obs-edit-field">
          <label>Process</label>
          <input type="text" class="form-input" id="edit-game-process" value="${esc(g.process || '')}" placeholder="e.g. javaw.exe">
        </div>
        <div class="obs-edit-field">
          <label>Group</label>
          <select class="form-input" id="edit-game-group">
            <option value="">None</option>
            ${groupOptions}
          </select>
        </div>
        <div class="obs-edit-buttons">
          ${deleteBtn}
          <span style="flex:1"></span>
          <button class="btn btn-sm" id="edit-game-cancel">Cancel</button>
          <button class="btn btn-primary btn-sm" id="edit-game-save">Save</button>
        </div>
      </div>
    </div>`;

  // Attach modal events
  $('edit-game-save')?.addEventListener('click', saveGame);
  $('edit-game-cancel')?.addEventListener('click', closeEditModal);
  $('edit-game-delete')?.addEventListener('click', () => deleteGameFromModal());
  $('obs-edit-overlay')?.addEventListener('click', (e) => {
    if (e.target.id === 'obs-edit-overlay') closeEditModal();
  });
}

/** POST the full {games, groups} payload to the backend. */
async function postFullPayload() {
  const payload = {
    games: games.map(g => ({ process: g.process, name: g.name, group: g.group || '' })),
    groups: groups.map(g => (typeof g === 'string' ? g : (g.name || g.id || ''))),
  };
  await api('/api/obs/games', { method: 'POST', body: payload });
}

async function saveGame() {
  if (!editingGame) return;

  const name = $('edit-game-name')?.value?.trim();
  const process = $('edit-game-process')?.value?.trim();
  const group = $('edit-game-group')?.value || '';

  if (!name) {
    toast('Name is required', 'info');
    return;
  }
  if (!process) {
    toast('Process is required', 'info');
    return;
  }

  if (editingGame._isNew) {
    // Check for duplicate process name
    if (games.some(g => g.process === process)) {
      toast('A game with this process already exists', 'error');
      return;
    }
    games.push({ process, name, group });
  } else {
    // Update existing game in-place
    const idx = editingGame._index;
    if (idx >= 0 && idx < games.length) {
      games[idx] = { process, name, group };
    }
  }

  try {
    await postFullPayload();
    toast(editingGame._isNew ? 'Game added' : 'Game updated', 'success');
    closeEditModal();
    renderGroups();
    renderGamesTable();
  } catch (err) {
    // Revert on failure — reload from backend
    toast('Failed to save: ' + err.message, 'error');
    await loadGames();
  }
}

async function deleteGame(idx) {
  if (idx < 0 || idx >= games.length) return;
  const g = games[idx];
  if (!confirm(`Delete "${g.name || g.process}"?`)) return;

  games.splice(idx, 1);
  try {
    await postFullPayload();
    toast('Game deleted', 'success');
    renderGamesTable();
  } catch (err) {
    toast('Failed to delete: ' + err.message, 'error');
    await loadGames();
  }
}

async function deleteGameFromModal() {
  if (!editingGame || editingGame._isNew) return;
  const idx = editingGame._index;
  closeEditModal();
  await deleteGame(idx);
}

async function moveGame(idx, direction) {
  const newIdx = idx + direction;
  if (newIdx < 0 || newIdx >= games.length) return;
  // Swap
  [games[idx], games[newIdx]] = [games[newIdx], games[idx]];
  try {
    await postFullPayload();
    renderGamesTable();
  } catch (err) {
    toast('Failed to reorder: ' + err.message, 'error');
    await loadGames();
  }
}

function closeEditModal() {
  editingGame = null;
  const root = $('obs-edit-root');
  if (root) root.innerHTML = '';
}

// ============================================================
// Logs
// ============================================================
async function loadLogs() {
  const linesSelect = $('obs-log-lines');
  const lines = linesSelect ? parseInt(linesSelect.value, 10) : 50;

  try {
    const data = await api('/api/obs/logs', { params: { lines } });
    logs = data?.logs || [];
  } catch (err) {
    toast('Failed to load OBS logs', 'error');
    console.error(err);
    logs = [];
  }
  renderLogs();
}

function renderLogs() {
  const container = $('obs-log-container');
  if (!container) return;

  if (logs.length === 0) {
    container.innerHTML = '<div class="obs-log-empty">No logs available</div>';
    return;
  }

  // Newest first
  const sorted = [...logs].reverse();
  container.innerHTML = sorted.map(entry => {
    if (typeof entry === 'string') {
      return `<div class="obs-log-entry">${esc(entry)}</div>`;
    }
    const ts = entry.timestamp ? `<span class="obs-log-ts">${fmtTime(entry.timestamp)}</span>` : '';
    const msg = entry.message || entry.text || entry.msg || JSON.stringify(entry);
    return `<div class="obs-log-entry">${ts}${esc(msg)}</div>`;
  }).join('');
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  // Status refresh
  $('obs-status-refresh')?.addEventListener('click', async () => {
    await loadStatus();
    toast('Status refreshed', 'info');
  });

  // Games refresh
  $('obs-games-refresh')?.addEventListener('click', async () => {
    await loadGames();
    toast('Games refreshed', 'info');
  });

  // Add game
  $('obs-games-add')?.addEventListener('click', () => openAddModal());

  // Games table — delegated click handler for edit, delete, move
  $('obs-games-tbody')?.addEventListener('click', (e) => {
    const editBtn = e.target.closest('[data-edit-game]');
    if (editBtn) {
      openEditModal(parseInt(editBtn.dataset.editGame, 10));
      return;
    }
    const delBtn = e.target.closest('[data-delete-game]');
    if (delBtn) {
      deleteGame(parseInt(delBtn.dataset.deleteGame, 10));
      return;
    }
    const upBtn = e.target.closest('[data-move-up]');
    if (upBtn) {
      moveGame(parseInt(upBtn.dataset.moveUp, 10), -1);
      return;
    }
    const downBtn = e.target.closest('[data-move-down]');
    if (downBtn) {
      moveGame(parseInt(downBtn.dataset.moveDown, 10), 1);
      return;
    }
  });

  // Logs refresh
  $('obs-logs-refresh')?.addEventListener('click', () => loadLogs());
  $('obs-log-lines')?.addEventListener('change', () => loadLogs());

  // Initial data load — parallel
  const [statusData, gamesData, logsData] = await apiBatch([
    ['/api/obs/status'],
    ['/api/obs/games'],
    ['/api/obs/logs', { params: { lines: 50 } }],
  ]);

  if (statusData) {
    obsConnected = !!statusData.obs_connected;
    renderStatus();
  } else {
    obsConnected = false;
    renderStatus();
  }

  if (gamesData) {
    games = gamesData.games || [];
    groups = gamesData.groups || [];
    renderGroups();
    renderGamesTable();
  } else {
    renderGamesTable();
  }

  if (logsData) {
    logs = logsData.logs || [];
    renderLogs();
  } else {
    renderLogs();
  }
}

export function unmount() {
  obsConnected = false;
  games = [];
  groups = [];
  logs = [];
  editingGame = null;
  // Clean up edit modal if open
  const root = document.getElementById('obs-edit-root');
  if (root) root.innerHTML = '';
}
