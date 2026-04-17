/** Image Gen Console — hash router + toast。
 *
 * ページモジュールは以下の API を持つ:
 *   render(): string         — 初回 1 回だけ呼ばれる。HTML を返す
 *   mount(): Promise         — 初回 1 回だけ呼ばれる。イベント結線・初期データ取得
 *   onShow?(rawHash): void   — 表示されるたびに呼ばれる（タイマー再開・クエリ反映）
 *   onHide?(): void          — 非表示になるときに呼ばれる（タイマー停止）
 *
 * ページコンテナはタブ切替で破棄されないため、フォーム値・取得済みデータ・
 * スクロール位置がすべて保持される。
 */

import * as generate from './pages/generate.js';
import * as jobs from './pages/jobs.js';
import * as gallery from './pages/gallery.js';
import * as prompts from './pages/prompts.js';
import * as extract from './pages/extract.js';

const routes = [
  { hash: '#/generate', module: generate, nav: 'generate', title: 'Generate' },
  { hash: '#/jobs',     module: jobs,     nav: 'jobs',     title: 'Jobs' },
  { hash: '#/gallery',  module: gallery,  nav: 'gallery',  title: 'Gallery' },
  { hash: '#/prompts',  module: prompts,  nav: 'prompts',  title: 'Prompts' },
  { hash: '#/extract',  module: extract,  nav: 'extract',  title: 'Extract' },
];

const DEFAULT_HASH = '#/generate';

const containers = {};   // nav -> HTMLElement
let currentNav = null;
let _navGen = 0;

function resolve(rawHash) {
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

  // 切替前ページの onHide
  if (currentNav && currentNav !== route.nav) {
    const prev = containers[currentNav];
    if (prev) {
      prev.el.style.display = 'none';
      try { prev.module.onHide?.(); } catch (err) { console.error('onHide failed', err); }
    }
  }

  // ナビ ハイライト更新
  document.querySelectorAll('.ig-nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === route.nav);
  });

  // 既存コンテナがあれば表示するだけ。なければ初回マウント
  let entry = containers[route.nav];
  const main = document.getElementById('main-content');
  const gen = ++_navGen;

  if (!entry) {
    const el = document.createElement('div');
    el.className = 'ig-page-container';
    el.dataset.page = route.nav;
    try {
      el.innerHTML = route.module.render();
      main.appendChild(el);
      entry = { el, module: route.module };
      containers[route.nav] = entry;
      if (route.module.mount) await route.module.mount();
    } catch (err) {
      if (gen !== _navGen) return;
      console.error(`page mount error (${route.nav}):`, err);
      el.innerHTML = `<div class="imggen-empty">ページの読み込みに失敗しました<br><span class="text-xs text-muted">${escapeHtml(err.message || String(err))}</span></div>`;
      toast(`${route.title} の読み込みに失敗`, 'error');
    }
  } else {
    entry.el.style.display = '';
  }

  currentNav = route.nav;

  // 表示のたびに呼ばれる（mount の直後でも 1 回呼ばれる）
  try { route.module.onShow?.(rawHash); } catch (err) { console.error('onShow failed', err); }
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
