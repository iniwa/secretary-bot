/** Dashboard page. */
import { api, apiBatch } from '../api.js';

const MOOD_BADGE = {
  curious:   'badge-info',
  calm:      'badge-success',
  talkative: 'badge-accent',
  concerned: 'badge-warning',
  idle:      'badge-muted',
};

function formatUptime(seconds) {
  if (!seconds || seconds < 0) return '---';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function timeAgo(isoStr) {
  if (!isoStr) return '---';
  const diff = Date.now() - new Date(isoStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function render() {
  return `
<div class="dashboard">
  <section class="status-row">
    <div class="stat-card">
      <div class="stat-card-header">
        <span class="stat-label">Bot</span>
        <span class="status-dot online pulse" id="d-bot-dot"></span>
      </div>
      <div class="stat-value mono" id="d-version">---</div>
      <div class="stat-sub" id="d-uptime">---</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <span class="stat-label">Ollama</span>
        <span class="status-dot" id="d-ollama-dot"></span>
      </div>
      <div class="stat-value" id="d-ollama-model">---</div>
      <div class="stat-sub" id="d-ollama-status">checking...</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <span class="stat-label">Agents</span>
        <span class="status-dot" id="d-agent-dot"></span>
      </div>
      <div class="stat-value" id="d-agent-count">---</div>
      <div class="stat-sub" id="d-agent-detail">checking...</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <span class="stat-label">InnerMind</span>
        <span class="status-dot" id="d-im-dot"></span>
      </div>
      <div class="stat-value" id="d-im-status">---</div>
      <div class="stat-sub" id="d-im-detail">checking...</div>
    </div>
  </section>

  <section class="card activity-card" id="d-game-card">
    <div class="card-header">
      <h3>Main PC Activity</h3>
      <span class="status-dot" id="d-game-dot" title="Main PC agent status"></span>
    </div>
    <div class="activity-row">
      <div class="activity-cell">
        <div class="stat-label">Playing</div>
        <div class="activity-value" id="d-game-name">---</div>
      </div>
      <div class="activity-cell-wide">
        <div class="stat-label">Foreground Process</div>
        <div class="mono activity-value" id="d-game-fg">---</div>
      </div>
      <div id="d-game-fs-wrap" hidden>
        <span class="badge badge-info" id="d-game-fs">Fullscreen</span>
      </div>
    </div>
  </section>

  <section class="monologue-card" id="d-monologue">
    <div class="card-header">
      <h3>Latest Monologue</h3>
      <span class="badge badge-muted" id="d-mono-mood">---</span>
    </div>
    <p class="monologue-text" id="d-mono-text">Loading...</p>
    <div class="monologue-meta">
      <span id="d-mono-time">---</span>
    </div>
  </section>

  <section class="quick-stats">
    <div class="mini-card">
      <div class="mini-value" id="d-reminders">-</div>
      <div class="mini-label">Active Reminders</div>
    </div>
    <div class="mini-card">
      <div class="mini-value" id="d-memos">-</div>
      <div class="mini-label">Memos</div>
    </div>
    <div class="mini-card">
      <div class="mini-value" id="d-convs">-</div>
      <div class="mini-label">Conversations</div>
    </div>
    <div class="mini-card">
      <div class="mini-value" id="d-ai-mem">-</div>
      <div class="mini-label">AI Memories</div>
    </div>
  </section>
</div>`;
}

function $(id) { return document.getElementById(id); }

// ------------------------------------------------------------
// Main PC game activity (polled; card is updated independently)
// ------------------------------------------------------------
let _gamePollTimer = null;

async function refreshMainActivity() {
  const dot = $('d-game-dot');
  const nameEl = $('d-game-name');
  const fgEl = $('d-game-fg');
  const fsWrap = $('d-game-fs-wrap');
  if (!nameEl) return;

  try {
    const data = await api('/api/activity/main');
    if (!data || data.alive === false) {
      if (dot) dot.className = 'status-dot error';
      nameEl.textContent = '---';
      fgEl.textContent = data?.error || 'Main PC agent not reachable';
      if (fsWrap) fsWrap.hidden = true;
      return;
    }
    if (dot) dot.className = 'status-dot online';
    const game = data.game;
    const fg = data.foreground_process || '';
    const isFs = data.is_fullscreen === true;
    nameEl.textContent = game || 'No Game';
    nameEl.style.color = game ? 'var(--text-primary)' : 'var(--text-muted)';
    fgEl.textContent = fg || '(none)';
    if (fsWrap) fsWrap.hidden = !isFs;
  } catch (err) {
    if (dot) dot.className = 'status-dot error';
    nameEl.textContent = '---';
    fgEl.textContent = 'Error: ' + (err?.message || 'fetch failed');
    if (fsWrap) fsWrap.hidden = true;
  }
}

export async function mount() {
  // Kick off Main PC activity fetch in parallel (non-blocking for rest of dashboard)
  refreshMainActivity();
  _gamePollTimer = setInterval(refreshMainActivity, 10000);

  const [status, monologues, imStatus, reminders, memos, llmConfig] = await apiBatch([
    ['/api/status'],
    ['/api/monologue', { params: { limit: 1 } }],
    ['/api/inner-mind/status'],
    ['/api/units/reminders', { params: { active: 1 } }],
    ['/api/units/memos'],
    ['/api/llm-config'],
  ]);

  // Bot status
  if (status) {
    $('d-version').textContent = status.version?.slice(0, 7) || '---';
    $('d-uptime').textContent = formatUptime(status.uptime);

    // Ollama
    const dot = $('d-ollama-dot');
    if (status.ollama) {
      dot.className = 'status-dot online';
      $('d-ollama-status').textContent = 'Connected';
    } else {
      dot.className = 'status-dot error';
      $('d-ollama-status').textContent = 'Disconnected';
    }

    // Agents
    const agents = status.agents || [];
    const alive = agents.filter(a => a.alive).length;
    const restarting = agents.filter(a => a.status === 'restarting').length;
    const aDot = $('d-agent-dot');
    let aDotClass;
    if (alive === agents.length && agents.length > 0) {
      aDotClass = 'online';
    } else if (restarting > 0) {
      aDotClass = 'warning pulse';
    } else if (alive > 0) {
      aDotClass = 'warning';
    } else {
      aDotClass = 'error';
    }
    aDot.className = 'status-dot ' + aDotClass;
    $('d-agent-count').textContent = `${alive} / ${agents.length}`;
    $('d-agent-detail').textContent = agents.map(a => {
      if (a.status === 'restarting') return `${a.name}: RESTARTING`;
      return `${a.name}: ${a.alive ? 'ON' : 'OFF'}`;
    }).join(', ') || 'No agents';

    // Memory stats for quick stats
    if (status.memory) {
      $('d-ai-mem').textContent = status.memory.ai_memory >= 0 ? status.memory.ai_memory : '-';
    }
    // Conversations
    if (status.db) {
      $('d-convs').textContent = status.db.conversation_log >= 0 ? status.db.conversation_log.toLocaleString() : '-';
    }
  }

  // Ollama model
  if (llmConfig) {
    $('d-ollama-model').textContent = llmConfig.ollama_model || '---';
  }

  // InnerMind
  if (imStatus) {
    const enabled = imStatus.enabled;
    const imDot = $('d-im-dot');
    imDot.className = 'status-dot ' + (enabled ? 'online pulse' : '');
    $('d-im-status').textContent = enabled ? 'Active' : 'Inactive';
    const mood = imStatus.self_model?.mood;
    const lastMono = imStatus.last_monologue;
    const parts = [];
    if (mood) parts.push(`mood: ${mood}`);
    if (lastMono?.created_at) parts.push(timeAgo(lastMono.created_at));
    $('d-im-detail').textContent = parts.join(' / ') || '---';
  }

  // Monologue
  if (monologues?.monologues?.length) {
    const m = monologues.monologues[0];
    $('d-mono-text').textContent = m.monologue || '(empty)';
    $('d-mono-time').textContent = m.created_at ? timeAgo(m.created_at) : '---';
    const moodEl = $('d-mono-mood');
    if (m.mood) {
      moodEl.textContent = m.mood;
      moodEl.className = `badge ${MOOD_BADGE[m.mood] || 'badge-muted'}`;
    }
  } else {
    $('d-mono-text').textContent = 'No monologues yet.';
  }

  // Reminders
  if (reminders?.items) {
    $('d-reminders').textContent = reminders.items.length;
  }

  // Memos
  if (memos?.items) {
    $('d-memos').textContent = memos.items.length;
  }
}

export function unmount() {
  if (_gamePollTimer) {
    clearInterval(_gamePollTimer);
    _gamePollTimer = null;
  }
}
