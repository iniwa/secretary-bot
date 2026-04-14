/** キャラ一覧（プリセット設定済み/未設定バッジ） */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';

export function render() {
  return `
    <div class="page-header">
      <h2>👤 プリセット — キャラ一覧</h2>
    </div>
    <p class="text-muted text-sm mb-2">キャラを選ぶと 6 部位のプリセットを編集できます。</p>
    <div id="char-list"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount() {
  try {
    const masters = await api('/masters');
    const chars = masters?.characters || [];
    const statuses = await Promise.all(chars.map(async (c) => {
      try {
        const preset = await api(`/presets/${c.id}`);
        const entries = Array.isArray(preset) ? preset : (preset?.presets || []);
        return { char: c, count: entries.filter(e => e && (e.preferred_set_ids_json || e.preferred_main_stats_json)).length };
      } catch {
        return { char: c, count: 0 };
      }
    }));
    render2(statuses);
  } catch (err) {
    document.getElementById('char-list').innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function render2(statuses) {
  const el = document.getElementById('char-list');
  if (!statuses.length) {
    el.innerHTML = '<div class="placeholder">キャラマスタが空です</div>';
    return;
  }
  el.innerHTML = `
    <div class="character-grid">
      ${statuses.map(({ char, count }) => `
        <a href="#/presets/${encodeURIComponent(char.slug)}" class="character-card">
          <h3>${escapeHtml(char.name_ja)}</h3>
          <div class="text-muted text-xs mb-1">${escapeHtml(char.element || '-')} / ${escapeHtml(char.faction || '-')}</div>
          ${count >= 6
            ? '<span class="preset-badge set">✓ 設定済み</span>'
            : count > 0
              ? `<span class="preset-badge set">部分設定 ${count}/6</span>`
              : '<span class="preset-badge unset">未設定</span>'}
        </a>
      `).join('')}
    </div>
  `;
}
