/** ZZZ Disc Manager — hash router + toast + modal helpers. */
import * as capture from './pages/capture.js';
import * as discs from './pages/discs.js';
import * as discDetail from './pages/disc_detail.js';
import * as upload from './pages/upload.js';
import * as presets from './pages/presets.js';
import * as presetEdit from './pages/preset_edit.js';
import * as conflict from './pages/conflict.js';

const routes = [
  { pattern: /^#\/capture$/,                 module: capture,     nav: 'capture' },
  { pattern: /^#\/discs$/,                   module: discs,       nav: 'discs' },
  { pattern: /^#\/discs\/([^/]+)$/,          module: discDetail,  nav: 'discs', param: 'id' },
  { pattern: /^#\/upload$/,                  module: upload,      nav: 'upload' },
  { pattern: /^#\/presets$/,                 module: presets,     nav: 'presets' },
  { pattern: /^#\/presets\/([^/]+)$/,        module: presetEdit,  nav: 'presets', param: 'slug' },
  { pattern: /^#\/conflicts$/,               module: conflict,    nav: 'conflicts' },
];

let currentModule = null;
let _navGen = 0;

function resolve(hash) {
  for (const r of routes) {
    const m = hash.match(r.pattern);
    if (m) return { route: r, params: r.param ? { [r.param]: decodeURIComponent(m[1]) } : {} };
  }
  return null;
}

async function navigate() {
  const hash = location.hash || '#/capture';
  const resolved = resolve(hash);
  if (!resolved) {
    location.hash = '#/capture';
    return;
  }
  const { route, params } = resolved;

  // unmount previous
  if (currentModule?.unmount) {
    try { currentModule.unmount(); } catch { /* silent */ }
  }

  currentModule = route.module;
  const gen = ++_navGen;

  // update nav
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === route.nav);
  });

  const main = document.getElementById('main-content');
  try {
    main.innerHTML = route.module.render(params);
    if (route.module.mount) await route.module.mount(params);
  } catch (err) {
    if (gen !== _navGen) return;
    console.error('page mount error:', err);
    main.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>ページの読み込みに失敗しました</div><div class="text-muted text-sm mt-1">${escapeHtml(err.message || String(err))}</div></div>`;
  }
}

export function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

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

export function openModal({ title, body, footer }) {
  const root = document.getElementById('modal-root');
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-header">
        <h3>${escapeHtml(title || '')}</h3>
        <button class="close-btn" data-action="close">×</button>
      </div>
      <div class="modal-body"></div>
      <div class="modal-footer"></div>
    </div>
  `;
  const bodyEl = backdrop.querySelector('.modal-body');
  const footerEl = backdrop.querySelector('.modal-footer');
  if (typeof body === 'string') bodyEl.innerHTML = body;
  else if (body instanceof Node) bodyEl.appendChild(body);
  if (typeof footer === 'string') footerEl.innerHTML = footer;
  else if (footer instanceof Node) footerEl.appendChild(footer);

  const close = () => backdrop.remove();
  backdrop.querySelector('[data-action="close"]').addEventListener('click', close);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  root.appendChild(backdrop);
  return { backdrop, bodyEl, footerEl, close };
}

export function confirmDialog(message) {
  return new Promise(resolve => {
    const { backdrop, footerEl, close } = openModal({
      title: '確認',
      body: `<p>${escapeHtml(message)}</p>`,
    });
    footerEl.innerHTML = `
      <button class="btn" data-act="cancel">キャンセル</button>
      <button class="btn btn-danger" data-act="ok">OK</button>
    `;
    footerEl.querySelector('[data-act="cancel"]').addEventListener('click', () => { close(); resolve(false); });
    footerEl.querySelector('[data-act="ok"]').addEventListener('click', () => { close(); resolve(true); });
  });
}

// ============================================================
// Init
// ============================================================
window.addEventListener('hashchange', navigate);
document.addEventListener('DOMContentLoaded', () => {
  if (!location.hash) location.hash = '#/capture';
  navigate();
});
