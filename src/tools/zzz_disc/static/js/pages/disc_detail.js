/** ディスク詳細 + このディスクを使っているビルド一覧 */
import { api } from '../api.js';
import { escapeHtml, toast, confirmDialog } from '../app.js';
import { statLabel, formatStatValue } from '../labels.js';

let _state = { disc: null };

export function render(params) {
  return `
    <div class="page-header">
      <a href="#/discs" class="btn btn-sm btn-ghost">← 一覧</a>
      <h2>ディスク #${escapeHtml(params.id)}</h2>
      <button class="btn btn-sm" id="pin-btn">📌 ピン</button>
      <button class="btn btn-sm btn-danger" id="delete-btn">削除</button>
    </div>
    <div id="disc-info" class="card"><div class="spinner"></div></div>
    <div class="card">
      <h3 class="mb-1">使用ビルド</h3>
      <div id="used-by"></div>
    </div>
  `;
}

export async function mount(params) {
  const id = params.id;
  document.getElementById('delete-btn').addEventListener('click', () => deleteDisc(id));
  document.getElementById('pin-btn').addEventListener('click', () => togglePin(id));
  try {
    const res = await api(`/discs/${id}`);
    const disc = res?.disc || res;
    _state.disc = disc;
    refreshPinBtn();
    renderDisc(disc);
    const usedBy = res?.used_by || [];
    if (usedBy.length) renderUsedBy(usedBy);
  } catch (err) {
    document.getElementById('disc-info').innerHTML = `<div class="text-muted">${escapeHtml(err.message)}</div>`;
  }
  try {
    const res = await api(`/discs/${id}/builds`);
    const builds = Array.isArray(res) ? res : (res?.builds || res?.used_by || []);
    if (builds.length) renderUsedBy(builds);
  } catch (err) {
    document.getElementById('used-by').innerHTML = `<div class="text-muted text-sm">${escapeHtml(err.message)}</div>`;
  }
}

function renderDisc(d) {
  const el = document.getElementById('disc-info');
  const subs = parseJSON(d.sub_stats_json) || d.sub_stats || [];
  const setName = d.set_name_ja || d.set_name || '-';
  const level = d.level != null ? `Lv.${d.level}` : '';
  el.innerHTML = `
    <div class="form-grid">
      <label>部位</label><div>${d.slot}</div>
      <label>セット</label><div>${escapeHtml(setName)} ${level ? `<span class="disc-tile-level">${escapeHtml(level)}</span>` : ''}</div>
      <label>メインステ</label><div>${escapeHtml(statLabel(d.main_stat_name))} <strong>${escapeHtml(formatStatValue(d.main_stat_name, d.main_stat_value))}</strong></div>
      <label>サブステ</label>
      <div class="disc-subs">
        ${(subs || []).map(s => `
          <div class="disc-sub-row">
            <span class="sub-name">${escapeHtml(statLabel(s.name))}</span>
            ${Number(s.upgrades || 0) > 0 ? `<span class="sub-dots">${'<span class="dot"></span>'.repeat(Number(s.upgrades))}</span>` : ''}
            <span class="sub-value">${escapeHtml(formatStatValue(s.name, s.value))}</span>
          </div>
        `).join('') || '<span class="text-muted">—</span>'}
      </div>
      <label>メモ</label><div>${escapeHtml(d.note || '') || '<span class="text-muted">—</span>'}</div>
      ${d.fingerprint ? `<label>fingerprint</label><div class="mono text-xs text-muted">${escapeHtml(d.fingerprint)}</div>` : ''}
    </div>
  `;
}

function renderUsedBy(builds) {
  const el = document.getElementById('used-by');
  if (!builds.length) {
    el.innerHTML = '<div class="text-muted text-sm">このディスクはまだどのビルドでも使われていません</div>';
    return;
  }
  el.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>キャラ</th>
          <th>ビルド</th>
          <th style="width:80px;"></th>
        </tr>
      </thead>
      <tbody>
        ${builds.map(b => `
          <tr>
            <td>${escapeHtml(b.character_name_ja || b.slug || '-')}</td>
            <td>
              ${escapeHtml(b.name || '無名')}
              ${b.is_current ? '<span class="build-current-badge" style="margin-left:6px;">現在</span>' : ''}
              ${b.rank ? `<span class="rank-badge rank-${escapeHtml(b.rank)}" style="margin-left:6px;">${escapeHtml(b.rank)}</span>` : ''}
            </td>
            <td>${b.character_slug ? `<a href="#/characters/${escapeHtml(b.character_slug)}" class="btn btn-sm">開く</a>` : ''}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function refreshPinBtn() {
  const btn = document.getElementById('pin-btn');
  if (!btn || !_state.disc) return;
  const on = !!_state.disc.is_pinned;
  btn.textContent = on ? '📌 ピン済み（解除）' : '📌 ピン留め';
  btn.classList.toggle('btn-primary', on);
}

async function togglePin(id) {
  if (!_state.disc) return;
  const next = !_state.disc.is_pinned;
  const btn = document.getElementById('pin-btn');
  if (btn) btn.disabled = true;
  try {
    const res = await api(`/discs/${id}/pin`, { method: 'PUT', body: { pinned: next } });
    _state.disc = res?.disc || { ..._state.disc, is_pinned: next };
    refreshPinBtn();
    toast(next ? '📌 ピン留めしました' : 'ピン解除しました', 'success');
  } catch (err) {
    toast(`ピン操作失敗: ${err.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function deleteDisc(id) {
  const ok = await confirmDialog(`ディスク #${id} を削除します。よろしいですか？\n（使用中のビルドからも外れます）`);
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
