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
import * as obs from './pages/obs.js';
import * as inputRelay from './pages/input-relay.js';
import * as stt from './pages/stt.js';
import * as activity from './pages/activity.js';
import * as dockerMonitor from './pages/docker-monitor.js';
import * as pending from './pages/pending.js';
import * as clipPipeline from './pages/clip-pipeline.js';

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
  obs:          { title: 'OBS',              module: obs },
  'input-relay':{ title: 'Input Relay',      module: inputRelay },
  stt:          { title: 'STT',              module: stt },
  activity:     { title: 'Activity',         module: activity },
  settings:     { title: 'Settings',         module: settings },
  logs:         { title: 'Logs',             module: logs },
  'docker-monitor': { title: 'Docker Monitor', module: dockerMonitor },
  pending:      { title: 'Pending Actions',  module: pending },
  maintenance:  { title: 'Maintenance',      module: maintenance },
  'clip-pipeline': { title: 'Auto-Kirinuki', module: clipPipeline },
};

let currentPage = null;
let currentModule = null;
let _navGen = 0;  // ナビゲーション世代カウンター（高速切り替え時の競合防止）

// ============================================================
// Router
// ============================================================
function getPageFromHash() {
  const raw = location.hash.replace('#', '') || 'dashboard';
  const hash = raw.split('?')[0];  // クエリ部は除去
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
  const gen = ++_navGen;  // この navigate 呼び出しの世代を記録

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
      // 世代が変わっていたら（別ページへ遷移済み）エラーを無視
      if (gen !== _navGen) return;
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
// Sidebar group collapse (persisted in localStorage)
// ============================================================
const COLLAPSE_KEY = 'sidebar-collapsed-groups-v2';

function initCollapsibleGroups() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;

  const stored = localStorage.getItem(COLLAPSE_KEY);
  const hasStored = stored !== null;
  let collapsed = new Set();
  try { collapsed = new Set(hasStored ? JSON.parse(stored) : []); } catch { /* silent */ }

  // 初期状態の復元（保存があれば優先、なければHTMLの初期状態を読み取って保存）
  sidebar.querySelectorAll('.nav-group[data-group]').forEach(group => {
    const name = group.dataset.group;
    if (hasStored) {
      group.classList.toggle('collapsed', collapsed.has(name));
    } else if (group.classList.contains('collapsed')) {
      collapsed.add(name);
    }
    const label = group.querySelector('.nav-group-label');
    if (label) label.setAttribute('aria-expanded', group.classList.contains('collapsed') ? 'false' : 'true');
  });
  if (!hasStored) {
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed])); } catch { /* silent */ }
  }

  // Event delegation: ラベル内の SVG/span をクリックしてもラベルに解決する
  sidebar.addEventListener('click', (e) => {
    const label = e.target.closest('.nav-group-label');
    if (!label) return;
    const group = label.closest('.nav-group[data-group]');
    if (!group) return;
    e.preventDefault();
    const name = group.dataset.group;
    const isCollapsed = group.classList.toggle('collapsed');
    label.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
    if (isCollapsed) collapsed.add(name); else collapsed.delete(name);
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed])); } catch { /* silent */ }
  });
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
  // Nav clicks（data-page を持たない外部リンク（target=_blank 等）はブラウザの既定挙動に任せる）
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (!el.dataset.page) return;
      e.preventDefault();
      navigate(el.dataset.page);
    });
  });

  // Mobile menu
  document.getElementById('menu-btn').addEventListener('click', openSidebar);
  document.getElementById('sidebar-overlay').addEventListener('click', closeSidebar);

  // Collapsible nav groups
  initCollapsibleGroups();

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
