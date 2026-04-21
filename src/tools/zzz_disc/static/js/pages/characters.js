/** キャラ一覧グリッド（検索 + 属性 / 陣営 / 推奨メインステ フィルタ + ソート） */
import { api } from '../api.js';
import { escapeHtml } from '../app.js';
import { elementLabel } from '../labels.js';

const SORT_OPTIONS = [
  { value: 'display_order', label: '既定順' },
  { value: 'name_ja', label: '名前 (あ→ん)' },
  { value: 'name_ja_desc', label: '名前 (ん→あ)' },
  { value: 'element', label: '属性' },
  { value: 'faction', label: '陣営' },
  { value: 'has_current', label: '現在ビルド有→無' },
  { value: 'preset_count', label: 'プリセット数 多→少' },
];

const MAIN_STAT_FILTER_SLOTS = [
  { key: '4', label: '4番',
    candidates: ['HP%', '攻撃力%', '防御力%', '会心率%', '会心ダメージ%', '異常マスタリー'] },
  { key: '5', label: '5番',
    candidates: ['HP%', '攻撃力%', '防御力%', '貫通率%',
                 '物理属性ダメージ%', '炎属性ダメージ%', '氷属性ダメージ%',
                 '電気属性ダメージ%', 'エーテル属性ダメージ%'] },
  { key: '6', label: '6番',
    candidates: ['HP%', '攻撃力%', '防御力%', '異常掌握',
                 '衝撃力%', 'エネルギー自動回復%'] },
];

const ELEMENT_OPTIONS = ['物理', '炎', '氷', '電気', 'エーテル', '霜'];
const BUILD_STATUS_OPTIONS = [
  { value: 'all', label: 'すべて' },
  { value: 'has_current', label: '現在ビルド有' },
  { value: 'no_current', label: '現在ビルド無' },
  { value: 'has_preset', label: 'プリセット有' },
  { value: 'no_preset', label: 'プリセット無' },
];
const BUILD_STATUS_LABEL = Object.fromEntries(BUILD_STATUS_OPTIONS.map(o => [o.value, o.label]));

let state = {
  chars: [],
  sort: 'display_order',
  searchText: '',
  elementFilter: new Set(),
  factionFilter: new Set(),
  buildStatus: 'all',
  mainStatFilter: { '4': new Set(), '5': new Set(), '6': new Set() },
  panelOpen: false,
};

export function render() {
  const elementChipsHtml = ELEMENT_OPTIONS.map(n => `
    <label class="rec-sub-chip element-chip element-${elementKey(n)}" data-filter="element">
      <input type="checkbox" data-filter="element" data-val="${escapeHtml(n)}" />
      <span>${escapeHtml(n)}</span>
    </label>
  `).join('');
  const slotRowsHtml = MAIN_STAT_FILTER_SLOTS.map(({ key, label, candidates }) => `
    <div class="filter-slot-row" data-slot="${key}">
      <div class="filter-slot-label">${escapeHtml(label)}</div>
      <div class="rec-sub-chips">
        ${candidates.map(n => `
          <label class="rec-sub-chip">
            <input type="checkbox" data-slot="${key}" data-val="${escapeHtml(n)}" />
            <span>${escapeHtml(n)}</span>
          </label>
        `).join('')}
      </div>
    </div>
  `).join('');
  return `
    <div class="page-header">
      <h2>👥 キャラ一覧</h2>
      <button class="btn btn-sm" id="refresh-btn">↻ 更新</button>
      <a href="#/settings" class="btn btn-sm btn-primary">HoYoLAB 設定</a>
    </div>
    <p class="text-muted text-sm mb-2">
      キャラを選ぶと現在の装備とプリセットビルドが表示されます。
    </p>
    <div class="filter-bar">
      <input type="search" id="search-text" placeholder="🔍 キャラ名検索" class="input-sm" style="flex:1;min-width:160px;" />
      <label>並び替え:</label>
      <select id="sort-by">
        ${SORT_OPTIONS.map(o => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join('')}
      </select>
      <label>装備:</label>
      <select id="build-status">
        ${BUILD_STATUS_OPTIONS.map(o => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join('')}
      </select>
      <button class="btn btn-sm" id="filter-toggle" type="button" aria-expanded="false">
        🎛 フィルタ<span class="filter-total-badge" id="filter-total-badge"></span>
      </button>
      <button class="btn btn-sm btn-ghost" id="filter-clear-all" hidden>全解除</button>
      <span id="char-count" class="text-muted text-sm"></span>
    </div>
    <div class="active-filter-tags" id="active-tags" hidden></div>
    <div class="filter-panel" id="filter-panel" hidden>
      <div class="filter-section">
        <div class="filter-section-label">⚡ 属性</div>
        <div class="rec-sub-chips" id="element-chips">${elementChipsHtml}</div>
      </div>
      <div class="filter-section">
        <div class="filter-section-label">🏴 陣営</div>
        <div class="rec-sub-chips" id="faction-chips">
          <div class="text-muted text-sm">読み込み中…</div>
        </div>
      </div>
      <div class="filter-section">
        <div class="filter-section-label">🎯 推奨メインステ</div>
        <div class="filter-slot-rows">${slotRowsHtml}</div>
      </div>
    </div>
    <div id="char-list"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

function elementKey(label) {
  // CSS クラス化のため英数キーにマップ
  return ({ '物理': 'phys', '炎': 'fire', '氷': 'ice', '電気': 'elec',
           'エーテル': 'ether', '霜': 'frost' })[label] || 'other';
}

export async function mount() {
  document.getElementById('refresh-btn').addEventListener('click', load);

  const panel = document.getElementById('filter-panel');
  const toggleBtn = document.getElementById('filter-toggle');
  toggleBtn.addEventListener('click', () => {
    state.panelOpen = !state.panelOpen;
    panel.hidden = !state.panelOpen;
    toggleBtn.setAttribute('aria-expanded', String(state.panelOpen));
    toggleBtn.classList.toggle('on', state.panelOpen);
  });

  const sel = document.getElementById('sort-by');
  sel.value = state.sort;
  sel.addEventListener('change', (ev) => {
    state.sort = ev.target.value;
    renderGrid();
  });

  const searchEl = document.getElementById('search-text');
  searchEl.value = state.searchText;
  searchEl.addEventListener('input', (ev) => {
    state.searchText = (ev.target.value || '').trim();
    renderActiveTags();
    renderGrid();
  });

  const bsSel = document.getElementById('build-status');
  bsSel.value = state.buildStatus;
  bsSel.addEventListener('change', (ev) => {
    state.buildStatus = ev.target.value;
    renderActiveTags();
    renderGrid();
  });

  document.querySelectorAll('#element-chips input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const val = cb.dataset.val;
      if (cb.checked) state.elementFilter.add(val);
      else state.elementFilter.delete(val);
      cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
      renderActiveTags();
      renderGrid();
    });
  });

  document.querySelectorAll('.filter-slot-row input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const slot = cb.dataset.slot;
      const val = cb.dataset.val;
      const set = state.mainStatFilter[slot];
      if (cb.checked) set.add(val); else set.delete(val);
      cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
      renderActiveTags();
      renderGrid();
    });
  });

  document.getElementById('filter-clear-all').addEventListener('click', () => {
    clearAllFilters();
    renderActiveTags();
    renderGrid();
  });

  // アクティブタグのクリックで個別解除（イベント委譲）
  document.getElementById('active-tags').addEventListener('click', (ev) => {
    const tag = ev.target.closest('.active-tag');
    if (!tag) return;
    const { kind, val, slot } = tag.dataset;
    removeFilter(kind, val, slot);
    renderActiveTags();
    renderGrid();
  });

  await load();
}

function clearAllFilters() {
  state.searchText = '';
  state.elementFilter.clear();
  state.factionFilter.clear();
  state.buildStatus = 'all';
  Object.values(state.mainStatFilter).forEach(s => s.clear());
  const searchEl = document.getElementById('search-text');
  const bsSel = document.getElementById('build-status');
  if (searchEl) searchEl.value = '';
  if (bsSel) bsSel.value = 'all';
  document.querySelectorAll(
    '#element-chips input[type="checkbox"], #faction-chips input[type="checkbox"], .filter-slot-row input[type="checkbox"]'
  ).forEach(cb => {
    cb.checked = false;
    cb.closest('.rec-sub-chip')?.classList.remove('on');
  });
}

function removeFilter(kind, val, slot) {
  if (kind === 'search') {
    state.searchText = '';
    const el = document.getElementById('search-text'); if (el) el.value = '';
  } else if (kind === 'build') {
    state.buildStatus = 'all';
    const el = document.getElementById('build-status'); if (el) el.value = 'all';
  } else if (kind === 'element') {
    state.elementFilter.delete(val);
    syncChip('#element-chips', val, false);
  } else if (kind === 'faction') {
    state.factionFilter.delete(val);
    syncChip('#faction-chips', val, false);
  } else if (kind === 'mainstat') {
    state.mainStatFilter[slot]?.delete(val);
    const cb = document.querySelector(
      `.filter-slot-row[data-slot="${slot}"] input[data-val="${cssEscape(val)}"]`
    );
    if (cb) { cb.checked = false; cb.closest('.rec-sub-chip')?.classList.remove('on'); }
  }
}

function syncChip(containerSel, val, checked) {
  const cb = document.querySelector(
    `${containerSel} input[data-val="${cssEscape(val)}"]`
  );
  if (!cb) return;
  cb.checked = checked;
  cb.closest('.rec-sub-chip')?.classList.toggle('on', checked);
}

function cssEscape(s) {
  return String(s).replace(/"/g, '\\"');
}

function activeFilterCount() {
  return (state.searchText ? 1 : 0)
    + (state.buildStatus !== 'all' ? 1 : 0)
    + state.elementFilter.size
    + state.factionFilter.size
    + Object.values(state.mainStatFilter).reduce((a, s) => a + s.size, 0);
}

function renderActiveTags() {
  const el = document.getElementById('active-tags');
  const clearBtn = document.getElementById('filter-clear-all');
  const badge = document.getElementById('filter-total-badge');
  if (!el) return;
  const tags = [];
  if (state.searchText) {
    tags.push(tagHtml('search', null, null, `🔍 「${state.searchText}」`));
  }
  if (state.buildStatus !== 'all') {
    tags.push(tagHtml('build', null, null, `装備: ${BUILD_STATUS_LABEL[state.buildStatus]}`));
  }
  for (const v of state.elementFilter) {
    tags.push(tagHtml('element', v, null, `⚡ ${v}`, `element-${elementKey(v)}`));
  }
  for (const v of state.factionFilter) {
    tags.push(tagHtml('faction', v, null, `🏴 ${v}`));
  }
  for (const [slot, set] of Object.entries(state.mainStatFilter)) {
    for (const v of set) {
      tags.push(tagHtml('mainstat', v, slot, `🎯 ${slot}番 ${v}`));
    }
  }
  const total = activeFilterCount();
  if (badge) badge.textContent = total ? String(total) : '';
  if (clearBtn) clearBtn.hidden = total === 0;
  if (!tags.length) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  el.hidden = false;
  el.innerHTML = tags.join('');
}

function tagHtml(kind, val, slot, label, extraClass = '') {
  const attrs = [`data-kind="${kind}"`];
  if (val !== null) attrs.push(`data-val="${escapeHtml(val)}"`);
  if (slot !== null) attrs.push(`data-slot="${escapeHtml(slot)}"`);
  return `
    <button class="active-tag ${extraClass}" type="button" ${attrs.join(' ')} title="クリックで解除">
      <span>${escapeHtml(label)}</span>
      <span class="active-tag-x">✕</span>
    </button>
  `;
}

function renderFactionChips() {
  const factions = [...new Set(state.chars.map(c => c.faction).filter(Boolean))]
    .sort((a, b) => new Intl.Collator('ja').compare(a, b));
  const wrap = document.getElementById('faction-chips');
  if (!wrap) return;
  wrap.innerHTML = factions.map(n => `
    <label class="rec-sub-chip ${state.factionFilter.has(n) ? 'on' : ''}">
      <input type="checkbox" data-filter="faction" data-val="${escapeHtml(n)}" ${state.factionFilter.has(n) ? 'checked' : ''} />
      <span>${escapeHtml(n)}</span>
    </label>
  `).join('') || '<div class="text-muted text-sm">該当なし</div>';
  wrap.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const val = cb.dataset.val;
      if (cb.checked) state.factionFilter.add(val);
      else state.factionFilter.delete(val);
      cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
      renderActiveTags();
      renderGrid();
    });
  });
}

function matchSearch(c) {
  if (!state.searchText) return true;
  const q = state.searchText.toLowerCase();
  return [c.name_ja, c.slug, c.faction, elementLabel(c.element)]
    .filter(Boolean)
    .some(s => String(s).toLowerCase().includes(q));
}

function matchElement(c) {
  if (!state.elementFilter.size) return true;
  return state.elementFilter.has(elementLabel(c.element));
}

function matchFaction(c) {
  if (!state.factionFilter.size) return true;
  return state.factionFilter.has(c.faction);
}

function matchBuildStatus(c) {
  const hasCurrent = !!(c.has_current_build || c.current_build_id);
  const presetCount = c.preset_count ?? 0;
  switch (state.buildStatus) {
    case 'has_current': return hasCurrent;
    case 'no_current': return !hasCurrent;
    case 'has_preset': return presetCount > 0;
    case 'no_preset': return presetCount === 0;
    default: return true;
  }
}

function matchMainStatFilter(c) {
  const rec = c.recommended_main_stats || {};
  for (const [slot, set] of Object.entries(state.mainStatFilter)) {
    if (!set.size) continue;
    const chosen = Array.isArray(rec[slot]) ? rec[slot] : [];
    const hit = chosen.some(v => set.has(v));
    if (!hit) return false;
  }
  return true;
}

function applyFilters(chars) {
  return chars.filter(c =>
    matchSearch(c) && matchElement(c) && matchFaction(c)
    && matchBuildStatus(c) && matchMainStatFilter(c)
  );
}

async function load() {
  const el = document.getElementById('char-list');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const chars = await api('/characters');
    const list = Array.isArray(chars) ? chars : (chars?.characters || []);
    state.chars = list;
    renderFactionChips();
    renderActiveTags();
    renderGrid();
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function sortChars(chars, key) {
  const arr = chars.slice();
  const collator = new Intl.Collator('ja');
  switch (key) {
    case 'name_ja':
      arr.sort((a, b) => collator.compare(a.name_ja || '', b.name_ja || ''));
      break;
    case 'name_ja_desc':
      arr.sort((a, b) => collator.compare(b.name_ja || '', a.name_ja || ''));
      break;
    case 'element':
      arr.sort((a, b) => {
        const ae = elementLabel(a.element) || '';
        const be = elementLabel(b.element) || '';
        return collator.compare(ae, be)
          || (a.display_order ?? 0) - (b.display_order ?? 0);
      });
      break;
    case 'faction':
      arr.sort((a, b) => collator.compare(a.faction || '', b.faction || '')
        || (a.display_order ?? 0) - (b.display_order ?? 0));
      break;
    case 'has_current':
      arr.sort((a, b) => {
        const av = (a.has_current_build || a.current_build_id) ? 1 : 0;
        const bv = (b.has_current_build || b.current_build_id) ? 1 : 0;
        return bv - av || (a.display_order ?? 0) - (b.display_order ?? 0);
      });
      break;
    case 'preset_count':
      arr.sort((a, b) => (b.preset_count ?? 0) - (a.preset_count ?? 0)
        || (a.display_order ?? 0) - (b.display_order ?? 0));
      break;
    default:
      arr.sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0)
        || (a.id ?? 0) - (b.id ?? 0));
  }
  return arr;
}

function renderGrid() {
  const el = document.getElementById('char-list');
  const counter = document.getElementById('char-count');
  const filtered = applyFilters(state.chars);
  const chars = sortChars(filtered, state.sort);
  if (counter) counter.textContent = `${chars.length} / ${state.chars.length} 件`;
  if (!chars.length) {
    if (!state.chars.length) {
      el.innerHTML = '<div class="placeholder"><div class="big-icon">👥</div><div>キャラがまだ登録されていません</div><div class="text-muted text-sm mt-1">HoYoLAB 設定から同期してください</div></div>';
    } else {
      el.innerHTML = '<div class="placeholder"><div class="big-icon">🔍</div><div>フィルタ条件に一致するキャラがいません</div><div class="text-muted text-sm mt-1">「全解除」で初期状態に戻せます</div></div>';
    }
    return;
  }
  el.innerHTML = `
    <div class="character-grid">
      ${chars.map(cardHtml).join('')}
    </div>
  `;
}

function cardHtml(c) {
  const hasCurrent = !!c.has_current_build || !!c.current_build_id;
  const presetCount = c.preset_count ?? 0;
  return `
    <a href="#/characters/${encodeURIComponent(c.slug)}" class="character-card">
      <h3>${escapeHtml(c.name_ja)}</h3>
      <div class="text-muted text-xs mb-1">${escapeHtml(elementLabel(c.element))} / ${escapeHtml(c.faction || '-')}</div>
      <div class="row gap-1">
        ${hasCurrent
          ? '<span class="preset-badge set">● 現在ビルド</span>'
          : '<span class="preset-badge unset">未同期</span>'}
        ${presetCount > 0
          ? `<span class="preset-badge set">プリセット ${presetCount}</span>`
          : ''}
      </div>
    </a>
  `;
}
