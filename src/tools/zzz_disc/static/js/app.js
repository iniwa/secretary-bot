/** ZZZ Disc Manager — hash router + toast + modal helpers. */
import * as characters from './pages/characters.js';
import * as characterDetail from './pages/character_detail.js';
import * as discs from './pages/discs.js';
import * as discDetail from './pages/disc_detail.js';
import * as capture from './pages/capture.js';
import * as upload from './pages/upload.js';
import * as shared from './pages/shared.js';
import * as settings from './pages/settings.js';

const routes = [
  { pattern: /^#\/characters$/,              module: characters,       nav: 'characters' },
  { pattern: /^#\/characters\/([^/]+)$/,     module: characterDetail,  nav: 'characters', param: 'slug' },
  { pattern: /^#\/discs$/,                   module: discs,            nav: 'discs' },
  { pattern: /^#\/discs\/([^/]+)$/,          module: discDetail,       nav: 'discs', param: 'id' },
  { pattern: /^#\/capture$/,                 module: capture,          nav: 'capture',      pref: 'show_capture' },
  { pattern: /^#\/upload$/,                  module: upload,           nav: 'upload',       pref: 'show_upload' },
  { pattern: /^#\/shared$/,                  module: shared,           nav: 'shared' },
  { pattern: /^#\/settings$/,                module: settings,         nav: 'settings' },
];

// UI 設定（localStorage）: 任意機能の表示切替。デフォルトは全 OFF。
const PREF_KEY = 'zzz_disc.ui_prefs';
const PREF_DEFAULTS = { show_upload: false, show_capture: false };

export function getUiPrefs() {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (!raw) return { ...PREF_DEFAULTS };
    return { ...PREF_DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...PREF_DEFAULTS };
  }
}

export function setUiPref(key, value) {
  const next = { ...getUiPrefs(), [key]: !!value };
  localStorage.setItem(PREF_KEY, JSON.stringify(next));
  applyUiPrefs();
}

function applyUiPrefs() {
  const prefs = getUiPrefs();
  document.querySelectorAll('[data-optional]').forEach(el => {
    const key = `show_${el.dataset.optional}`;
    el.hidden = !prefs[key];
  });
}

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
  const hash = location.hash || '#/characters';
  const resolved = resolve(hash);
  if (!resolved) {
    location.hash = '#/characters';
    return;
  }
  const { route, params } = resolved;

  // 無効化された任意ページ（設定で OFF）への直接遷移は弾く
  if (route.pref && !getUiPrefs()[route.pref]) {
    location.hash = '#/characters';
    return;
  }

  if (currentModule?.unmount) {
    try { currentModule.unmount(); } catch { /* silent */ }
  }

  currentModule = route.module;
  const gen = ++_navGen;

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
    const { footerEl, close } = openModal({
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

export function promptDialog({ title = '入力', label = '', value = '', placeholder = '' }) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <label class="text-secondary text-sm">${escapeHtml(label)}</label>
      <input type="text" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" style="width:100%;margin-top:6px;" autofocus />
    `;
    const { footerEl, close } = openModal({ title, body: wrap });
    const input = wrap.querySelector('input');
    footerEl.innerHTML = `
      <button class="btn" data-act="cancel">キャンセル</button>
      <button class="btn btn-primary" data-act="ok">OK</button>
    `;
    footerEl.querySelector('[data-act="cancel"]').addEventListener('click', () => { close(); resolve(null); });
    footerEl.querySelector('[data-act="ok"]').addEventListener('click', () => { const v = input.value; close(); resolve(v); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { const v = input.value; close(); resolve(v); }
    });
    setTimeout(() => input.focus(), 50);
  });
}

/**
 * セット効果ポップオーバー（position: fixed）のグローバル配置ハンドラ。
 * 理由: absolute 配置だと親の overflow:auto や stacking context に隠れるため。
 */
function setupSetPopovers() {
  let activePop = null;
  const hide = () => {
    if (activePop) {
      activePop.classList.remove('is-visible');
      activePop = null;
    }
  };
  const position = (host, pop) => {
    const r = host.getBoundingClientRect();
    // 先に可視化してサイズを確定
    pop.style.top = '0px';
    pop.style.left = '0px';
    pop.classList.add('is-visible');
    const pr = pop.getBoundingClientRect();
    const margin = 6;
    let top = r.top - pr.height - margin;
    if (top < margin) top = r.bottom + margin;
    let left = r.left;
    const maxLeft = window.innerWidth - pr.width - margin;
    if (left > maxLeft) left = maxLeft;
    if (left < margin) left = margin;
    pop.style.top = `${Math.round(top)}px`;
    pop.style.left = `${Math.round(left)}px`;
  };
  document.addEventListener('mouseover', (e) => {
    const host = e.target.closest?.('.set-name.has-popover');
    if (!host) return;
    const pop = host.querySelector(':scope > .set-effect-popover');
    if (!pop) return;
    if (activePop && activePop !== pop) hide();
    activePop = pop;
    position(host, pop);
  });
  document.addEventListener('mouseout', (e) => {
    const host = e.target.closest?.('.set-name.has-popover');
    if (!host) return;
    const related = e.relatedTarget;
    if (related && host.contains(related)) return;
    hide();
  });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
}

window.addEventListener('hashchange', navigate);
document.addEventListener('DOMContentLoaded', () => {
  applyUiPrefs();
  setupSetPopovers();
  if (!location.hash) location.hash = '#/characters';
  navigate();
});
