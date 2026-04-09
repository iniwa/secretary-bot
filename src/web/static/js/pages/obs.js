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
      <button class="btn btn-sm" id="obs-games-refresh">Refresh</button>
    </div>
    <div class="obs-groups-wrap" id="obs-groups-wrap"></div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Process</th>
            <th>Group</th>
            <th>Scene</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="obs-games-tbody">
          <tr><td colspan="5" class="obs-log-empty">Loading...</td></tr>
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
    tbody.innerHTML = '<tr><td colspan="5" class="obs-log-empty">No games configured</td></tr>';
    return;
  }

  tbody.innerHTML = games.map((g, idx) => {
    const name = esc(g.name || g.title || '---');
    const proc = esc(g.process || g.process_name || '---');
    const group = g.group
      ? `<span class="obs-group-badge">${esc(String(g.group))}</span>`
      : '<span class="text-muted">---</span>';
    const scene = esc(g.scene || g.scene_name || '---');
    return `<tr>
      <td>${name}</td>
      <td class="mono text-xs">${proc}</td>
      <td>${group}</td>
      <td>${scene}</td>
      <td><button class="btn btn-sm" data-edit-game="${idx}">Edit</button></td>
    </tr>`;
  }).join('');
}

// ============================================================
// Edit game modal
// ============================================================
function openEditModal(idx) {
  const game = games[idx];
  if (!game) return;
  editingGame = { ...game, _index: idx };
  renderEditModal();
}

function renderEditModal() {
  const root = $('obs-edit-root');
  if (!root || !editingGame) { if (root) root.innerHTML = ''; return; }

  const g = editingGame;
  const groupOptions = groups.map(gr => {
    const gName = typeof gr === 'string' ? gr : (gr.name || gr.id || '');
    const sel = String(g.group) === String(gName) ? ' selected' : '';
    return `<option value="${esc(String(gName))}"${sel}>${esc(String(gName))}</option>`;
  }).join('');

  root.innerHTML = `
    <div class="obs-edit-overlay" id="obs-edit-overlay">
      <div class="obs-edit-panel">
        <h3>Edit Game</h3>
        <div class="obs-edit-field">
          <label>Name</label>
          <input type="text" class="form-input" id="edit-game-name" value="${esc(g.name || g.title || '')}">
        </div>
        <div class="obs-edit-field">
          <label>Process</label>
          <input type="text" class="form-input" id="edit-game-process" value="${esc(g.process || g.process_name || '')}">
        </div>
        <div class="obs-edit-field">
          <label>Group</label>
          <select class="form-input" id="edit-game-group">
            <option value="">None</option>
            ${groupOptions}
          </select>
        </div>
        <div class="obs-edit-field">
          <label>Scene</label>
          <input type="text" class="form-input" id="edit-game-scene" value="${esc(g.scene || g.scene_name || '')}">
        </div>
        <div class="obs-edit-buttons">
          <button class="btn btn-primary btn-sm" id="edit-game-save">Save</button>
          <button class="btn btn-sm" id="edit-game-cancel">Cancel</button>
        </div>
      </div>
    </div>`;

  // Attach modal events
  $('edit-game-save')?.addEventListener('click', saveGame);
  $('edit-game-cancel')?.addEventListener('click', closeEditModal);
  $('obs-edit-overlay')?.addEventListener('click', (e) => {
    if (e.target.id === 'obs-edit-overlay') closeEditModal();
  });
}

async function saveGame() {
  if (!editingGame) return;

  const name = $('edit-game-name')?.value?.trim();
  const process = $('edit-game-process')?.value?.trim();
  const group = $('edit-game-group')?.value || '';
  const scene = $('edit-game-scene')?.value?.trim();

  if (!name) {
    toast('Name is required', 'info');
    return;
  }

  // Build the updated game object — preserve original fields, override edited ones
  const updated = { ...games[editingGame._index] };
  updated.name = name;
  if ('title' in updated) updated.title = name;
  updated.process = process || '';
  if ('process_name' in updated) updated.process_name = process || '';
  updated.group = group || '';
  updated.scene = scene || '';
  if ('scene_name' in updated) updated.scene_name = scene || '';

  try {
    await api('/api/obs/games', { method: 'POST', body: updated });
    toast('Game configuration saved', 'success');
    closeEditModal();
    await loadGames();
  } catch (err) {
    toast('Failed to save game configuration: ' + err.message, 'error');
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

  // Games table — delegated edit handler
  $('obs-games-tbody')?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-edit-game]');
    if (!btn) return;
    const idx = parseInt(btn.dataset.editGame, 10);
    openEditModal(idx);
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
