/** Main application — router, navigation, toast system. */
import { api } from './api.js';
import * as dashboard from './pages/dashboard.js';
import * as chat from './pages/chat.js';
import * as settings from './pages/settings.js';
import * as logs from './pages/logs.js';
import * as maintenance from './pages/maintenance.js';
import * as reminder from './pages/reminder.js';
import * as todo from './pages/todo.js';
import * as memo from './pages/memo.js';
import * as timer from './pages/timer.js';
import * as weather from './pages/weather.js';
import * as rss from './pages/rss.js';
import * as monologue from './pages/monologue.js';
import * as memory from './pages/memory.js';
import * as innermind from './pages/innermind.js';

// ============================================================
// Page registry — add pages here as they're implemented
// ============================================================
const pages = {
  dashboard:    { title: 'Dashboard',        module: dashboard },
  chat:         { title: 'Chat',             module: chat },
  reminder:     { title: 'Reminder',         module: reminder },
  todo:         { title: 'Todo',             module: todo },
  memo:         { title: 'Memo',             module: memo },
  timer:        { title: 'Timer',            module: timer },
  weather:      { title: 'Weather',          module: weather },
  rss:          { title: 'RSS',              module: rss },
  monologue:    { title: 'Monologue',        module: monologue },
  memory:       { title: 'Memory',           module: memory },
  innermind:    { title: 'InnerMind Settings', module: innermind },
  obs:          { title: 'OBS',              module: null },
  'input-relay':{ title: 'Input Relay',      module: null },
  stt:          { title: 'STT',              module: null },
  settings:     { title: 'Settings',         module: settings },
  logs:         { title: 'Logs',             module: logs },
  maintenance:  { title: 'Maintenance',      module: maintenance },
};

let currentPage = null;
let currentModule = null;

// ============================================================
// Router
// ============================================================
function getPageFromHash() {
  const hash = location.hash.replace('#', '') || 'dashboard';
  return pages[hash] ? hash : 'dashboard';
}

async function navigate(pageName) {
  if (pageName === currentPage) return;

  // Unmount previous page
  if (currentModule?.unmount) {
    try { currentModule.unmount(); } catch { /* silent */ }
  }

  currentPage = pageName;
  const page = pages[pageName];
  currentModule = page.module;

  // Update nav
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === pageName);
  });

  // Update topbar title
  document.getElementById('page-title').textContent = page.title;

  // Render content
  const main = document.getElementById('main-content');
  if (page.module) {
    main.innerHTML = page.module.render();
    try {
      await page.module.mount();
    } catch (err) {
      console.error(`Page mount error (${pageName}):`, err);
      toast(`Failed to load ${page.title}`, 'error');
    }
  } else {
    main.innerHTML = `
      <div class="page-placeholder">
        <div class="page-placeholder-title">${page.title}</div>
        <div>Coming soon</div>
      </div>`;
  }

  // Close mobile sidebar
  closeSidebar();

  // Update hash without triggering hashchange
  history.replaceState(null, '', '#' + pageName);
}

// ============================================================
// Sidebar (mobile toggle)
// ============================================================
function openSidebar() {
  document.getElementById('sidebar').classList.add('open');
  document.getElementById('sidebar-overlay').classList.add('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
}

// ============================================================
// Toast
// ============================================================
export function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('removing');
    el.addEventListener('animationend', () => el.remove());
  }, 3000);
}

// ============================================================
// Topbar status chips
// ============================================================
function chip(state, label) {
  return `<span class="status-chip chip-${state}"><span class="chip-dot"></span>${label}</span>`;
}

async function refreshTopbarStatus() {
  const container = document.getElementById('topbar-status');
  if (!container) return;
  try {
    const [s, gemini] = await Promise.all([
      api('/api/status'),
      api('/api/gemini-config').catch(() => null),
    ]);
    const chips = [];

    // Ollama
    chips.push(chip(s.ollama ? 'ok' : 'ng', 'Ollama'));

    // Gemini
    const geminiEnabled = gemini && (gemini.conversation || gemini.memory_extraction || gemini.unit_routing);
    chips.push(chip(geminiEnabled ? 'ok' : 'off', 'Gemini'));

    // Agents
    const agents = s.agents || [];
    const alive = agents.filter(a => a.alive).length;
    const agentState = agents.length === 0 ? 'off' : alive === agents.length ? 'ok' : alive > 0 ? 'warn' : 'ng';
    chips.push(chip(agentState, `Agent ${alive}/${agents.length}`));

    container.innerHTML = chips.join('');
  } catch { /* silent */ }
}

// ============================================================
// Sidebar footer version
// ============================================================
async function refreshFooter() {
  try {
    const h = await api('/health');
    const el = document.getElementById('sidebar-footer');
    if (el) el.textContent = `v.${(h.version || '').slice(0, 7)} / up ${formatUptime(h.uptime)}`;
  } catch { /* silent */ }
}
function formatUptime(sec) {
  if (!sec) return '---';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h${m}m` : `${m}m`;
}

// ============================================================
// Init
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  // Nav clicks
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      navigate(el.dataset.page);
    });
  });

  // Mobile menu
  document.getElementById('menu-btn').addEventListener('click', openSidebar);
  document.getElementById('sidebar-overlay').addEventListener('click', closeSidebar);

  // Hash navigation
  window.addEventListener('hashchange', () => navigate(getPageFromHash()));

  // Initial page
  navigate(getPageFromHash());

  // Periodic status refresh
  refreshTopbarStatus();
  refreshFooter();
  setInterval(refreshTopbarStatus, 15000);
  setInterval(refreshFooter, 60000);
});
