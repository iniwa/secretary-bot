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
import * as wildcards from './pages/wildcards.js';
import * as lora from './pages/lora.js';
import * as reference from './pages/reference.js';
import * as referenceChenkin from './pages/reference_chenkin.js';
import * as referenceAsuma from './pages/reference_asuma.js';
import { toast } from './lib/toast.js';

const routes = [
  { hash: '#/generate',          module: generate,         nav: 'generate',          title: 'Generate' },
  { hash: '#/jobs',              module: jobs,             nav: 'jobs',              title: 'Jobs' },
  { hash: '#/gallery',           module: gallery,          nav: 'gallery',           title: 'Gallery' },
  { hash: '#/prompts',           module: prompts,          nav: 'prompts',           title: 'Prompts' },
  { hash: '#/extract',           module: extract,          nav: 'extract',           title: 'Extract' },
  { hash: '#/wildcards',         module: wildcards,        nav: 'wildcards',         title: 'Wildcards' },
  { hash: '#/lora',              module: lora,             nav: 'lora',              title: 'LoRA' },
  { hash: '#/reference',         module: reference,        nav: 'reference',         title: 'プリセット参考（共通）' },
  { hash: '#/reference/chenkin', module: referenceChenkin, nav: 'reference-chenkin', title: '参考 / ChenkinNoob-XL' },
  { hash: '#/reference/asuma',   module: referenceAsuma,   nav: 'reference-asuma',   title: '参考 / AsumaXL' },
];

const DEFAULT_HASH = '#/generate';

const containers = {};   // nav -> { el, module }
let currentNav = null;
let _navChain = Promise.resolve();   // 直列実行用 mutex

function resolve(rawHash) {
  const base = (rawHash || '').split('?')[0];
  return routes.find(r => r.hash === base) || null;
}

/** すべての navigate をチェーン化して直列実行する。
 *  これにより mount() の await 中に別の navigate が割り込んで
 *  複数のページコンテナが同時表示されてしまう race を防ぐ。
 */
function navigate() {
  _navChain = _navChain.then(_navigateImpl).catch((err) => {
    console.error('navigate failed', err);
  });
  return _navChain;
}

async function _navigateImpl() {
  const rawHash = location.hash || DEFAULT_HASH;
  const route = resolve(rawHash);
  if (!route) {
    location.hash = DEFAULT_HASH;
    return;
  }

  // ナビ ハイライト更新
  document.querySelectorAll('.ig-nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === route.nav);
  });

  // 対象以外のすべてのページを必ず hide（currentNav に頼らず safety net）
  for (const [nav, entry] of Object.entries(containers)) {
    if (nav === route.nav) continue;
    if (entry.el.style.display !== 'none') {
      entry.el.style.display = 'none';
      try { entry.module.onHide?.(); } catch (err) { console.error('onHide failed', err); }
    }
  }

  // 既存コンテナがあれば表示するだけ。なければ初回マウント
  let entry = containers[route.nav];
  const main = document.getElementById('main-content');

  if (!entry) {
    const el = document.createElement('div');
    el.className = 'ig-page-container';
    el.dataset.page = route.nav;
    // race 対策で先に containers へ登録（同期実行内で完結）
    entry = { el, module: route.module };
    containers[route.nav] = entry;
    try {
      el.innerHTML = route.module.render();
      main.appendChild(el);
      if (route.module.mount) await route.module.mount();
    } catch (err) {
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
// NSFW hidden mode — バージョン表示ダブルクリックでトグル
// ============================================================
const NSFW_LS_KEY = 'ig:nsfw';

function isNsfwOn() {
  try { return localStorage.getItem(NSFW_LS_KEY) === '1'; }
  catch { return false; }
}

function applyNsfwAttr(on) {
  if (on) document.body.setAttribute('data-nsfw', 'on');
  else document.body.removeAttribute('data-nsfw');
}

function setNsfwMode(on) {
  try { localStorage.setItem(NSFW_LS_KEY, on ? '1' : '0'); }
  catch { /* ignore */ }
  applyNsfwAttr(on);
  // 既存ページに状態変化を通知（gallery などが再読込する）
  window.dispatchEvent(new CustomEvent('ig:nsfw-change', { detail: { on } }));
}

// 他モジュールから参照できるようにグローバルへエクスポート
window.IGNsfw = {
  isOn: isNsfwOn,
  set: setNsfwMode,
};

function setupNsfwToggle() {
  const el = document.getElementById('ig-topbar-status');
  if (!el) return;
  // ダブルクリックで切替
  el.addEventListener('dblclick', (e) => {
    e.preventDefault();
    const next = !isNsfwOn();
    setNsfwMode(next);
    toast(next ? 'NSFW mode ON' : 'NSFW mode OFF', 'info');
  });
}

// ============================================================
// モバイル用サイドバー drawer 制御
// ============================================================
function setupMobileDrawer() {
  const btn = document.getElementById('ig-menu-toggle');
  const sidebar = document.getElementById('ig-sidebar');
  const backdrop = document.getElementById('ig-sidebar-backdrop');
  if (!btn || !sidebar || !backdrop) return;

  const open = () => {
    sidebar.classList.add('open');
    backdrop.classList.add('open');
  };
  const close = () => {
    sidebar.classList.remove('open');
    backdrop.classList.remove('open');
  };
  const toggle = () => {
    if (sidebar.classList.contains('open')) close();
    else open();
  };

  btn.addEventListener('click', toggle);
  backdrop.addEventListener('click', close);
  // ナビ項目タップで自動で閉じる（モバイル only 動作だが PC でも害なし）
  sidebar.addEventListener('click', (e) => {
    if (e.target.closest('.ig-nav-item')) close();
  });
  // hashchange でも閉じる（「← Bot」以外の遷移保険）
  window.addEventListener('hashchange', close);
}

// ============================================================
// Init
// ============================================================
window.addEventListener('hashchange', navigate);
document.addEventListener('DOMContentLoaded', () => {
  if (!location.hash) location.hash = DEFAULT_HASH;
  // NSFW モードを DOM 確定直後に body に反映しておく
  applyNsfwAttr(isNsfwOn());
  setupNsfwToggle();
  setupMobileDrawer();
  navigate();
  refreshTopbarStatus();
  setInterval(refreshTopbarStatus, 60000);
});
