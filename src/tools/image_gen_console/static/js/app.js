/** Image Gen Console — hash router + toast。
 *
 * 各ページモジュールは以下の API を export:
 *   render(params): string  — HTML を返す
 *   mount(params): Promise  — DOM に紐づけ
 *   unmount(): void         — タイマー/SSE 等のクリーンアップ
 */

import * as generate from './pages/generate.js';
import * as jobs from './pages/jobs.js';
import * as gallery from './pages/gallery.js';
import * as prompts from './pages/prompts.js';

const routes = [
  { hash: '#/generate', module: generate, nav: 'generate', title: 'Generate' },
  { hash: '#/jobs',     module: jobs,     nav: 'jobs',     title: 'Jobs' },
  { hash: '#/gallery',  module: gallery,  nav: 'gallery',  title: 'Gallery' },
  { hash: '#/prompts',  module: prompts,  nav: 'prompts',  title: 'Prompts' },
];

const DEFAULT_HASH = '#/generate';

let currentModule = null;
let _navGen = 0;

function resolve(rawHash) {
  // クエリ部を除いてマッチさせる
  const base = (rawHash || '').split('?')[0];
  return routes.find(r => r.hash === base) || null;
}

async function navigate() {
  const rawHash = location.hash || DEFAULT_HASH;
  const route = resolve(rawHash);
  if (!route) {
    location.hash = DEFAULT_HASH;
    return;
  }

  if (currentModule?.unmount) {
    try { currentModule.unmount(); } catch { /* silent */ }
  }
  currentModule = route.module;
  const gen = ++_navGen;

  document.querySelectorAll('.ig-nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === route.nav);
  });

  const main = document.getElementById('main-content');
  try {
    main.innerHTML = route.module.render();
    if (route.module.mount) await route.module.mount();
  } catch (err) {
    if (gen !== _navGen) return;
    console.error(`page mount error (${route.nav}):`, err);
    main.innerHTML = `<div class="imggen-empty">ページの読み込みに失敗しました<br><span class="text-xs text-muted">${escapeHtml(err.message || String(err))}</span></div>`;
    toast(`${route.title} の読み込みに失敗`, 'error');
  }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ============================================================
// Toast（src/web/static/js/app.js と同じ API）
// ============================================================
export function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
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
// Topbar status
// ============================================================
async function refreshTopbarStatus() {
  const el = document.getElementById('ig-topbar-status');
  if (!el) return;
  try {
    const res = await fetch('/health');
    const h = await res.json();
    const ver = (h.version || '').slice(0, 7);
    el.textContent = ver ? `v.${ver}` : '';
  } catch { /* silent */ }
}

// ============================================================
// Init
// ============================================================
window.addEventListener('hashchange', navigate);
document.addEventListener('DOMContentLoaded', () => {
  if (!location.hash) location.hash = DEFAULT_HASH;
  navigate();
  refreshTopbarStatus();
  setInterval(refreshTopbarStatus, 60000);
});
