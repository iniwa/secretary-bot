/** InnerMind page. */
import { api, apiBatch } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// Constants
// ============================================================
const MOOD_BADGE = {
  curious:   'badge-info',
  calm:      'badge-success',
  talkative: 'badge-accent',
  concerned: 'badge-warning',
  idle:      'badge-muted',
};

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
  .im-layout {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    max-width: 860px;
    margin: 0 auto;
  }

  /* Status card */
  .im-status-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1rem;
    margin-bottom: 0.5rem;
  }
  .im-stat-item {
    text-align: center;
  }
  .im-stat-label {
    font-size: 0.7rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.35rem;
  }
  .im-stat-value {
    font-size: 0.95rem;
    color: var(--text-primary);
    font-weight: 500;
  }
  .im-enabled-indicator {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-bottom: 0.75rem;
  }
  .im-monologue-preview {
    margin-top: 0.75rem;
    padding: 0.75rem 1rem;
    background: var(--bg-raised);
    border-radius: var(--radius-sm);
    border-left: 3px solid var(--border-hover);
  }
  .im-monologue-preview-label {
    font-size: 0.6875rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.35rem;
    font-weight: 500;
  }
  .im-monologue-text {
    font-size: 0.85rem;
    color: var(--text-primary);
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }
  .im-monologue-time {
    font-size: 0.7rem;
    color: var(--text-muted);
    margin-top: 0.35rem;
  }

  /* Settings card */
  .im-settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.85rem;
  }
  @media (max-width: 600px) {
    .im-status-grid { grid-template-columns: 1fr; gap: 0.5rem; }
    .im-settings-grid { grid-template-columns: 1fr; }
  }
  .form-group {
    margin-bottom: 0.85rem;
  }
  .form-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .form-row .form-input {
    width: auto;
    max-width: 180px;
  }
  .form-hint {
    font-size: 0.7rem;
    color: var(--text-muted);
    margin-top: 0.2rem;
  }
  .card-footer {
    display: flex;
    justify-content: flex-end;
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
  }
  .im-toggle-row {
    grid-column: 1 / -1;
  }

  /* Toggle switch */
  .toggle-switch {
    position: relative;
    display: inline-block;
    width: 40px;
    height: 22px;
    flex-shrink: 0;
  }
  .toggle-switch input {
    opacity: 0;
    width: 0;
    height: 0;
  }
  .toggle-slider {
    position: absolute;
    inset: 0;
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    border-radius: 999px;
    transition: all var(--ease);
    cursor: pointer;
  }
  .toggle-slider::before {
    content: '';
    position: absolute;
    width: 16px;
    height: 16px;
    left: 2px;
    top: 2px;
    background: var(--text-muted);
    border-radius: 50%;
    transition: all var(--ease);
  }
  .toggle-switch input:checked + .toggle-slider {
    background: var(--accent-muted);
    border-color: var(--accent);
  }
  .toggle-switch input:checked + .toggle-slider::before {
    transform: translateX(18px);
    background: var(--accent);
  }

  /* Context sources */
  .im-ctx-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .im-ctx-source {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .im-ctx-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.6rem 0.9rem;
    cursor: pointer;
    background: var(--bg-raised);
    transition: background var(--ease);
    user-select: none;
  }
  .im-ctx-header:hover {
    background: var(--bg-overlay);
  }
  .im-ctx-name {
    font-size: 0.825rem;
    font-weight: 500;
    color: var(--text-primary);
  }
  .im-ctx-chevron {
    font-size: 0.7rem;
    color: var(--text-muted);
    transition: transform var(--ease);
  }
  .im-ctx-source.open .im-ctx-chevron {
    transform: rotate(180deg);
  }
  .im-ctx-body {
    display: none;
    padding: 0.75rem 0.9rem;
    border-top: 1px solid var(--border);
  }
  .im-ctx-source.open .im-ctx-body {
    display: block;
  }
  .im-ctx-text {
    font-size: 0.8rem;
    color: var(--text-secondary);
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 300px;
    overflow-y: auto;
    margin: 0;
  }
  .im-ctx-empty {
    text-align: center;
    padding: 1.5rem;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .im-ctx-actions {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 0.75rem;
  }
</style>

<div class="im-layout">

  <!-- Status Card -->
  <div class="card">
    <div class="card-header"><h3>Status</h3></div>
    <div class="im-enabled-indicator">
      <span class="status-dot" id="im-enabled-dot"></span>
      <span id="im-enabled-label">---</span>
    </div>
    <div class="im-status-grid">
      <div class="im-stat-item">
        <div class="im-stat-label">Mood</div>
        <div class="im-stat-value" id="im-mood"><span class="badge badge-muted">---</span></div>
      </div>
      <div class="im-stat-item">
        <div class="im-stat-label">Energy</div>
        <div class="im-stat-value" id="im-energy">---</div>
      </div>
      <div class="im-stat-item">
        <div class="im-stat-label">Interest</div>
        <div class="im-stat-value" id="im-interest">---</div>
      </div>
    </div>
    <div class="im-monologue-preview" id="im-last-monologue" style="display:none">
      <div class="im-monologue-preview-label">Last Monologue</div>
      <p class="im-monologue-text" id="im-monologue-text"></p>
      <div class="im-monologue-time" id="im-monologue-time"></div>
    </div>
  </div>

  <!-- Settings Card -->
  <div class="card">
    <div class="card-header"><h3>Settings</h3></div>
    <div class="im-settings-grid">
      <div class="form-group im-toggle-row">
        <div class="form-row">
          <label class="form-label" style="margin-bottom:0">Enabled</label>
          <label class="toggle-switch">
            <input type="checkbox" id="im-s-enabled">
            <span class="toggle-slider"></span>
          </label>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Speak Probability</label>
        <input type="number" id="im-s-speak-prob" class="form-input" min="0" max="1" step="0.05">
        <div class="form-hint">Chance of speaking after thinking (0.0 - 1.0)</div>
      </div>
      <div class="form-group">
        <label class="form-label">Min Speak Interval (min)</label>
        <input type="number" id="im-s-speak-interval" class="form-input" min="0" step="1">
        <div class="form-hint">Minutes between autonomous messages</div>
      </div>
      <div class="form-group">
        <label class="form-label">Thinking Interval (ticks)</label>
        <input type="number" id="im-s-think-ticks" class="form-input" min="1" step="1">
        <div class="form-hint">Heartbeat ticks between thoughts</div>
      </div>
      <div class="form-group">
        <label class="form-label">Speak Channel ID</label>
        <input type="text" id="im-s-channel" class="form-input" placeholder="Discord channel ID">
      </div>
      <div class="form-group">
        <label class="form-label">Target User ID</label>
        <input type="text" id="im-s-user" class="form-input" placeholder="Discord user ID to monitor">
      </div>
      <div class="form-group im-toggle-row">
        <label class="form-label">Tavily News Queries</label>
        <input type="text" id="im-s-tavily" class="form-input" placeholder="生成AI, VTuber">
        <div class="form-hint">カンマ区切りで複数指定可。TAVILY_API_KEY が必須</div>
      </div>
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="im-s-save">Save</button>
    </div>
  </div>

  <!-- Context Sources Card -->
  <div class="card">
    <div class="card-header"><h3>Context Sources</h3></div>
    <div class="im-ctx-actions">
      <button class="btn btn-sm" id="im-ctx-refresh">Refresh</button>
    </div>
    <div class="im-ctx-list" id="im-ctx-list">
      <div class="im-ctx-empty">Loading...</div>
    </div>
  </div>

</div>`;
}

// ============================================================
// Rendering helpers
// ============================================================
function renderMoodBadge(mood) {
  const cls = MOOD_BADGE[mood] || 'badge-muted';
  return `<span class="badge ${cls}">${esc(mood || 'unknown')}</span>`;
}

function renderContextSources(sources) {
  if (!sources || !sources.length) {
    return '<div class="im-ctx-empty">No context sources available.</div>';
  }
  return sources.map((src, i) => `
    <div class="im-ctx-source" data-ctx-idx="${i}">
      <div class="im-ctx-header">
        <span class="im-ctx-name">${esc(src.name)}</span>
        <span class="im-ctx-chevron">&#9660;</span>
      </div>
      <div class="im-ctx-body">
        <pre class="im-ctx-text">${esc(src.text)}</pre>
      </div>
    </div>
  `).join('');
}

// ============================================================
// Data loading
// ============================================================
async function loadStatus() {
  try {
    const data = await api('/api/inner-mind/status');

    // Enabled indicator
    const dot = $('im-enabled-dot');
    const label = $('im-enabled-label');
    if (dot && label) {
      const enabled = !!data.enabled;
      dot.className = 'status-dot ' + (enabled ? 'online pulse' : '');
      label.textContent = enabled ? 'Active' : 'Inactive';
    }

    // Self model
    const sm = data.self_model || {};
    $('im-mood').innerHTML = renderMoodBadge(sm.mood);
    $('im-energy').textContent = sm.energy_level ?? '---';
    $('im-interest').textContent = sm.interest_topic || '---';

    // Last monologue
    const lm = data.last_monologue;
    const preview = $('im-last-monologue');
    if (lm && lm.monologue) {
      preview.style.display = '';
      $('im-monologue-text').textContent = lm.monologue;
      $('im-monologue-time').innerHTML =
        `<span title="${esc(fullDatetime(lm.created_at))}">${timeAgo(lm.created_at)}</span>` +
        (lm.mood ? ` &middot; ${renderMoodBadge(lm.mood)}` : '');
    } else {
      preview.style.display = 'none';
    }
  } catch (err) {
    console.error('Load InnerMind status:', err);
    toast('Failed to load InnerMind status', 'error');
  }
}

async function loadSettings() {
  try {
    const data = await api('/api/inner-mind/settings');
    $('im-s-enabled').checked = !!data.enabled;
    $('im-s-speak-prob').value = data.speak_probability ?? '';
    $('im-s-speak-interval').value = data.min_speak_interval_minutes ?? '';
    $('im-s-think-ticks').value = data.thinking_interval_ticks ?? '';
    $('im-s-channel').value = data.speak_channel_id || '';
    $('im-s-user').value = data.target_user_id || '';
    $('im-s-tavily').value = (data.tavily_queries || []).join(', ');
  } catch (err) {
    console.error('Load InnerMind settings:', err);
    toast('Failed to load InnerMind settings', 'error');
  }
}

async function loadContext() {
  const list = $('im-ctx-list');
  if (!list) return;
  list.innerHTML = '<div class="im-ctx-empty">Loading...</div>';
  try {
    const data = await api('/api/inner-mind/context');
    list.innerHTML = renderContextSources(data.sources || []);
    attachContextToggle();
  } catch (err) {
    console.error('Load InnerMind context:', err);
    list.innerHTML = '<div class="im-ctx-empty">Failed to load context sources.</div>';
    toast('Failed to load context sources', 'error');
  }
}

async function saveSettings() {
  try {
    await api('/api/inner-mind/settings', {
      method: 'POST',
      body: {
        enabled: $('im-s-enabled').checked,
        speak_probability: Number($('im-s-speak-prob').value),
        min_speak_interval_minutes: Number($('im-s-speak-interval').value),
        thinking_interval_ticks: Number($('im-s-think-ticks').value),
        speak_channel_id: $('im-s-channel').value,
        target_user_id: $('im-s-user').value,
        tavily_queries: $('im-s-tavily').value.split(',').map(s => s.trim()).filter(Boolean),
      },
    });
    toast('InnerMind settings saved', 'success');
    // Refresh status to reflect changes
    await loadStatus();
  } catch (err) {
    console.error('Save InnerMind settings:', err);
    toast('Failed to save InnerMind settings', 'error');
  }
}

// ============================================================
// Event helpers
// ============================================================
function attachContextToggle() {
  const sources = document.querySelectorAll('.im-ctx-source');
  sources.forEach(el => {
    const header = el.querySelector('.im-ctx-header');
    if (!header) return;
    header.addEventListener('click', () => {
      el.classList.toggle('open');
    });
  });
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  // Save button
  $('im-s-save')?.addEventListener('click', saveSettings);

  // Context refresh
  $('im-ctx-refresh')?.addEventListener('click', loadContext);

  // Load all data in parallel
  await Promise.all([loadStatus(), loadSettings(), loadContext()]);
}
