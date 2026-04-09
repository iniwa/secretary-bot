/** Input Relay page — status, controls, and logs for the Input Relay submodule on Windows Agents. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let agents = [];
let selectedRole = '';
let logContent = '';

function $(id) { return document.getElementById(id); }

// ============================================================
// Helpers
// ============================================================
function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function setLoading(btn, loading) {
  if (loading) {
    btn._origHTML = btn.innerHTML;
    btn.innerHTML = '<span class="ir-spinner"></span> Running...';
    btn.disabled = true;
  } else {
    btn.innerHTML = btn._origHTML || btn.textContent;
    btn.disabled = false;
  }
}

function setControlsDisabled(role, disabled) {
  document.querySelectorAll(`.ir-agent-card[data-role="${role}"] .btn`).forEach(btn => {
    btn.disabled = disabled;
  });
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .ir-page {
    max-width: 1000px;
    margin: 0 auto;
  }
  .ir-section {
    margin-bottom: 1.5rem;
  }
  .ir-section-title {
    font-size: 1rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 0.75rem;
  }
  .ir-update-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
  }
  .ir-update-desc {
    font-size: 0.8125rem;
    color: var(--text-secondary);
    line-height: 1.5;
  }
  .ir-update-results {
    margin-top: 0.75rem;
    display: none;
  }
  .ir-update-results.visible {
    display: block;
  }
  .ir-result-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.8125rem;
  }
  .ir-result-item:last-child { border-bottom: none; }
  .ir-result-name {
    font-weight: 500;
    color: var(--text-primary);
    min-width: 80px;
  }
  .ir-result-msg {
    color: var(--text-secondary);
  }
  .ir-agents-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1rem;
  }
  .ir-agent-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    transition: border-color var(--ease);
  }
  .ir-agent-card:hover {
    border-color: var(--border-hover);
  }
  .ir-agent-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .ir-agent-name {
    font-size: 0.9375rem;
    font-weight: 600;
    color: var(--text-primary);
  }
  .ir-agent-role {
    font-size: 0.75rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .ir-agent-status {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.8125rem;
  }
  .ir-agent-meta {
    font-size: 0.75rem;
    color: var(--text-muted);
    line-height: 1.5;
  }
  .ir-agent-controls {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin-top: 0.25rem;
  }
  .ir-agent-controls .btn {
    flex: 1;
    min-width: 70px;
  }
  .ir-settings-link {
    margin-top: 0.25rem;
  }
  .ir-settings-note {
    font-size: 0.75rem;
    color: var(--text-muted);
    font-style: italic;
    margin-top: 0.25rem;
  }
  .ir-log-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 0.75rem;
  }
  .ir-log-header select.form-input {
    width: auto;
    min-width: 140px;
  }
  .ir-log-container {
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1rem;
    max-height: 500px;
    overflow-y: auto;
    font-family: 'Cascadia Code', 'Fira Code', 'SF Mono', monospace;
    font-size: 0.75rem;
    line-height: 1.6;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .ir-log-empty {
    color: var(--text-muted);
    text-align: center;
    padding: 2rem;
    font-family: inherit;
    font-size: 0.8125rem;
  }
  .ir-empty {
    text-align: center;
    padding: 3rem 2rem;
    color: var(--text-muted);
    font-size: 0.875rem;
  }
  .ir-spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid var(--text-muted);
    border-top-color: transparent;
    border-radius: 50%;
    animation: ir-spin 0.6s linear infinite;
  }
  @keyframes ir-spin { to { transform: rotate(360deg); } }
  .btn[disabled] {
    opacity: 0.5;
    pointer-events: none;
  }
</style>

<div class="ir-page">

  <!-- Update All Agents -->
  <div class="ir-section">
    <div class="ir-section-title">Update</div>
    <div class="card">
      <div class="card-body ir-update-card">
        <div>
          <div class="ir-update-desc">Update the Input Relay submodule on all connected agents.</div>
        </div>
        <button class="btn btn-primary" id="ir-update-all">Update All Agents</button>
      </div>
      <div class="ir-update-results" id="ir-update-results"></div>
    </div>
  </div>

  <!-- Agent Status Cards -->
  <div class="ir-section">
    <div class="ir-section-title">Agent Status</div>
    <div id="ir-agents-container">
      <div class="ir-empty">Loading...</div>
    </div>
  </div>

  <!-- Logs -->
  <div class="ir-section">
    <div class="ir-section-title">Logs</div>
    <div class="card">
      <div class="ir-log-header">
        <select class="form-input" id="ir-log-role">
          <option value="">Select agent role...</option>
        </select>
        <button class="btn btn-sm" id="ir-log-refresh">Refresh</button>
      </div>
      <div class="ir-log-container" id="ir-log-content">
        <div class="ir-log-empty">Select an agent role to view logs</div>
      </div>
    </div>
  </div>

</div>`;
}

// ============================================================
// Agent cards
// ============================================================
function renderAgentCard(agent) {
  const isOnline = agent.status === 'online' || agent.alive;
  const dotClass = isOnline ? 'online' : 'error';
  const statusLabel = isOnline ? 'Online' : 'Offline';
  const role = agent.role || 'unknown';

  let settingsHtml = '';
  if (agent.host && agent.port) {
    const settingsUrl = `http://${agent.host}:${agent.port}/input-relay`;
    settingsHtml = `
      <div class="ir-settings-link">
        <a href="${esc(settingsUrl)}" target="_blank" rel="noopener" class="btn btn-sm" style="width:100%;text-align:center">Open Settings</a>
      </div>`;
  } else {
    settingsHtml = `<div class="ir-settings-note">Settings available on agent's web UI</div>`;
  }

  return `
    <div class="ir-agent-card" data-role="${esc(role)}">
      <div class="ir-agent-header">
        <div>
          <div class="ir-agent-name">${esc(agent.agent_name || agent.agent_id || 'Agent')}</div>
          <div class="ir-agent-role">${esc(role)}</div>
        </div>
        <div class="ir-agent-status">
          <span class="status-dot ${dotClass}"></span>
          <span>${statusLabel}</span>
        </div>
      </div>
      ${agent.version ? `<div class="ir-agent-meta">Version: ${esc(agent.version)}</div>` : ''}
      <div class="ir-agent-controls">
        <button class="btn btn-sm" data-action="start" data-role="${esc(role)}">Start</button>
        <button class="btn btn-sm" data-action="stop" data-role="${esc(role)}">Stop</button>
        <button class="btn btn-sm btn-danger" data-action="restart" data-role="${esc(role)}">Restart</button>
      </div>
      ${settingsHtml}
    </div>`;
}

function renderAgents() {
  const container = $('ir-agents-container');
  if (!container) return;

  if (agents.length === 0) {
    container.innerHTML = '<div class="ir-empty">No agents found</div>';
    return;
  }

  container.innerHTML = `<div class="ir-agents-grid">${agents.map(renderAgentCard).join('')}</div>`;
  attachControlHandlers();
}

// ============================================================
// Role dropdown for logs
// ============================================================
function populateRoleDropdown() {
  const select = $('ir-log-role');
  if (!select) return;

  const currentValue = select.value;
  // Keep the placeholder option, rebuild the rest
  let html = '<option value="">Select agent role...</option>';
  const roles = [...new Set(agents.map(a => a.role).filter(Boolean))];
  roles.forEach(role => {
    const sel = role === currentValue ? ' selected' : '';
    html += `<option value="${esc(role)}"${sel}>${esc(role)}</option>`;
  });
  select.innerHTML = html;

  // If no selection yet and there are roles, auto-select the first
  if (!currentValue && roles.length > 0) {
    select.value = roles[0];
    selectedRole = roles[0];
  }
}

// ============================================================
// Data fetching
// ============================================================
async function loadStatus() {
  try {
    const data = await api('/api/tools/input-relay/status');
    agents = data?.agents || [];
    renderAgents();
    populateRoleDropdown();
  } catch (err) {
    console.error('Load input-relay status:', err);
    const container = $('ir-agents-container');
    if (container) {
      container.innerHTML = '<div class="ir-empty" style="color:var(--error)">Failed to load agent status</div>';
    }
    toast('Failed to load Input Relay status', 'error');
  }
}

async function loadLogs(role) {
  if (!role) return;
  const logEl = $('ir-log-content');
  if (!logEl) return;

  logEl.innerHTML = '<div class="ir-log-empty">Loading...</div>';

  try {
    const data = await api(`/api/tools/input-relay/logs/${encodeURIComponent(role)}`, {
      params: { lines: 100 }
    });
    logContent = typeof data === 'string' ? data : (data?.logs || data?.content || data?.text || '');
    if (typeof logContent === 'object') {
      logContent = JSON.stringify(logContent, null, 2);
    }

    if (!logContent || logContent.trim() === '') {
      logEl.innerHTML = '<div class="ir-log-empty">No logs available</div>';
    } else {
      logEl.textContent = logContent;
      // Auto-scroll to bottom
      logEl.scrollTop = logEl.scrollHeight;
    }
  } catch (err) {
    console.error('Load input-relay logs:', err);
    logEl.innerHTML = '<div class="ir-log-empty" style="color:var(--error)">Failed to load logs</div>';
    toast('Failed to load logs', 'error');
  }
}

// ============================================================
// Control actions
// ============================================================
async function agentAction(action, role, btn) {
  setLoading(btn, true);
  setControlsDisabled(role, true);
  try {
    const res = await api(`/api/tools/input-relay/${action}/${encodeURIComponent(role)}`, { method: 'POST' });
    toast(`${action} successful for "${role}"`, 'success');
    // Refresh status after action
    await loadStatus();
  } catch (err) {
    console.error(`Input relay ${action}:`, err);
    toast(`${action} failed for "${role}": ${err.message}`, 'error');
  } finally {
    setLoading(btn, false);
    setControlsDisabled(role, false);
  }
}

function attachControlHandlers() {
  document.querySelectorAll('.ir-agent-controls .btn[data-action]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const action = btn.dataset.action;
      const role = btn.dataset.role;
      if (action && role) {
        agentAction(action, role, btn);
      }
    });
  });
}

// ============================================================
// Update all agents
// ============================================================
async function updateAllAgents() {
  const btn = $('ir-update-all');
  const resultsEl = $('ir-update-results');
  if (!btn || !resultsEl) return;

  setLoading(btn, true);
  resultsEl.classList.remove('visible');

  try {
    const res = await api('/api/tools/input-relay/update', { method: 'POST' });
    const agentResults = res?.agents || [];

    if (agentResults.length === 0) {
      resultsEl.innerHTML = '<div class="ir-result-item"><span class="ir-result-msg">No agent results returned</span></div>';
    } else {
      resultsEl.innerHTML = agentResults.map(a => {
        const statusBadge = a.success
          ? '<span class="badge badge-success">OK</span>'
          : '<span class="badge badge-error">Failed</span>';
        const name = a.name || a.agent_name || a.id || a.agent_id || 'unknown';
        const msg = a.message || a.detail || '';
        return `
          <div class="ir-result-item">
            <span class="ir-result-name">${esc(name)}</span>
            ${statusBadge}
            <span class="ir-result-msg">${esc(msg)}</span>
          </div>`;
      }).join('');
    }

    resultsEl.classList.add('visible');
    toast('Input Relay update complete', 'success');
    // Refresh status after update
    await loadStatus();
  } catch (err) {
    console.error('Update input-relay:', err);
    resultsEl.innerHTML = `<div class="ir-result-item" style="color:var(--error)">${esc(err.message)}</div>`;
    resultsEl.classList.add('visible');
    toast('Input Relay update failed', 'error');
  } finally {
    setLoading(btn, false);
  }
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  // Load status
  await loadStatus();

  // Update All button
  $('ir-update-all')?.addEventListener('click', updateAllAgents);

  // Log role selector
  $('ir-log-role')?.addEventListener('change', (e) => {
    selectedRole = e.target.value;
    if (selectedRole) {
      loadLogs(selectedRole);
    } else {
      const logEl = $('ir-log-content');
      if (logEl) logEl.innerHTML = '<div class="ir-log-empty">Select an agent role to view logs</div>';
    }
  });

  // Log refresh button
  $('ir-log-refresh')?.addEventListener('click', () => {
    const role = $('ir-log-role')?.value;
    if (role) {
      loadLogs(role);
      toast('Logs refreshed', 'info');
    } else {
      toast('Select an agent role first', 'warning');
    }
  });

  // Auto-load logs if a role is already selected
  if (selectedRole) {
    loadLogs(selectedRole);
  }
}

export function unmount() {
  agents = [];
  selectedRole = '';
  logContent = '';
}
