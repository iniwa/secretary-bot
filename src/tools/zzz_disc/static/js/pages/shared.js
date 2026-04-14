/** 共有ディスク一覧（複数ビルドで使われているディスク） */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';
import { statLabel, formatStatValue } from '../labels.js';

export function render() {
  return `
    <div class="page-header">
      <h2>🔗 共有ディスク</h2>
      <button class="btn btn-sm" id="refresh-btn">↻ 更新</button>
    </div>
    <p class="text-muted text-sm mb-2">
      複数のビルド（現在装備 + プリセット）で同じディスクが使われているものを一覧表示します。
    </p>
    <div id="body"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount() {
  document.getElementById('refresh-btn').addEventListener('click', load);
  await load();
}

async function load() {
  const el = document.getElementById('body');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const data = await api('/shared-discs');
    const items = Array.isArray(data) ? data : (data?.items || data?.shared || []);
    renderTable(items);
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function renderTable(items) {
  const el = document.getElementById('body');
  if (!items.length) {
    el.innerHTML = '<div class="placeholder"><div class="big-icon">✓</div><div>共有ディスクはありません</div></div>';
    return;
  }
  el.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th style="width:60px;">#</th>
          <th style="width:60px;">部位</th>
          <th>セット / メインステ</th>
          <th>使用ビルド</th>
          <th style="width:100px;"></th>
        </tr>
      </thead>
      <tbody>
        ${items.map(rowHtml).join('')}
      </tbody>
    </table>
  `;
  el.querySelectorAll('[data-detail-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      location.hash = `#/discs/${btn.dataset.detailId}`;
    });
  });
}

function rowHtml(item) {
  const disc = item.disc || item;
  const builds = item.builds || item.used_by || [];
  const mainText = disc.main_stat_name
    ? `${statLabel(disc.main_stat_name)} ${formatStatValue(disc.main_stat_name, disc.main_stat_value)}`
    : '-';
  const setName = disc.set_name_ja || disc.set_name || '-';
  return `
    <tr>
      <td class="mono">${disc.id}</td>
      <td>${escapeHtml(String(disc.slot || '-'))}</td>
      <td>
        <div>${escapeHtml(setName)}</div>
        <div class="text-xs text-muted">${escapeHtml(mainText)}</div>
      </td>
      <td class="text-sm">
        ${builds.map(b => `
          <div>
            ${escapeHtml(b.character_name_ja || b.slug || '-')} / ${escapeHtml(b.name || '無名')}
            ${b.is_current ? '<span class="build-current-badge" style="margin-left:4px;">現在</span>' : ''}
          </div>
        `).join('')}
      </td>
      <td><button class="btn btn-sm" data-detail-id="${disc.id}">詳細</button></td>
    </tr>
  `;
}
