/** ディスク一覧 + フィルタ + ソート + 共有マーカー */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';
import { statLabel, formatStatValue, STAT_LABELS } from '../labels.js';

const SORT_OPTIONS = [
  { value: 'id_desc', label: '新しい順' },
  { value: 'slot', label: '部位' },
  { value: 'set', label: 'セット' },
  { value: 'main_stat', label: 'メインステ種別' },
  { value: 'sub_stat', label: 'サブステ値（要選択）' },
];

const SUB_STAT_KEYS = [
  'hp_flat', 'atk_flat', 'def_flat',
  'hp_pct', 'atk_pct', 'def_pct',
  'crit_rate', 'crit_dmg',
  'pen_value', 'anomaly_proficiency',
];

let state = {
  sets: [],
  filters: { slot: '', set_id: '', character_id: '', unassigned: false, shared: false },
  sort: 'id_desc',
  subStatKey: 'crit_rate',
  discs: [],
  shared: new Set(),
  usageByDisc: new Map(), // disc_id -> [{character_id, character_name_ja, build_name, is_current}]
  characters: [],         // for filter dropdown (deduped from usage)
};

export function render() {
  return `
    <div class="page-header">
      <h2>💿 ディスク一覧</h2>
      <button class="btn btn-sm" id="refresh-btn">↻ 更新</button>
    </div>
    <div class="filter-bar">
      <label>部位:</label>
      <select id="filter-slot">
        <option value="">全て</option>
        ${[1,2,3,4,5,6].map(s => `<option value="${s}">${s}</option>`).join('')}
      </select>
      <label>セット:</label>
      <select id="filter-set"><option value="">全て</option></select>
      <label>キャラ:</label>
      <select id="filter-character"><option value="">全て</option></select>
      <label class="inline-label">
        <input type="checkbox" id="filter-unassigned" /> 未割当のみ
      </label>
      <label class="inline-label">
        <input type="checkbox" id="filter-shared" /> 共有のみ
      </label>
    </div>
    <div class="filter-bar">
      <label>並び替え:</label>
      <select id="sort-by">
        ${SORT_OPTIONS.map(o => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join('')}
      </select>
      <span id="sub-stat-wrap" style="display:none;">
        <label>サブステ:</label>
        <select id="sort-sub-stat">
          ${SUB_STAT_KEYS.map(k => `<option value="${k}">${escapeHtml(STAT_LABELS[k] || k)}</option>`).join('')}
        </select>
      </span>
      <span id="disc-count" class="text-muted text-sm"></span>
    </div>
    <div id="discs-table"></div>
  `;
}

export async function mount() {
  try {
    const setsRes = await api('/sets');
    state.sets = Array.isArray(setsRes) ? setsRes : (setsRes?.sets || []);
    fillSetFilter();
  } catch (err) {
    state.sets = [];
  }
  document.getElementById('refresh-btn').addEventListener('click', refresh);
  document.getElementById('filter-slot').addEventListener('change', (e) => {
    state.filters.slot = e.target.value;
    loadDiscs();
  });
  document.getElementById('filter-set').addEventListener('change', (e) => {
    state.filters.set_id = e.target.value;
    loadDiscs();
  });
  document.getElementById('filter-character').addEventListener('change', (e) => {
    state.filters.character_id = e.target.value;
    renderTable();
  });
  document.getElementById('filter-unassigned').addEventListener('change', (e) => {
    state.filters.unassigned = e.target.checked;
    renderTable();
  });
  document.getElementById('filter-shared').addEventListener('change', (e) => {
    state.filters.shared = e.target.checked;
    renderTable();
  });
  document.getElementById('sort-by').addEventListener('change', (e) => {
    state.sort = e.target.value;
    document.getElementById('sub-stat-wrap').style.display =
      state.sort === 'sub_stat' ? '' : 'none';
    renderTable();
  });
  document.getElementById('sort-sub-stat').addEventListener('change', (e) => {
    state.subStatKey = e.target.value;
    if (state.sort === 'sub_stat') renderTable();
  });
  await Promise.all([loadShared(), loadUsage()]);
  await loadDiscs();
}

async function refresh() {
  await Promise.all([loadShared(), loadUsage()]);
  await loadDiscs();
}

function fillSetFilter() {
  const sel = document.getElementById('filter-set');
  for (const s of state.sets) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.name_ja;
    sel.appendChild(opt);
  }
}

function fillCharFilter() {
  const sel = document.getElementById('filter-character');
  const current = sel.value;
  // 既存 option（"全て"以外）を除去
  while (sel.options.length > 1) sel.remove(1);
  for (const c of state.characters) {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.name_ja;
    sel.appendChild(opt);
  }
  if (current && [...sel.options].some(o => o.value === current)) {
    sel.value = current;
  }
}

async function loadShared() {
  try {
    const data = await api('/shared-discs');
    const items = Array.isArray(data) ? data : (data?.shared_discs || data?.items || data?.shared || []);
    state.shared = new Set(items.map(x => (x.disc?.id ?? x.id)).filter(x => x != null));
  } catch {
    state.shared = new Set();
  }
}

async function loadUsage() {
  try {
    const data = await api('/disc-usage');
    const rows = Array.isArray(data) ? data : (data?.usage || []);
    const byDisc = new Map();
    const charMap = new Map();
    for (const r of rows) {
      const list = byDisc.get(r.disc_id) || [];
      list.push(r);
      byDisc.set(r.disc_id, list);
      if (r.character_id != null && !charMap.has(r.character_id)) {
        charMap.set(r.character_id, {
          id: r.character_id,
          name_ja: r.character_name_ja,
          slug: r.character_slug,
        });
      }
    }
    state.usageByDisc = byDisc;
    state.characters = [...charMap.values()].sort(
      (a, b) => new Intl.Collator('ja').compare(a.name_ja || '', b.name_ja || '')
    );
    fillCharFilter();
  } catch {
    state.usageByDisc = new Map();
    state.characters = [];
  }
}

async function loadDiscs() {
  const container = document.getElementById('discs-table');
  container.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const data = await api('/discs', { params: { slot: state.filters.slot, set_id: state.filters.set_id } });
    state.discs = Array.isArray(data) ? data : (data?.discs || []);
    renderTable();
  } catch (err) {
    container.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function setNameById(id) {
  const s = state.sets.find(x => x.id === id);
  return s?.name_ja || '-';
}

function applyFilters(discs) {
  const charId = state.filters.character_id ? Number(state.filters.character_id) : null;
  return discs.filter(d => {
    const usage = state.usageByDisc.get(d.id) || [];
    if (state.filters.unassigned && usage.length > 0) return false;
    if (state.filters.shared && !state.shared.has(d.id)) return false;
    if (charId != null) {
      if (!usage.some(u => u.character_id === charId)) return false;
    }
    return true;
  });
}

function subStatValue(d, key) {
  const subs = parseJSON(d.sub_stats_json) || d.sub_stats || [];
  const hit = subs.find(s => s.name === key);
  if (!hit) return null;
  const v = typeof hit.value === 'number' ? hit.value : parseFloat(hit.value);
  return Number.isFinite(v) ? v : null;
}

function sortDiscs(discs) {
  const arr = discs.slice();
  const collator = new Intl.Collator('ja');
  switch (state.sort) {
    case 'slot':
      arr.sort((a, b) => (a.slot - b.slot) || (b.id - a.id));
      break;
    case 'set':
      arr.sort((a, b) => {
        const an = a.set_name_ja || setNameById(a.set_id) || '';
        const bn = b.set_name_ja || setNameById(b.set_id) || '';
        return collator.compare(an, bn) || (a.slot - b.slot) || (b.id - a.id);
      });
      break;
    case 'main_stat':
      arr.sort((a, b) => {
        const al = statLabel(a.main_stat_name) || '';
        const bl = statLabel(b.main_stat_name) || '';
        return collator.compare(al, bl) || (a.slot - b.slot) || (b.id - a.id);
      });
      break;
    case 'sub_stat': {
      const k = state.subStatKey;
      arr.sort((a, b) => {
        const av = subStatValue(a, k);
        const bv = subStatValue(b, k);
        if (av == null && bv == null) return b.id - a.id;
        if (av == null) return 1;
        if (bv == null) return -1;
        return bv - av || (b.id - a.id);
      });
      break;
    }
    default:
      arr.sort((a, b) => b.id - a.id);
  }
  return arr;
}

function renderTable() {
  const container = document.getElementById('discs-table');
  const counter = document.getElementById('disc-count');
  const filtered = applyFilters(state.discs);
  const sorted = sortDiscs(filtered);
  if (counter) counter.textContent = `${sorted.length} / ${state.discs.length} 件`;
  if (!sorted.length) {
    container.innerHTML = '<div class="placeholder"><div class="big-icon">💿</div><div>条件に合うディスクがありません</div></div>';
    return;
  }
  container.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th style="width:40px;">#</th>
          <th style="width:60px;">部位</th>
          <th>セット</th>
          <th>メインステ</th>
          <th>サブステ</th>
          <th>使用ビルド</th>
          <th style="width:50px;"></th>
          <th style="width:80px;"></th>
        </tr>
      </thead>
      <tbody>
        ${sorted.map(d => rowHtml(d)).join('')}
      </tbody>
    </table>
  `;
  container.querySelectorAll('[data-detail-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      location.hash = `#/discs/${btn.dataset.detailId}`;
    });
  });
}

function rowHtml(d) {
  const isShared = state.shared.has(d.id);
  const subs = parseJSON(d.sub_stats_json) || d.sub_stats || [];
  const subText = (subs || []).map(s => `${statLabel(s.name)}+${formatStatValue(s.name, s.value)}${s.upgrades ? `(${s.upgrades})` : ''}`).join(' / ');
  const mainText = d.main_stat_name
    ? `${statLabel(d.main_stat_name)} ${formatStatValue(d.main_stat_name, d.main_stat_value)}`
    : '-';
  const level = d.level != null ? ` <span class="text-xs text-muted">Lv.${d.level}</span>` : '';
  const usage = state.usageByDisc.get(d.id) || [];
  const usageText = usage.length
    ? usage.map(u => `${escapeHtml(u.character_name_ja || '')}${u.is_current ? '★' : ''}`).join(', ')
    : '<span class="text-muted">未割当</span>';
  return `
    <tr>
      <td class="text-muted mono">${d.id}</td>
      <td>${d.slot}</td>
      <td>${escapeHtml(d.set_name_ja || d.set_name || setNameById(d.set_id))}${level}</td>
      <td>${escapeHtml(mainText)}</td>
      <td class="text-sm text-secondary">${escapeHtml(subText)}</td>
      <td class="text-sm">${usageText}</td>
      <td>${isShared ? '<span class="conflict-mark" title="複数ビルドで使用中">⚠</span>' : ''}</td>
      <td><button class="btn btn-sm" data-detail-id="${d.id}">詳細</button></td>
    </tr>
  `;
}

function parseJSON(s) {
  if (!s) return null;
  if (typeof s === 'object') return s;
  try { return JSON.parse(s); } catch { return null; }
}
