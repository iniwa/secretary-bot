/** ディスク一覧 + フィルタ + 共有マーカー */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';
import { statLabel, formatStatValue } from '../labels.js';

let state = { sets: [], filters: { slot: '', set_id: '' }, discs: [], shared: new Set() };

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
    const setsRes = await api('/sets');
    state.sets = Array.isArray(setsRes) ? setsRes : (setsRes?.sets || []);
    fillSetFilter();
  } catch (err) {
    state.sets = [];
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
  await loadShared();
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

async function loadShared() {
  try {
    const data = await api('/shared-discs');
    const items = Array.isArray(data) ? data : (data?.items || data?.shared || []);
    state.shared = new Set(items.map(x => (x.disc?.id ?? x.id)).filter(x => x != null));
  } catch {
    state.shared = new Set();
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
  const isShared = state.shared.has(d.id);
  const subs = parseJSON(d.sub_stats_json) || d.sub_stats || [];
  const subText = (subs || []).map(s => `${statLabel(s.name)}+${formatStatValue(s.name, s.value)}${s.upgrades ? `(${s.upgrades})` : ''}`).join(' / ');
  const mainText = d.main_stat_name
    ? `${statLabel(d.main_stat_name)} ${formatStatValue(d.main_stat_name, d.main_stat_value)}`
    : '-';
  const level = d.level != null ? ` <span class="text-xs text-muted">Lv.${d.level}</span>` : '';
  return `
    <tr>
      <td class="text-muted mono">${d.id}</td>
      <td>${d.slot}</td>
      <td>${escapeHtml(d.set_name_ja || d.set_name || setNameById(d.set_id))}${level}</td>
      <td>${escapeHtml(mainText)}</td>
      <td class="text-sm text-secondary">${escapeHtml(subText)}</td>
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
