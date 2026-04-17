/** Gallery page — 日別グループ + lightbox + 「この設定で再現」。 */
import { toast } from '../app.js';
import { GenerationAPI } from '../lib/generation_api.js';
import {
  esc, fmtDate, openLightbox, stashSet,
} from '../lib/common.js';

// ============================================================
// State
// ============================================================
let items = [];
let allItems = [];
let offset = 0;
const PAGE_SIZE = 60;
let highlightJobId = null;  // URL ?job=<id> から

function $(id) { return document.getElementById(id); }

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<section class="card imggen-section">
  <div class="imggen-header">
    <h3>Gallery</h3>
    <div style="display:flex;gap:0.4rem;align-items:center;">
      <input id="gal-filter" class="form-input" type="search" placeholder="prompt で検索..." style="width:200px;font-size:0.75rem;padding:0.2rem 0.4rem;">
      <button id="gal-reload" class="btn btn-sm">再読込</button>
      <button id="gal-more" class="btn btn-sm btn-primary">もっと読む</button>
    </div>
  </div>
  <div id="gal-body">
    <div class="imggen-empty">Loading...</div>
  </div>
</section>
`;
}

function groupByDay(list) {
  const groups = new Map();
  for (const it of list) {
    const key = it.created_at ? fmtDate(it.created_at) : '---';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(it);
  }
  return [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));
}

function renderGallery() {
  const el = $('gal-body');
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div class="imggen-empty">No images yet</div>';
    return;
  }
  const grouped = groupByDay(items);
  el.innerHTML = grouped.map(([day, list]) => `
    <div class="imggen-gallery-day">
      <div class="imggen-gallery-day-head">${esc(day)} <span class="text-muted">(${list.length})</span></div>
      <div class="imggen-gallery-grid">
        ${list.map(g => {
          const idx = items.indexOf(g);
          const kind = g.kind || 'image';
          const hilit = (highlightJobId && g.job_id === highlightJobId) ? 'style="outline:2px solid var(--accent);"' : '';
          const badge = kind !== 'image' ? `<span class="kind-badge">${esc(kind)}</span>` : '';
          const thumb = kind === 'image'
            ? `<img loading="lazy" src="${esc(g.thumb_url || g.url)}" alt="">`
            : `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:0.7rem;color:var(--text-secondary);">${esc(kind)}</div>`;
          return `<div class="imggen-gallery-item" data-idx="${idx}" ${hilit}>${thumb}${badge}</div>`;
        }).join('')}
      </div>
    </div>
  `).join('');
  el.onclick = (e) => {
    const node = e.target.closest('[data-idx]');
    if (!node) return;
    const g = items[Number(node.dataset.idx)];
    if (!g) return;
    openLightbox(g, { onReuse: handleReuse });
  };

  if (highlightJobId) {
    const hit = el.querySelector('[style*="outline"]');
    if (hit) hit.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

// ============================================================
// Data
// ============================================================
async function loadGallery({ reset = true } = {}) {
  if (reset) {
    offset = 0;
    allItems = [];
  }
  try {
    const data = await GenerationAPI.gallery({ limit: PAGE_SIZE, offset });
    const page = data?.items || [];
    allItems = reset ? page : [...allItems, ...page];
    offset = allItems.length;
    applyFilter();
  } catch (err) {
    console.error('gallery load failed', err);
    const el = $('gal-body');
    if (el) el.innerHTML = '<div class="imggen-empty">取得失敗</div>';
  }
}

function applyFilter() {
  const q = ($('gal-filter')?.value || '').trim().toLowerCase();
  if (!q) {
    items = [...allItems];
  } else {
    items = allItems.filter(it => {
      const hay = `${it.positive || ''} ${it.negative || ''}`.toLowerCase();
      return hay.includes(q);
    });
  }
  renderGallery();
}

// ============================================================
// Reuse
// ============================================================
async function handleReuse(item) {
  try {
    const jobId = item.job_id;
    if (!jobId) { toast('job_id がありません', 'error'); return; }
    const job = await GenerationAPI.getJob(jobId);
    if (!job) { toast('ジョブが見つかりません', 'error'); return; }
    stashSet({
      source: 'gallery',
      job_id: jobId,
      workflow_name: job.workflow_name,
      positive: job.positive,
      negative: job.negative,
      params: job.params || {},
      modality: job.modality || 'image',
    });
    location.hash = '#/generate?prefill=gallery';
    toast('生成フォームに取り込みました', 'info');
  } catch (err) {
    console.error('reuse failed', err);
    toast('取り込み失敗', 'error');
  }
}

// ============================================================
// Mount / Show / Hide
// ============================================================
function parseQuery(rawHash) {
  const h = rawHash || location.hash || '';
  const q = h.split('?')[1] || '';
  const params = new URLSearchParams(q);
  highlightJobId = params.get('job') || null;
}

export async function mount() {
  $('gal-reload')?.addEventListener('click', () => loadGallery({ reset: true }));
  $('gal-more')?.addEventListener('click', () => loadGallery({ reset: false }));
  const filter = $('gal-filter');
  if (filter) {
    let debounce = null;
    filter.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(applyFilter, 200);
    });
  }
  await loadGallery({ reset: true });
}

export function onShow(rawHash) {
  // ?job=... 付きで遷移してきたら、その都度ハイライトを更新
  const prev = highlightJobId;
  parseQuery(rawHash);
  if (highlightJobId && highlightJobId !== prev) renderGallery();
}
