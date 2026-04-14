/** キャラ一覧グリッド（現在ビルド有無バッジ + プリセット数） */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';
import { elementLabel } from '../labels.js';

export function render() {
  return `
    <div class="page-header">
      <h2>👥 キャラ一覧</h2>
      <button class="btn btn-sm" id="refresh-btn">↻ 更新</button>
      <a href="#/settings" class="btn btn-sm btn-primary">HoYoLAB 設定</a>
    </div>
    <p class="text-muted text-sm mb-2">
      キャラを選ぶと現在の装備とプリセットビルドが表示されます。
    </p>
    <div id="char-list"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount() {
  document.getElementById('refresh-btn').addEventListener('click', load);
  await load();
}

async function load() {
  const el = document.getElementById('char-list');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const chars = await api('/characters');
    const list = Array.isArray(chars) ? chars : (chars?.characters || []);
    renderGrid(list);
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function renderGrid(chars) {
  const el = document.getElementById('char-list');
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
