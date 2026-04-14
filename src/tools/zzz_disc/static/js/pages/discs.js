/** ディスク一覧 + フィルタ + ⚠競合マーカー */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';

let state = { masters: null, filters: { slot: '', set_id: '' }, discs: [], conflicts: new Set() };

const SLOT_LABEL = { 1: '1', 2: '2', 3: '3', 4: '4', 5: '5', 6: '6' };

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
    </div>
    <div id="discs-table"></div>
  `;
}

export async function mount() {
  try {
    state.masters = await api('/masters');
    fillSetFilter();
  } catch (err) {
    toast(`マスタ取得失敗: ${err.message}`, 'error');
    state.masters = { characters: [], sets: [] };
  }
  document.getElementById('refresh-btn').addEventListener('click', loadDiscs);
  document.getElementById('filter-slot').addEventListener('change', (e) => {
    state.filters.slot = e.target.value;
    loadDiscs();
  });
  document.getElementById('filter-set').addEventListener('change', (e) => {
    state.filters.set_id = e.target.value;
    loadDiscs();
  });
  await loadConflicts();
  await loadDiscs();
}

function fillSetFilter() {
  const sel = document.getElementById('filter-set');
  for (const s of state.masters.sets || []) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.name_ja;
    sel.appendChild(opt);
  }
}

async function loadConflicts() {
  try {
    const data = await api('/conflicts');
    const ids = data?.shared_disc_ids || data?.conflicts?.flatMap(c => c.disc_ids || []) || [];
    state.conflicts = new Set(ids);
  } catch {
    state.conflicts = new Set();
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
  const s = (state.masters?.sets || []).find(x => x.id === id);
  return s?.name_ja || '-';
}

function renderTable() {
  const container = document.getElementById('discs-table');
  if (!state.discs.length) {
    container.innerHTML = '<div class="placeholder"><div class="big-icon">💿</div><div>ディスクはまだ登録されていません</div></div>';
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
          <th style="width:50px;"></th>
          <th style="width:80px;"></th>
        </tr>
      </thead>
      <tbody>
        ${state.discs.map(d => rowHtml(d)).join('')}
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
  const isConflict = state.conflicts.has(d.id);
  const subs = parseJSON(d.sub_stats_json) || [];
  const subText = subs.map(s => `${s.name}+${s.value}${s.upgrades ? `(${s.upgrades})` : ''}`).join(' / ');
  const mainText = d.main_stat_name
    ? `${d.main_stat_name} ${d.main_stat_value ?? ''}`
    : '-';
  return `
    <tr>
      <td class="text-muted mono">${d.id}</td>
      <td>${SLOT_LABEL[d.slot] ?? d.slot}</td>
      <td>${escapeHtml(d.set_name || setNameById(d.set_id))}</td>
      <td>${escapeHtml(mainText)}</td>
      <td class="text-sm text-secondary">${escapeHtml(subText)}</td>
      <td>${isConflict ? '<span class="conflict-mark" title="複数キャラで候補">⚠</span>' : ''}</td>
      <td><button class="btn btn-sm" data-detail-id="${d.id}">詳細</button></td>
    </tr>
  `;
}

function parseJSON(s) {
  if (!s) return null;
  if (typeof s === 'object') return s;
  try { return JSON.parse(s); } catch { return null; }
}
