/** ディスク詳細 + 合うキャラスコア降順 */
import { api } from '../api.js';
import { escapeHtml, toast, confirmDialog } from '../app.js';

export function render(params) {
  return `
    <div class="page-header">
      <a href="#/discs" class="btn btn-sm btn-ghost">← 一覧</a>
      <h2>ディスク詳細 #${escapeHtml(params.id)}</h2>
      <button class="btn btn-sm btn-danger" id="delete-btn">削除</button>
    </div>
    <div id="disc-info" class="card"><div class="spinner"></div></div>
    <div class="card">
      <h3 class="mb-1">🎯 合うキャラ（スコア降順）</h3>
      <div id="candidates"></div>
    </div>
  `;
}

export async function mount(params) {
  const id = params.id;
  document.getElementById('delete-btn').addEventListener('click', () => deleteDisc(id));
  try {
    const disc = await api(`/discs/${id}`);
    renderDisc(disc);
  } catch (err) {
    document.getElementById('disc-info').innerHTML = `<div class="text-muted">${escapeHtml(err.message)}</div>`;
  }
  try {
    const cands = await api(`/discs/${id}/candidates`);
    renderCandidates(Array.isArray(cands) ? cands : (cands?.candidates || []));
  } catch (err) {
    document.getElementById('candidates').innerHTML = `<div class="text-muted">${escapeHtml(err.message)}</div>`;
  }
}

function renderDisc(d) {
  const el = document.getElementById('disc-info');
  const subs = parseJSON(d.sub_stats_json) || [];
  el.innerHTML = `
    <div class="form-grid">
      <label>部位</label><div>${d.slot}</div>
      <label>セット</label><div>${escapeHtml(d.set_name || '-')}</div>
      <label>メインステ</label><div>${escapeHtml(d.main_stat_name || '')} ${d.main_stat_value ?? ''}</div>
      <label>サブステ</label>
      <div>
        ${subs.map(s => `<div>${escapeHtml(s.name)} +${s.value} ${s.upgrades ? `<span class="text-muted text-xs">(強化${s.upgrades})</span>` : ''}</div>`).join('') || '<span class="text-muted">—</span>'}
      </div>
      <label>メモ</label><div>${escapeHtml(d.note || '') || '<span class="text-muted">—</span>'}</div>
    </div>
  `;
}

function renderCandidates(cands) {
  const el = document.getElementById('candidates');
  if (!cands.length) {
    el.innerHTML = '<div class="text-muted">候補なし（プリセット未設定 or 閾値未満）</div>';
    return;
  }
  el.innerHTML = `
    <table class="data-table">
      <thead>
        <tr><th>キャラ</th><th style="width:120px;">スコア</th></tr>
      </thead>
      <tbody>
        ${cands.map(c => `
          <tr>
            <td>${escapeHtml(c.character_name || c.name_ja || c.slug || '-')}</td>
            <td class="mono">${(c.score ?? 0).toFixed(2)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function deleteDisc(id) {
  const ok = await confirmDialog(`ディスク #${id} を削除します。よろしいですか？`);
  if (!ok) return;
  try {
    await api(`/discs/${id}`, { method: 'DELETE' });
    toast('削除しました', 'info');
    location.hash = '#/discs';
  } catch (err) {
    toast(`削除失敗: ${err.message}`, 'error');
  }
}

function parseJSON(s) {
  if (!s) return null;
  if (typeof s === 'object') return s;
  try { return JSON.parse(s); } catch { return null; }
}
