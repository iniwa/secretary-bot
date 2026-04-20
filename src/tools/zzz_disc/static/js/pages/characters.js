/** キャラ一覧グリッド（現在ビルド有無バッジ + プリセット数 + ソート） */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';
import { elementLabel } from '../labels.js';

const SORT_OPTIONS = [
  { value: 'display_order', label: '既定順' },
  { value: 'name_ja', label: '名前' },
  { value: 'element', label: '属性' },
  { value: 'faction', label: '陣営' },
  { value: 'has_current', label: '現在ビルド有→無' },
  { value: 'preset_count', label: 'プリセット数 多→少' },
];

const MAIN_STAT_FILTER_SLOTS = [
  { key: '4', label: '4号位',
    candidates: ['HP%', '攻撃力%', '防御力%', '会心率%', '会心ダメージ%', '異常掌握'] },
  { key: '5', label: '5号位',
    candidates: ['HP%', '攻撃力%', '防御力%', '貫通率%',
                 '物理属性ダメージ%', '炎属性ダメージ%', '氷属性ダメージ%',
                 '電気属性ダメージ%', 'エーテル属性ダメージ%'] },
  { key: '6', label: '6号位',
    candidates: ['HP%', '攻撃力%', '防御力%', '異常マスタリー', '異常掌握',
                 '衝撃力%', 'エネルギー自動回復%'] },
];

let state = {
  chars: [],
  sort: 'display_order',
  mainStatFilter: { '4': new Set(), '5': new Set(), '6': new Set() },
};

export function render() {
  const slotChipsHtml = MAIN_STAT_FILTER_SLOTS.map(({ key, label, candidates }) => `
    <details class="main-stat-filter-slot" data-slot="${key}">
      <summary><strong>${escapeHtml(label)}</strong></summary>
      <div class="rec-sub-chips">
        ${candidates.map(n => `
          <label class="rec-sub-chip">
            <input type="checkbox" data-slot="${key}" data-val="${escapeHtml(n)}" />
            <span>${escapeHtml(n)}</span>
          </label>
        `).join('')}
      </div>
    </details>
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
      <label>並び替え:</label>
      <select id="sort-by">
        ${SORT_OPTIONS.map(o => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join('')}
      </select>
      <span id="char-count" class="text-muted text-sm"></span>
    </div>
    <details class="main-stat-filter-wrap" id="main-stat-filter">
      <summary class="text-sm text-muted">🎯 推奨メインステで絞り込み <span id="main-stat-filter-count"></span></summary>
      <div class="main-stat-filter-body">
        ${slotChipsHtml}
        <button class="btn btn-sm" id="main-stat-filter-clear">クリア</button>
      </div>
    </details>
    <div id="char-list"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount() {
  document.getElementById('refresh-btn').addEventListener('click', load);
  const sel = document.getElementById('sort-by');
  sel.value = state.sort;
  sel.addEventListener('change', (ev) => {
    state.sort = ev.target.value;
    renderGrid();
  });
  document.querySelectorAll('#main-stat-filter input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const slot = cb.dataset.slot;
      const val = cb.dataset.val;
      const set = state.mainStatFilter[slot];
      if (cb.checked) set.add(val); else set.delete(val);
      updateMainStatFilterBadge();
      renderGrid();
    });
  });
  document.getElementById('main-stat-filter-clear').addEventListener('click', () => {
    Object.values(state.mainStatFilter).forEach(s => s.clear());
    document.querySelectorAll('#main-stat-filter input[type="checkbox"]').forEach(cb => {
      cb.checked = false;
    });
    updateMainStatFilterBadge();
    renderGrid();
  });
  await load();
}

function updateMainStatFilterBadge() {
  const el = document.getElementById('main-stat-filter-count');
  if (!el) return;
  const total = Object.values(state.mainStatFilter).reduce((a, s) => a + s.size, 0);
  el.textContent = total ? `（${total} 件選択中）` : '';
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

async function load() {
  const el = document.getElementById('char-list');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const chars = await api('/characters');
    const list = Array.isArray(chars) ? chars : (chars?.characters || []);
    state.chars = list;
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
  const filtered = state.chars.filter(matchMainStatFilter);
  const chars = sortChars(filtered, state.sort);
  if (counter) counter.textContent = `${chars.length} / ${state.chars.length} 件`;
  if (!chars.length) {
    el.innerHTML = '<div class="placeholder"><div class="big-icon">👥</div><div>キャラがまだ登録されていません</div><div class="text-muted text-sm mt-1">HoYoLAB 設定から同期してください</div></div>';
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
