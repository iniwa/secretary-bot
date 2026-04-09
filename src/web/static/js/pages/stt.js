/** STT (Speech-to-Text) page. */
import { api, apiBatch } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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

function formatDuration(seconds) {
  if (seconds == null) return '---';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .stt-layout {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    max-width: 860px;
    margin: 0 auto;
  }

  /* Status card */
  .stt-status-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }
  .stt-status-label {
    font-size: 0.8125rem;
    color: var(--text-secondary);
    font-weight: 500;
  }
  .stt-agent-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .stt-agent-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.6rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-raised);
  }
  .stt-agent-name {
    font-size: 0.825rem;
    font-weight: 500;
    color: var(--text-primary);
  }
  .stt-agent-state {
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  /* Controls card */
  .stt-controls-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }
  .stt-control-group {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .stt-control-group.full-width {
    grid-column: 1 / -1;
  }
  .stt-btn-row {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .stt-device-row {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
  }
  .stt-device-row select {
    flex: 1;
  }

  /* Transcripts card */
  .stt-transcripts {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-height: 600px;
    overflow-y: auto;
  }
  .stt-transcript-item {
    padding: 0.875rem 1rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-raised);
    transition: border-color var(--ease);
  }
  .stt-transcript-item:hover {
    border-color: var(--border-hover);
  }
  .stt-transcript-text {
    font-size: 0.875rem;
    line-height: 1.6;
    color: var(--text-primary);
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0 0 0.5rem 0;
  }
  .stt-transcript-meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    font-size: 0.7rem;
    color: var(--text-muted);
  }
  .stt-transcript-meta span {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
  }
  .stt-empty {
    text-align: center;
    padding: 2rem 1rem;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .stt-header-actions {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 0.75rem;
  }

  /* Summaries card */
  .stt-summaries {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-height: 600px;
    overflow-y: auto;
  }
  .stt-summary-item {
    padding: 0.875rem 1rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-raised);
    transition: border-color var(--ease);
  }
  .stt-summary-item:hover {
    border-color: var(--border-hover);
  }
  .stt-summary-text {
    font-size: 0.875rem;
    line-height: 1.6;
    color: var(--text-primary);
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0 0 0.5rem 0;
  }
  .stt-summary-meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    font-size: 0.7rem;
    color: var(--text-muted);
  }
  .stt-summary-meta span {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
  }

  @media (max-width: 600px) {
    .stt-controls-grid {
      grid-template-columns: 1fr;
    }
    .stt-transcripts,
    .stt-summaries {
      max-height: 400px;
    }
  }
</style>

<div class="stt-layout">

  <!-- Status Card -->
  <div class="card">
    <div class="card-header"><h3>Status</h3></div>
    <div class="stt-status-row">
      <span class="status-dot" id="stt-model-dot"></span>
      <span class="stt-status-label">Whisper Model:</span>
      <span id="stt-model-status">---</span>
    </div>
    <div class="stt-agent-list" id="stt-agent-list">
      <div class="stt-empty">Loading...</div>
    </div>
  </div>

  <!-- Controls Card -->
  <div class="card">
    <div class="card-header"><h3>Controls</h3></div>
    <div class="stt-controls-grid">
      <div class="stt-control-group">
        <label class="form-label">Role</label>
        <select class="form-input" id="stt-role">
          <option value="sub" selected>Sub</option>
          <option value="main">Main</option>
        </select>
      </div>
      <div class="stt-control-group">
        <label class="form-label">Actions</label>
        <div class="stt-btn-row">
          <button class="btn btn-sm" id="stt-btn-init">Init</button>
          <button class="btn btn-primary btn-sm" id="stt-btn-start">Start</button>
          <button class="btn btn-sm" id="stt-btn-stop">Stop</button>
        </div>
      </div>
      <div class="stt-control-group full-width">
        <label class="form-label">Audio Device</label>
        <div class="stt-device-row">
          <select class="form-input" id="stt-device-select">
            <option value="">-- Select device --</option>
          </select>
          <button class="btn btn-sm" id="stt-btn-refresh-devices">Refresh</button>
          <button class="btn btn-primary btn-sm" id="stt-btn-set-device">Set Device</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Transcripts Card -->
  <div class="card">
    <div class="card-header"><h3>Transcripts</h3></div>
    <div class="stt-header-actions">
      <button class="btn btn-sm" id="stt-btn-refresh-transcripts">Refresh</button>
    </div>
    <div class="stt-transcripts" id="stt-transcripts">
      <div class="stt-empty">Loading...</div>
    </div>
  </div>

  <!-- Summaries Card -->
  <div class="card">
    <div class="card-header"><h3>Summaries</h3></div>
    <div class="stt-header-actions">
      <button class="btn btn-sm" id="stt-btn-refresh-summaries">Refresh</button>
    </div>
    <div class="stt-summaries" id="stt-summaries">
      <div class="stt-empty">Loading...</div>
    </div>
  </div>

</div>`;
}

// ============================================================
// Rendering helpers
// ============================================================
function renderAgentList(agents) {
  if (!agents || !agents.length) {
    return '<div class="stt-empty">No agents reported.</div>';
  }
  return agents.map(a => `
    <div class="stt-agent-item">
      <span class="stt-agent-name">${esc(a.name || a.role || 'unknown')}</span>
      <span class="stt-agent-state">
        <span class="status-dot ${a.state === 'running' || a.state === 'listening' ? 'online pulse' : ''}"></span>
        ${esc(a.state || 'unknown')}
      </span>
    </div>
  `).join('');
}

function renderTranscripts(transcripts) {
  if (!transcripts || !transcripts.length) {
    return '<div class="stt-empty">No transcripts available.</div>';
  }
  return transcripts.map(t => {
    const summarizedBadge = t.summarized
      ? '<span class="badge badge-info">Summarized</span>'
      : '';
    const startTime = fullDatetime(t.started_at);
    const endTime = fullDatetime(t.ended_at);
    const duration = formatDuration(t.duration_seconds);

    return `
    <div class="stt-transcript-item">
      <p class="stt-transcript-text">${esc(t.text)}</p>
      <div class="stt-transcript-meta">
        ${summarizedBadge}
        <span title="Start">${esc(startTime)}</span>
        <span>&rarr;</span>
        <span title="End">${esc(endTime)}</span>
        <span title="Duration">(${esc(duration)})</span>
      </div>
    </div>`;
  }).join('');
}

function renderSummaries(summaries) {
  if (!summaries || !summaries.length) {
    return '<div class="stt-empty">No summaries available.</div>';
  }
  return summaries.map(s => {
    let transcriptIds = '';
    try {
      const ids = JSON.parse(s.transcript_ids || '[]');
      transcriptIds = ids.join(', ');
    } catch {
      transcriptIds = s.transcript_ids || '';
    }
    const created = fullDatetime(s.created_at);

    return `
    <div class="stt-summary-item">
      <p class="stt-summary-text">${esc(s.summary)}</p>
      <div class="stt-summary-meta">
        <span title="Created">${esc(created)}</span>
        <span title="Transcript IDs">IDs: ${esc(transcriptIds)}</span>
      </div>
    </div>`;
  }).join('');
}

// ============================================================
// Data loading
// ============================================================
async function loadStatus() {
  try {
    const [statusData, modelData] = await apiBatch([
      ['/api/stt/status'],
      ['/api/stt/model/status'],
    ]);

    // Whisper model status
    const dot = $('stt-model-dot');
    const label = $('stt-model-status');
    if (dot && label && modelData) {
      const loaded = !!modelData.loaded;
      dot.className = 'status-dot ' + (loaded ? 'online pulse' : '');
      label.textContent = loaded ? 'Loaded' : 'Not loaded';
    } else if (dot && label) {
      dot.className = 'status-dot';
      label.textContent = 'Unknown';
    }

    // Agent list
    const listEl = $('stt-agent-list');
    if (listEl && statusData) {
      listEl.innerHTML = renderAgentList(statusData.agents || []);
    } else if (listEl) {
      listEl.innerHTML = '<div class="stt-empty">Failed to load agent status.</div>';
    }
  } catch (err) {
    console.error('Load STT status:', err);
    toast('Failed to load STT status', 'error');
  }
}

async function loadDevices() {
  const select = $('stt-device-select');
  if (!select) return;

  const role = $('stt-role')?.value || 'sub';
  select.innerHTML = '<option value="">Loading...</option>';

  try {
    const data = await api('/api/stt/devices', { params: { role } });
    const devices = data?.devices || data || [];

    if (Array.isArray(devices) && devices.length) {
      select.innerHTML = '<option value="">-- Select device --</option>' +
        devices.map((d, i) => {
          const idx = d.index ?? i;
          const name = d.name || `Device ${idx}`;
          return `<option value="${idx}">${esc(name)}</option>`;
        }).join('');
    } else {
      select.innerHTML = '<option value="">No devices found</option>';
    }
  } catch (err) {
    console.error('Load STT devices:', err);
    select.innerHTML = '<option value="">Failed to load</option>';
    toast('Failed to load audio devices', 'error');
  }
}

async function loadTranscripts() {
  const container = $('stt-transcripts');
  if (!container) return;

  const role = $('stt-role')?.value || 'sub';
  container.innerHTML = '<div class="stt-empty">Loading...</div>';

  try {
    const data = await api('/api/stt/transcripts', { params: { role } });
    container.innerHTML = renderTranscripts(data?.transcripts || []);
  } catch (err) {
    console.error('Load STT transcripts:', err);
    container.innerHTML = '<div class="stt-empty">Failed to load transcripts.</div>';
    toast('Failed to load transcripts', 'error');
  }
}

async function loadSummaries() {
  const container = $('stt-summaries');
  if (!container) return;

  container.innerHTML = '<div class="stt-empty">Loading...</div>';

  try {
    const data = await api('/api/stt/summaries');
    container.innerHTML = renderSummaries(data?.summaries || []);
  } catch (err) {
    console.error('Load STT summaries:', err);
    container.innerHTML = '<div class="stt-empty">Failed to load summaries.</div>';
    toast('Failed to load summaries', 'error');
  }
}

// ============================================================
// Control actions
// ============================================================
async function sendControl(action, extra = {}) {
  const role = $('stt-role')?.value || 'sub';
  const btnId = `stt-btn-${action}`;
  const btn = $(btnId);
  const origText = btn?.textContent;

  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Wait...';
  }

  try {
    await api('/api/stt/control', {
      method: 'POST',
      body: { role, action, ...extra },
    });
    toast(`STT ${action} sent`, 'success');
    // Refresh status after control action
    await loadStatus();
  } catch (err) {
    console.error(`STT control ${action}:`, err);
    toast(`Failed to ${action}: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }
}

async function setDevice() {
  const select = $('stt-device-select');
  const idx = select?.value;
  if (idx === '' || idx == null) {
    toast('Select a device first', 'error');
    return;
  }

  const btn = $('stt-btn-set-device');
  const origText = btn?.textContent;
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Wait...';
  }

  const role = $('stt-role')?.value || 'sub';
  try {
    await api('/api/stt/control', {
      method: 'POST',
      body: { role, action: 'set_device', device_index: Number(idx) },
    });
    toast('Device set', 'success');
  } catch (err) {
    console.error('STT set device:', err);
    toast(`Failed to set device: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  // Control buttons
  $('stt-btn-init')?.addEventListener('click', () => sendControl('init'));
  $('stt-btn-start')?.addEventListener('click', () => sendControl('start'));
  $('stt-btn-stop')?.addEventListener('click', () => sendControl('stop'));
  $('stt-btn-set-device')?.addEventListener('click', setDevice);

  // Refresh buttons
  $('stt-btn-refresh-devices')?.addEventListener('click', loadDevices);
  $('stt-btn-refresh-transcripts')?.addEventListener('click', loadTranscripts);
  $('stt-btn-refresh-summaries')?.addEventListener('click', loadSummaries);

  // Load all data in parallel
  await Promise.all([loadStatus(), loadDevices(), loadTranscripts(), loadSummaries()]);
}
