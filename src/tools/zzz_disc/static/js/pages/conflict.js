/** 競合ビュー — キャラ×部位マトリックス + 共有ディスク一覧 */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';

export function render() {
  return `
    <div class="page-header">
      <h2>⚠️ 競合ビュー</h2>
      <button class="btn btn-sm" id="refresh-btn">↻ 更新</button>
    </div>
    <div id="conflict-body"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount() {
  document.getElementById('refresh-btn').addEventListener('click', load);
  await load();
}

async function load() {
  const el = document.getElementById('conflict-body');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const data = await api('/conflicts');
    renderBody(data || {});
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function renderBody(data) {
  const el = document.getElementById('conflict-body');
  const matrix = data.matrix || [];       // [{character_name, slots: {1: {top: [{disc_id, score}, ...]}, ...}}]
  const shared = data.shared_discs || []; // [{disc_id, summary, characters: [names]}]

  const slots = [1,2,3,4,5,6];
  const matrixHtml = matrix.length ? `
    <div class="card">
      <h3 class="mb-1">キャラ×部位マトリックス（TOP1のディスクID、競合セルは⚠）</h3>
      <table class="matrix">
        <thead>
          <tr>
            <th>キャラ</th>
            ${slots.map(s => `<th>部位${s}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${matrix.map(row => `
            <tr>
              <td>${escapeHtml(row.character_name || row.name_ja || row.slug || '-')}</td>
              ${slots.map(s => cellHtml(row.slots?.[s], row.conflicts?.[s])).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  ` : '';

  const sharedHtml = shared.length ? `
    <div class="card">
      <h3 class="mb-1">共有ディスク一覧（複数キャラが候補にしているディスク）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width:60px;">#</th>
            <th>ディスク</th>
            <th>候補キャラ</th>
          </tr>
        </thead>
        <tbody>
          ${shared.map(s => `
            <tr>
              <td class="mono">
                <a href="#/discs/${s.disc_id}">${s.disc_id}</a>
              </td>
              <td>${escapeHtml(s.summary || '-')}</td>
              <td>${(s.characters || []).map(c => escapeHtml(c)).join(', ')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  ` : '';

  el.innerHTML = (matrixHtml || sharedHtml) || '<div class="placeholder"><div class="big-icon">✓</div><div>競合はありません</div></div>';
}

function cellHtml(cell, isConflict) {
  if (!cell) return '<td>-</td>';
  const top = Array.isArray(cell.top) ? cell.top[0] : cell;
  if (!top) return '<td>-</td>';
  const discId = top.disc_id ?? top.id;
  const score = top.score ?? 0;
  const conflictCls = isConflict ? ' conflict' : '';
  return `<td class="${conflictCls.trim()}">
    ${isConflict ? '⚠ ' : ''}<a href="#/discs/${discId}">#${discId}</a>
    <div class="text-xs text-muted">${score.toFixed(1)}</div>
  </td>`;
}
