/** Gallery page — 日別アコーディオン + 検索・絞り込み + 選択モード + 比較 + コレクション。
 *  URLクエリ（#/gallery?q=...&tags=a,b&workflow=foo&order=new&collection_id=3 等）に状態を反映。
 */
import { toast } from '../lib/toast.js';
import { GenerationAPI } from '../lib/generation_api.js';
import {
  esc, fmtDate, fmtTime, openLightbox, promptTags, stashSet, buildPromptBlock,
} from '../lib/common.js';
import { decomposePromptClient } from '../lib/decompose.js';

// ============================================================
// Constants / State
// ============================================================
const PAGE_SIZE = 60;
const LS_COLLAPSED = 'imggen:gallery:collapsed';
const LS_DENSITY = 'imggen:gallery:density';
const DENSITY_PX = { sm: 90, md: 140, lg: 200 };

// フィルタ/表示の全状態
const state = {
  items: [],
  offset: 0,
  hasMore: false,
  loading: false,
  scrollY: 0,
  highlightJobId: null,

  filters: {
    q: '',
    tags: [],
    favorite: false,
    workflow: null,
    collectionId: null,
    dateFrom: null,
    dateTo: null,
    order: 'new',
  },

  selectionMode: false,
  selected: new Set(),

  density: loadDensity(),
  collapsedDays: loadCollapsedDays(),

  availableTags: [],
  availableWorkflows: [],
  collections: [],
};

function $(id) { return document.getElementById(id); }

function loadCollapsedDays() {
  try {
    const raw = localStorage.getItem(LS_COLLAPSED);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}
function saveCollapsedDays() {
  try { localStorage.setItem(LS_COLLAPSED, JSON.stringify([...state.collapsedDays])); }
  catch { /* ignore */ }
}
function loadDensity() {
  try { return localStorage.getItem(LS_DENSITY) || 'md'; } catch { return 'md'; }
}
function saveDensity(d) {
  try { localStorage.setItem(LS_DENSITY, d); } catch { /* ignore */ }
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<section class="card imggen-section imggen-gallery-section">
  <div class="imggen-gallery-toolbar">
    <div class="imggen-gallery-toolbar-row">
      <input id="gal-q" class="form-input" type="search"
        placeholder="prompt / タグ で検索（スペース区切り AND）..."
        style="flex:1 1 200px;min-width:180px;font-size:0.8rem;">
      <select id="gal-order" class="form-input" title="並び順" style="font-size:0.75rem;width:auto;">
        <option value="new">新しい順</option>
        <option value="old">古い順</option>
        <option value="fav">⭐優先</option>
      </select>
      <select id="gal-date" class="form-input" title="期間" style="font-size:0.75rem;width:auto;">
        <option value="">期間: すべて</option>
        <option value="today">今日</option>
        <option value="7d">直近7日</option>
        <option value="30d">直近30日</option>
        <option value="custom">カスタム…</option>
      </select>
      <button id="gal-reload" class="btn btn-sm" title="再読込">⟳</button>
    </div>
    <div class="imggen-gallery-toolbar-row">
      <label class="imggen-fav-toggle">
        <input id="gal-favonly" type="checkbox"> ⭐のみ
      </label>
      <select id="gal-tag-add" class="form-input" title="タグで絞り込み (AND)" style="font-size:0.75rem;width:auto;">
        <option value="">＋タグ追加</option>
      </select>
      <select id="gal-workflow" class="form-input" title="ワークフロー" style="font-size:0.75rem;width:auto;">
        <option value="">ワークフロー: すべて</option>
      </select>
      <select id="gal-collection" class="form-input" title="コレクション" style="font-size:0.75rem;width:auto;">
        <option value="">コレクション: すべて</option>
      </select>
      <button id="gal-new-col" class="btn btn-sm" title="新規コレクション作成">📁+</button>
      <span class="imggen-gallery-density" title="表示サイズ">
        <button data-density="sm" class="btn btn-xs">小</button>
        <button data-density="md" class="btn btn-xs">中</button>
        <button data-density="lg" class="btn btn-xs">大</button>
      </span>
      <button id="gal-collapse-all" class="btn btn-sm" title="日付を全て閉じる">▸ 全閉</button>
      <button id="gal-expand-all" class="btn btn-sm" title="日付を全て開く">▾ 全開</button>
      <button id="gal-select-mode" class="btn btn-sm" title="選択モード">☑ 選択</button>
    </div>
    <div id="gal-active-filters" class="imggen-gallery-chips"></div>
    <div id="gal-date-range" class="imggen-gallery-date-range" hidden>
      <input id="gal-date-from" type="date" class="form-input" style="font-size:0.75rem;width:auto;">
      <span>〜</span>
      <input id="gal-date-to" type="date" class="form-input" style="font-size:0.75rem;width:auto;">
      <button id="gal-date-apply" class="btn btn-xs">適用</button>
    </div>
    <div id="gal-selection-bar" class="imggen-gallery-selbar" hidden>
      <span id="gal-sel-count">0 件選択</span>
      <button id="gal-sel-all" class="btn btn-xs">全選択</button>
      <button id="gal-sel-clear" class="btn btn-xs">解除</button>
      <button id="gal-sel-fav" class="btn btn-xs">⭐ 切替</button>
      <button id="gal-sel-tag" class="btn btn-xs">🏷 タグ追加</button>
      <button id="gal-sel-collect" class="btn btn-xs">📁 コレクションへ</button>
      <button id="gal-sel-compare" class="btn btn-xs">⇔ 比較</button>
      <button id="gal-sel-delete" class="btn btn-xs btn-danger">🗑 削除</button>
    </div>
  </div>
  <div id="gal-body" class="imggen-gallery-body">
    <div class="imggen-empty">Loading...</div>
  </div>
  <div id="gal-sentinel" class="imggen-gallery-sentinel" hidden></div>
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
  const entries = [...groups.entries()];
  if (state.filters.order === 'old') {
    entries.sort((a, b) => a[0].localeCompare(b[0]));
  } else {
    entries.sort((a, b) => b[0].localeCompare(a[0]));
  }
  return entries;
}

function renderGallery() {
  const el = $('gal-body');
  if (!el) return;
  if (!state.items.length) {
    el.innerHTML = state.loading
      ? '<div class="imggen-empty">Loading...</div>'
      : '<div class="imggen-empty">該当する画像がありません</div>';
    return;
  }
  const minPx = DENSITY_PX[state.density] || DENSITY_PX.md;
  const grouped = groupByDay(state.items);
  el.innerHTML = grouped.map(([day, list]) => {
    const collapsed = state.collapsedDays.has(day);
    const bodyHtml = collapsed ? '' : `
      <div class="imggen-gallery-grid" data-density="${state.density}" style="--gal-min:${minPx}px;">
        ${list.map(g => renderItem(g)).join('')}
      </div>
    `;
    return `
    <div class="imggen-gallery-day${collapsed ? ' collapsed' : ''}" data-day="${esc(day)}">
      <button class="imggen-gallery-day-head" data-act="toggle-day">
        <span class="imggen-gallery-day-chevron">▾</span>
        <span class="imggen-gallery-day-label">${esc(day)}</span>
        <span class="text-muted">(${list.length})</span>
      </button>
      ${bodyHtml}
    </div>
  `;
  }).join('');
  bindGalleryEvents(el);
}

function renderItem(g) {
  const idx = state.items.indexOf(g);
  const kind = g.kind || 'image';
  const hilit = (state.highlightJobId && g.job_id === state.highlightJobId)
    ? ' imggen-gallery-item--hilit' : '';
  const selected = state.selected.has(g.job_id);
  const selCls = selected ? ' imggen-gallery-item--selected' : '';
  const badge = kind !== 'image' ? `<span class="kind-badge">${esc(kind)}</span>` : '';
  const star = g.favorite ? `<span class="imggen-gallery-star" title="お気に入り">★</span>` : '';
  const checkbox = state.selectionMode
    ? `<span class="imggen-gallery-check">${selected ? '✔' : ''}</span>` : '';
  const thumb = kind === 'image'
    ? `<img loading="lazy" decoding="async" src="${esc(g.thumb_url || g.url)}" alt="">`
    : `<div class="imggen-gallery-kindph">${esc(kind)}</div>`;
  return `<div class="imggen-gallery-item${hilit}${selCls}" data-idx="${idx}" data-jobid="${esc(g.job_id)}">${thumb}${badge}${star}${checkbox}</div>`;
}

function bindGalleryEvents(el) {
  el.onclick = (e) => {
    const head = e.target.closest('[data-act="toggle-day"]');
    if (head) {
      const wrap = head.closest('.imggen-gallery-day');
      if (!wrap) return;
      const day = wrap.dataset.day;
      if (state.collapsedDays.has(day)) state.collapsedDays.delete(day);
      else state.collapsedDays.add(day);
      saveCollapsedDays();
      renderGallery();
      return;
    }
    const node = e.target.closest('[data-idx]');
    if (!node) return;
    const g = state.items[Number(node.dataset.idx)];
    if (!g) return;
    if (state.selectionMode) {
      toggleSelection(g.job_id);
      return;
    }
    openLightbox(g, {
      onReuse: handleReuse,
      onExtract: handleExtract,
      onFavoriteToggle: handleFavoriteToggle,
      onTagsEdit: handleTagsEdit,
      onDelete: handleDelete,
      onSimilar: handleSimilar,
      onAddToCollection: handleAddToCollection,
      onNavigate: (delta) => handleNavigate(g, delta),
    });
  };

  if (state.highlightJobId) {
    const hit = el.querySelector('.imggen-gallery-item--hilit');
    if (hit) hit.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function handleNavigate(fromItem, delta) {
  const items = state.items;
  if (!items.length) return null;
  let idx = items.indexOf(fromItem);
  if (idx < 0) return null;
  idx += delta;
  if (idx < 0 || idx >= items.length) return null;
  return items[idx];
}

// ============================================================
// Active filters chips
// ============================================================
function renderActiveFilters() {
  const box = $('gal-active-filters');
  if (!box) return;
  const chips = [];
  if (state.filters.q) {
    chips.push(chipHtml('q', `🔎 ${state.filters.q}`));
  }
  for (const t of state.filters.tags) {
    chips.push(chipHtml(`tag:${t}`, `🏷 ${t}`));
  }
  if (state.filters.favorite) chips.push(chipHtml('fav', '⭐のみ'));
  if (state.filters.workflow) chips.push(chipHtml('workflow', `⚙ ${state.filters.workflow}`));
  if (state.filters.collectionId) {
    const col = state.collections.find(c => String(c.id) === String(state.filters.collectionId));
    chips.push(chipHtml('collection', `📁 ${col?.name || state.filters.collectionId}`));
  }
  if (state.filters.dateFrom || state.filters.dateTo) {
    const f = state.filters.dateFrom || '…';
    const t = state.filters.dateTo || '…';
    chips.push(chipHtml('date', `📅 ${f} 〜 ${t}`));
  }
  box.innerHTML = chips.join('');
  box.onclick = (e) => {
    const btn = e.target.closest('[data-chip]');
    if (!btn) return;
    const key = btn.dataset.chip;
    if (key === 'q') { state.filters.q = ''; $('gal-q').value = ''; }
    else if (key.startsWith('tag:')) {
      const tag = key.slice(4);
      state.filters.tags = state.filters.tags.filter(t => t !== tag);
    }
    else if (key === 'fav') { state.filters.favorite = false; $('gal-favonly').checked = false; }
    else if (key === 'workflow') { state.filters.workflow = null; $('gal-workflow').value = ''; }
    else if (key === 'collection') { state.filters.collectionId = null; $('gal-collection').value = ''; }
    else if (key === 'date') {
      state.filters.dateFrom = null; state.filters.dateTo = null;
      $('gal-date').value = '';
      $('gal-date-range').hidden = true;
    }
    applyFilters();
  };
}

function chipHtml(key, label) {
  return `<span class="imggen-chip" data-chip="${esc(key)}">${esc(label)} <span class="imggen-chip-x">×</span></span>`;
}

function refreshTagSelect() {
  const sel = $('gal-tag-add');
  if (!sel) return;
  const added = new Set(state.filters.tags);
  sel.innerHTML = `<option value="">＋タグ追加</option>` +
    state.availableTags
      .filter(t => !added.has(t.tag))
      .map(t => `<option value="${esc(t.tag)}">${esc(t.tag)} (${t.count})</option>`)
      .join('');
}

function refreshWorkflowSelect() {
  const sel = $('gal-workflow');
  if (!sel) return;
  const cur = state.filters.workflow || '';
  sel.innerHTML = `<option value="">ワークフロー: すべて</option>` +
    state.availableWorkflows
      .map(w => `<option value="${esc(w.name)}">${esc(w.name)}</option>`)
      .join('');
  sel.value = cur;
}

function refreshCollectionSelect() {
  const sel = $('gal-collection');
  if (!sel) return;
  const cur = state.filters.collectionId || '';
  sel.innerHTML = `<option value="">コレクション: すべて</option>` +
    state.collections
      .map(c => `<option value="${c.id}">${esc(c.name)} (${c.item_count})</option>`)
      .join('');
  sel.value = cur;
}

// ============================================================
// URL sync
// ============================================================
function parseHashQuery() {
  const h = location.hash || '';
  const q = h.split('?')[1] || '';
  const p = new URLSearchParams(q);
  state.highlightJobId = p.get('job') || null;
  state.filters.q = p.get('q') || '';
  state.filters.tags = (p.get('tags') || '').split(',').map(s => s.trim()).filter(Boolean);
  state.filters.favorite = p.get('favorite') === '1';
  state.filters.workflow = p.get('workflow') || null;
  state.filters.collectionId = p.get('collection_id') ? Number(p.get('collection_id')) : null;
  state.filters.dateFrom = p.get('date_from') || null;
  state.filters.dateTo = p.get('date_to') || null;
  state.filters.order = p.get('order') || 'new';
}

function pushHashQuery() {
  const p = new URLSearchParams();
  const f = state.filters;
  if (f.q) p.set('q', f.q);
  if (f.tags.length) p.set('tags', f.tags.join(','));
  if (f.favorite) p.set('favorite', '1');
  if (f.workflow) p.set('workflow', f.workflow);
  if (f.collectionId) p.set('collection_id', String(f.collectionId));
  if (f.dateFrom) p.set('date_from', f.dateFrom);
  if (f.dateTo) p.set('date_to', f.dateTo);
  if (f.order && f.order !== 'new') p.set('order', f.order);
  const qs = p.toString();
  const newHash = '#/gallery' + (qs ? `?${qs}` : '');
  if (location.hash !== newHash) {
    history.replaceState(null, '', newHash);
  }
}

function reflectFiltersToUI() {
  $('gal-q') && ($('gal-q').value = state.filters.q);
  $('gal-order') && ($('gal-order').value = state.filters.order);
  $('gal-favonly') && ($('gal-favonly').checked = state.filters.favorite);
  if ($('gal-workflow')) $('gal-workflow').value = state.filters.workflow || '';
  if ($('gal-collection')) $('gal-collection').value = state.filters.collectionId || '';
  if (state.filters.dateFrom || state.filters.dateTo) {
    $('gal-date').value = 'custom';
    $('gal-date-range').hidden = false;
    if ($('gal-date-from')) $('gal-date-from').value = state.filters.dateFrom || '';
    if ($('gal-date-to')) $('gal-date-to').value = state.filters.dateTo || '';
  }
  document.querySelectorAll('.imggen-gallery-density [data-density]').forEach(btn => {
    btn.classList.toggle('btn-primary', btn.dataset.density === state.density);
  });
  renderActiveFilters();
}

// ============================================================
// Data loading
// ============================================================
async function loadPage({ reset = true } = {}) {
  if (state.loading) return;
  state.loading = true;
  if (reset) {
    state.offset = 0;
    state.items = [];
    $('gal-body').innerHTML = '<div class="imggen-empty">Loading...</div>';
  }
  try {
    const data = await GenerationAPI.gallery({
      limit: PAGE_SIZE, offset: state.offset,
      q: state.filters.q,
      tags: state.filters.tags,
      favorite: state.filters.favorite,
      workflow: state.filters.workflow,
      collectionId: state.filters.collectionId,
      dateFrom: state.filters.dateFrom,
      dateTo: state.filters.dateTo,
      order: state.filters.order,
      nsfw: !!window.IGNsfw?.isOn(),
    });
    const page = data?.items || [];
    state.items = reset ? page : [...state.items, ...page];
    state.offset = state.items.length;
    state.hasMore = !!data?.has_more && page.length > 0;
    const sentinel = $('gal-sentinel');
    if (sentinel) sentinel.hidden = !state.hasMore;
    renderGallery();
  } catch (err) {
    console.error('gallery load failed', err);
    if (reset) $('gal-body').innerHTML = '<div class="imggen-empty">取得失敗</div>';
    toast(`取得失敗: ${err?.message || err}`, 'error');
  } finally {
    state.loading = false;
  }
}

async function loadTags() {
  try {
    const data = await GenerationAPI.galleryTags();
    state.availableTags = data?.tags || [];
    refreshTagSelect();
  } catch (err) { console.error('tags load failed', err); }
}

async function loadWorkflows() {
  try {
    const data = await GenerationAPI.listWorkflows();
    state.availableWorkflows = data?.workflows || [];
    refreshWorkflowSelect();
  } catch (err) { console.error('workflows load failed', err); }
}

async function loadCollections() {
  try {
    const data = await GenerationAPI.listCollections();
    state.collections = data?.collections || [];
    refreshCollectionSelect();
  } catch (err) { console.error('collections load failed', err); }
}

function applyFilters() {
  pushHashQuery();
  renderActiveFilters();
  refreshTagSelect();
  loadPage({ reset: true });
}

// ============================================================
// Selection mode
// ============================================================
function setSelectionMode(on) {
  state.selectionMode = !!on;
  if (!on) state.selected.clear();
  $('gal-selection-bar').hidden = !on;
  $('gal-select-mode').classList.toggle('btn-primary', on);
  $('gal-select-mode').textContent = on ? '☑ 選択中' : '☑ 選択';
  updateSelectionCount();
  renderGallery();
}

function toggleSelection(jobId) {
  if (state.selected.has(jobId)) state.selected.delete(jobId);
  else state.selected.add(jobId);
  updateSelectionCount();
  // ミニアップデート: 該当ノードだけトグル
  const nodes = document.querySelectorAll(`[data-jobid="${CSS.escape(jobId)}"]`);
  nodes.forEach(n => {
    n.classList.toggle('imggen-gallery-item--selected', state.selected.has(jobId));
    const c = n.querySelector('.imggen-gallery-check');
    if (c) c.textContent = state.selected.has(jobId) ? '✔' : '';
  });
}

function updateSelectionCount() {
  const el = $('gal-sel-count');
  if (el) el.textContent = `${state.selected.size} 件選択`;
  const canCompare = state.selected.size === 2;
  $('gal-sel-compare').disabled = !canCompare;
  const any = state.selected.size > 0;
  ['gal-sel-fav', 'gal-sel-tag', 'gal-sel-collect', 'gal-sel-delete'].forEach(id => {
    const b = $(id); if (b) b.disabled = !any;
  });
}

// ============================================================
// Actions (single)
// ============================================================
async function handleReuse(item) {
  try {
    const jobId = item.job_id;
    if (!jobId) { toast('job_id がありません', 'error'); return; }
    const [job, secs] = await Promise.all([
      GenerationAPI.getJob(jobId),
      GenerationAPI.listSections(),
    ]);
    if (!job) { toast('ジョブが見つかりません', 'error'); return; }
    const allSections = secs?.sections || [];
    const decomp = decomposePromptClient({
      positive: job.positive || '',
      negative: job.negative || '',
      sections: allSections,
    });
    stashSet({
      source: 'gallery',
      job_id: jobId,
      workflow_name: job.workflow_name,
      positive: decomp.userPositive,
      negative: decomp.userNegative,
      section_ids: decomp.section_ids,
      params: job.params || {},
      modality: job.modality || 'image',
    });
    location.hash = '#/generate?prefill=gallery';
    const n = decomp.section_ids.length;
    toast(n ? `生成フォームに取り込みました（セクション ${n} 件復元）` : '生成フォームに取り込みました', 'info');
  } catch (err) {
    console.error('reuse failed', err);
    toast('取り込み失敗', 'error');
  }
}

function handleExtract(item) {
  const url = item.url || item.thumb_url;
  if (!url) { toast('画像 URL がありません', 'error'); return; }
  const fname = (() => {
    try {
      const p = new URL(url, location.origin).searchParams.get('path') || '';
      const m = p.split(/[\\/]/).pop();
      return m || `${item.job_id || 'image'}.png`;
    } catch { return `${item.job_id || 'image'}.png`; }
  })();
  stashSet({
    type: 'extract-from-url',
    source: 'gallery',
    url, name: fname,
    job_id: item.job_id || null,
  });
  location.hash = '#/extract';
  toast('Extract ページへ送りました', 'info');
}

async function handleFavoriteToggle(item, next) {
  if (!item.job_id) { toast('job_id がありません', 'error'); return false; }
  try {
    await GenerationAPI.setJobFavorite(item.job_id, next);
    for (const it of state.items) {
      if (it.job_id === item.job_id) it.favorite = next;
    }
    renderGallery();
    return true;
  } catch (err) {
    toast(`お気に入り更新失敗: ${err?.message || err}`, 'error');
    return false;
  }
}

async function handleTagsEdit(item, applyTags) {
  if (!item.job_id) { toast('job_id がありません', 'error'); return; }
  const next = promptTags(item.tags || []);
  if (next === null) return;
  try {
    const res = await GenerationAPI.setJobTags(item.job_id, next);
    const tags = res?.tags || next;
    for (const it of state.items) {
      if (it.job_id === item.job_id) it.tags = tags;
    }
    applyTags(tags);
    renderGallery();
    loadTags();
  } catch (err) {
    toast(`タグ更新失敗: ${err?.message || err}`, 'error');
  }
}

async function handleDelete(item) {
  if (!item.job_id) return false;
  if (!confirm(`このジョブを削除します（NAS上のファイルも削除）。よろしいですか？`)) {
    return false;
  }
  try {
    const res = await GenerationAPI.deleteJob(item.job_id);
    state.items = state.items.filter(it => it.job_id !== item.job_id);
    renderGallery();
    toast(`削除しました（ファイル ${res?.removed_files || 0} 件）`, 'info');
    return true;
  } catch (err) {
    toast(`削除失敗: ${err?.message || err}`, 'error');
    return false;
  }
}

async function handleSimilar(item) {
  if (!item.job_id) return;
  try {
    const res = await GenerationAPI.gallerySimilar(item.job_id, 24);
    openSimilarModal(item, res?.items || []);
  } catch (err) {
    toast(`類似検索失敗: ${err?.message || err}`, 'error');
  }
}

async function handleAddToCollection(item) {
  if (!item.job_id) return;
  const id = await pickCollectionDialog();
  if (!id) return;
  try {
    await GenerationAPI.addJobsToCollection(id, [item.job_id]);
    toast(`コレクションに追加しました`, 'info');
    loadCollections();
  } catch (err) {
    toast(`追加失敗: ${err?.message || err}`, 'error');
  }
}

// ============================================================
// Bulk actions
// ============================================================
async function bulkFavorite() {
  if (!state.selected.size) return;
  const favorite = confirm(`${state.selected.size} 件をお気に入りにしますか？\n（キャンセルで解除）`);
  try {
    const res = await GenerationAPI.bulkFavorite([...state.selected], favorite);
    toast(`${res?.updated || 0} 件 更新`, 'info');
    for (const it of state.items) {
      if (state.selected.has(it.job_id)) it.favorite = favorite;
    }
    renderGallery();
  } catch (err) {
    toast(`更新失敗: ${err?.message || err}`, 'error');
  }
}

async function bulkTag() {
  if (!state.selected.size) return;
  const raw = prompt('追加するタグをカンマ区切りで（先頭に - を付けると除去）', '');
  if (raw === null) return;
  const add = [], remove = [];
  for (const t of raw.split(',').map(s => s.trim()).filter(Boolean)) {
    if (t.startsWith('-')) remove.push(t.slice(1));
    else add.push(t);
  }
  try {
    if (add.length) await GenerationAPI.bulkTags([...state.selected], add, 'add');
    if (remove.length) await GenerationAPI.bulkTags([...state.selected], remove, 'remove');
    toast(`タグ更新: +${add.length} / -${remove.length}`, 'info');
    loadPage({ reset: true });
    loadTags();
  } catch (err) {
    toast(`更新失敗: ${err?.message || err}`, 'error');
  }
}

async function bulkDelete() {
  const n = state.selected.size;
  if (!n) return;
  if (!confirm(`${n} 件を削除します（NAS上のファイルも削除）。よろしいですか？`)) return;
  try {
    const res = await GenerationAPI.bulkDelete([...state.selected]);
    toast(`削除: ${res?.deleted || 0} 件 / ファイル ${res?.removed_files || 0}`, 'info');
    state.selected.clear();
    updateSelectionCount();
    loadPage({ reset: true });
  } catch (err) {
    toast(`削除失敗: ${err?.message || err}`, 'error');
  }
}

async function bulkAddToCollection() {
  if (!state.selected.size) return;
  const id = await pickCollectionDialog();
  if (!id) return;
  try {
    const res = await GenerationAPI.addJobsToCollection(id, [...state.selected]);
    toast(`${res?.added || 0} 件 追加`, 'info');
    loadCollections();
  } catch (err) {
    toast(`追加失敗: ${err?.message || err}`, 'error');
  }
}

// ============================================================
// Compare mode
// ============================================================
async function openCompare() {
  if (state.selected.size !== 2) {
    toast('比較には 2 枚を選択してください', 'error');
    return;
  }
  const ids = [...state.selected];
  const items = state.items.filter(it => ids.includes(it.job_id));
  if (items.length < 2) {
    toast('選択された画像がギャラリーに見つかりません', 'error');
    return;
  }
  const [a, b] = items;
  const [jobA, jobB] = await Promise.all([
    GenerationAPI.getJob(a.job_id).catch(() => null),
    GenerationAPI.getJob(b.job_id).catch(() => null),
  ]);
  const overlay = document.createElement('div');
  overlay.className = 'imggen-compare-overlay';
  overlay.innerHTML = `
    <div class="imggen-compare-card">
      <div class="imggen-compare-head">
        <span>⇔ 画像比較</span>
        <button data-act="close" class="btn btn-sm">×</button>
      </div>
      <div class="imggen-compare-body">
        ${[a, b].map((it, i) => `
          <div class="imggen-compare-pane">
            <img src="${esc(it.preview_url || it.url)}" alt="">
            <div class="imggen-compare-meta">
              <div class="text-muted">${esc(fmtTime(it.created_at))}</div>
              <div>${esc(it.job_id)}</div>
            </div>
          </div>
        `).join('')}
      </div>
      <div class="imggen-compare-prompts" id="imggen-compare-prompts"></div>
      <div class="imggen-compare-params" id="imggen-compare-params"></div>
    </div>
  `;
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay || e.target.closest('[data-act="close"]')) overlay.remove();
  });
  document.addEventListener('keydown', function onKey(e) {
    if (e.key === 'Escape') {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    }
  });
  document.body.appendChild(overlay);
  // プロンプト並列表示
  const promptsBox = overlay.querySelector('#imggen-compare-prompts');
  const pairs = [
    ['POSITIVE', jobA?.positive || a.positive, jobB?.positive || b.positive],
    ['NEGATIVE', jobA?.negative || a.negative, jobB?.negative || b.negative],
  ];
  const blocks = [];
  for (const [label, ta, tb] of pairs) {
    const bA = buildPromptBlock(`A: ${label}`, ta || '');
    const bB = buildPromptBlock(`B: ${label}`, tb || '');
    blocks.push({ bA, bB });
    promptsBox.insertAdjacentHTML('beforeend', `
      <div class="imggen-compare-prompt-pair">
        <div>${bA.html}</div>
        <div>${bB.html}</div>
      </div>
    `);
  }
  blocks.forEach(({ bA, bB }) => { bA.bind(promptsBox); bB.bind(promptsBox); });
  // params diff
  const pbox = overlay.querySelector('#imggen-compare-params');
  const pa = jobA?.params || {};
  const pb = jobB?.params || {};
  const keys = [...new Set([...Object.keys(pa), ...Object.keys(pb)])].sort();
  if (keys.length) {
    pbox.innerHTML = `<table class="imggen-compare-table">
      <thead><tr><th>key</th><th>A</th><th>B</th></tr></thead>
      <tbody>${keys.map(k => {
        const va = JSON.stringify(pa[k]);
        const vb = JSON.stringify(pb[k]);
        const diff = va !== vb;
        return `<tr class="${diff ? 'diff' : ''}"><td>${esc(k)}</td><td>${esc(va ?? '')}</td><td>${esc(vb ?? '')}</td></tr>`;
      }).join('')}</tbody>
    </table>`;
  }
}

// ============================================================
// Similar modal
// ============================================================
function openSimilarModal(base, items) {
  const overlay = document.createElement('div');
  overlay.className = 'imggen-similar-overlay';
  overlay.innerHTML = `
    <div class="imggen-similar-card">
      <div class="imggen-similar-head">
        <span>🔍 類似プロンプト: ${items.length} 件</span>
        <button data-act="close" class="btn btn-sm">×</button>
      </div>
      <div class="imggen-similar-grid">
        ${items.map(it => `
          <a class="imggen-similar-item" data-jobid="${esc(it.job_id)}" title="score ${it.score}">
            <img loading="lazy" src="${esc(it.thumb_url)}" alt="">
            <span class="imggen-similar-score">${(it.score * 100).toFixed(0)}%</span>
          </a>
        `).join('') || '<div class="imggen-empty">類似なし</div>'}
      </div>
    </div>
  `;
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay || e.target.closest('[data-act="close"]')) { overlay.remove(); return; }
    const node = e.target.closest('[data-jobid]');
    if (node) {
      location.hash = `#/gallery?job=${encodeURIComponent(node.dataset.jobid)}`;
      overlay.remove();
    }
  });
  document.body.appendChild(overlay);
}

// ============================================================
// Collection picker dialog
// ============================================================
function pickCollectionDialog() {
  return new Promise(async (resolve) => {
    await loadCollections();
    const overlay = document.createElement('div');
    overlay.className = 'imggen-pick-overlay';
    overlay.innerHTML = `
      <div class="imggen-pick-card">
        <div class="imggen-pick-head">
          <span>📁 コレクションを選択</span>
          <button data-act="close" class="btn btn-sm">×</button>
        </div>
        <div class="imggen-pick-list">
          ${state.collections.map(c => `
            <button class="imggen-pick-item" data-cid="${c.id}">
              ${esc(c.name)} <span class="text-muted">(${c.item_count})</span>
            </button>
          `).join('') || '<div class="imggen-empty">コレクションがありません</div>'}
        </div>
        <div class="imggen-pick-new">
          <input type="text" class="form-input" placeholder="新規コレクション名..." id="imggen-pick-newname" style="flex:1;font-size:0.8rem;">
          <button class="btn btn-sm btn-primary" data-act="create">作成して追加</button>
        </div>
      </div>
    `;
    overlay.addEventListener('click', async (e) => {
      if (e.target === overlay || e.target.closest('[data-act="close"]')) {
        overlay.remove(); resolve(null); return;
      }
      const item = e.target.closest('[data-cid]');
      if (item) { overlay.remove(); resolve(Number(item.dataset.cid)); return; }
      if (e.target.closest('[data-act="create"]')) {
        const name = overlay.querySelector('#imggen-pick-newname').value.trim();
        if (!name) { toast('名前を入力', 'error'); return; }
        try {
          const res = await GenerationAPI.createCollection({ name });
          overlay.remove();
          resolve(res?.id || null);
        } catch (err) {
          toast(`作成失敗: ${err?.message || err}`, 'error');
        }
      }
    });
    document.body.appendChild(overlay);
  });
}

async function createCollectionPrompt() {
  const name = prompt('新規コレクション名');
  if (!name) return;
  try {
    await GenerationAPI.createCollection({ name: name.trim() });
    await loadCollections();
    toast('コレクションを作成しました', 'info');
  } catch (err) {
    toast(`作成失敗: ${err?.message || err}`, 'error');
  }
}

// ============================================================
// Date filter
// ============================================================
function applyDatePreset(v) {
  const range = $('gal-date-range');
  if (v === 'custom') {
    range.hidden = false;
    return;
  }
  range.hidden = true;
  const today = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  let from = null, to = null;
  if (v === 'today') { from = to = fmt(today); }
  else if (v === '7d') {
    const d = new Date(); d.setDate(d.getDate() - 6);
    from = fmt(d); to = fmt(today);
  }
  else if (v === '30d') {
    const d = new Date(); d.setDate(d.getDate() - 29);
    from = fmt(d); to = fmt(today);
  }
  state.filters.dateFrom = from;
  state.filters.dateTo = to;
  applyFilters();
}

// ============================================================
// Mount
// ============================================================
let intersectionObserver = null;

export async function mount() {
  parseHashQuery();
  reflectFiltersToUI();

  $('gal-reload')?.addEventListener('click', () => {
    loadPage({ reset: true }); loadTags(); loadWorkflows(); loadCollections();
  });

  // --- filters ---
  const q = $('gal-q');
  if (q) {
    let debounce = null;
    q.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        state.filters.q = q.value.trim();
        applyFilters();
      }, 300);
    });
  }
  $('gal-order')?.addEventListener('change', (e) => {
    state.filters.order = e.target.value;
    applyFilters();
  });
  $('gal-favonly')?.addEventListener('change', (e) => {
    state.filters.favorite = !!e.target.checked;
    applyFilters();
  });
  $('gal-tag-add')?.addEventListener('change', (e) => {
    const v = e.target.value;
    if (v && !state.filters.tags.includes(v)) {
      state.filters.tags.push(v);
      applyFilters();
    }
    e.target.value = '';
  });
  $('gal-workflow')?.addEventListener('change', (e) => {
    state.filters.workflow = e.target.value || null;
    applyFilters();
  });
  $('gal-collection')?.addEventListener('change', (e) => {
    state.filters.collectionId = e.target.value ? Number(e.target.value) : null;
    applyFilters();
  });
  $('gal-new-col')?.addEventListener('click', createCollectionPrompt);

  $('gal-date')?.addEventListener('change', (e) => applyDatePreset(e.target.value));
  $('gal-date-apply')?.addEventListener('click', () => {
    state.filters.dateFrom = $('gal-date-from').value || null;
    state.filters.dateTo = $('gal-date-to').value || null;
    applyFilters();
  });

  // --- density ---
  document.querySelectorAll('[data-density]').forEach(btn => {
    btn.addEventListener('click', () => {
      state.density = btn.dataset.density;
      saveDensity(state.density);
      reflectFiltersToUI();
      renderGallery();
    });
  });

  // --- collapse all ---
  $('gal-collapse-all')?.addEventListener('click', () => {
    const grouped = groupByDay(state.items);
    state.collapsedDays = new Set(grouped.map(([d]) => d));
    saveCollapsedDays();
    renderGallery();
  });
  $('gal-expand-all')?.addEventListener('click', () => {
    state.collapsedDays.clear();
    saveCollapsedDays();
    renderGallery();
  });

  // --- selection mode ---
  $('gal-select-mode')?.addEventListener('click', () => setSelectionMode(!state.selectionMode));
  $('gal-sel-all')?.addEventListener('click', () => {
    state.items.forEach(it => state.selected.add(it.job_id));
    updateSelectionCount();
    renderGallery();
  });
  $('gal-sel-clear')?.addEventListener('click', () => {
    state.selected.clear();
    updateSelectionCount();
    renderGallery();
  });
  $('gal-sel-fav')?.addEventListener('click', bulkFavorite);
  $('gal-sel-tag')?.addEventListener('click', bulkTag);
  $('gal-sel-delete')?.addEventListener('click', bulkDelete);
  $('gal-sel-collect')?.addEventListener('click', bulkAddToCollection);
  $('gal-sel-compare')?.addEventListener('click', openCompare);

  // --- infinite scroll ---
  const sentinel = $('gal-sentinel');
  if (sentinel && 'IntersectionObserver' in window) {
    const root = document.getElementById('main-content') || null;
    intersectionObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && state.hasMore && !state.loading) {
          loadPage({ reset: false });
        }
      }
    }, { root, rootMargin: '400px' });
    intersectionObserver.observe(sentinel);
  }

  // --- NSFW change ---
  window.addEventListener('ig:nsfw-change', () => {
    loadPage({ reset: true });
    loadTags();
  });

  // --- scroll position save ---
  window.addEventListener('beforeunload', saveScroll);

  await Promise.all([
    loadPage({ reset: true }),
    loadTags(),
    loadWorkflows(),
    loadCollections(),
  ]);

  // 復元: scrollY
  restoreScroll();
}

function scrollContainer() {
  return document.getElementById('main-content') || document.scrollingElement || document.documentElement;
}
function saveScroll() {
  try {
    const el = scrollContainer();
    sessionStorage.setItem('imggen:gallery:scrollY', String(el?.scrollTop || 0));
  } catch { /* ignore */ }
}
function restoreScroll() {
  try {
    const raw = sessionStorage.getItem('imggen:gallery:scrollY');
    if (!raw) return;
    const y = Number(raw);
    if (isNaN(y)) return;
    setTimeout(() => {
      const el = scrollContainer();
      if (el) el.scrollTop = y;
    }, 50);
  } catch { /* ignore */ }
}

export function onShow(rawHash) {
  const prevJob = state.highlightJobId;
  const prevFilterKey = JSON.stringify(state.filters);
  parseHashQuery();
  reflectFiltersToUI();
  if (JSON.stringify(state.filters) !== prevFilterKey) {
    loadPage({ reset: true });
    return;
  }
  if (state.highlightJobId && state.highlightJobId !== prevJob) {
    renderGallery();
  }
}

export function onHide() {
  saveScroll();
}
