/** Pending Actions — 自律アクションの承認待ち一覧 */
import { api } from '../api.js';
import { toast } from '../app.js';

let timer = null;

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtDt(iso) {
  if (!iso) return '---';
  try {
    const d = new Date(iso);
    return d.toLocaleString('ja-JP', { hour12: false });
  } catch { return iso; }
}

function remaining(expires) {
  if (!expires) return '';
  const diff = new Date(expires).getTime() - Date.now();
  if (diff <= 0) return '期限切れ';
  const m = Math.floor(diff / 60000);
  if (m < 60) return `残り ${m}分`;
  const h = Math.floor(m / 60);
  return `残り ${h}時間${m % 60}分`;
}

function statusBadge(s) {
  const map = {
    pending: ['badge-warning', '承認待ち'],
    approved: ['badge-success', '承認'],
    executed: ['badge-success', '実行済'],
    rejected: ['badge-muted', '却下'],
    expired: ['badge-muted', '期限切れ'],
    cancelled: ['badge-muted', 'キャンセル'],
    failed: ['badge-danger', '失敗'],
    cancelled_by_newer: ['badge-muted', '上書き'],
  };
  const [cls, label] = map[s] || ['badge-muted', s || '?'];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function renderItem(item) {
  let params = '';
  try { params = JSON.stringify(JSON.parse(item.params || '{}'), null, 2); }
  catch { params = item.params || ''; }
  const isPending = item.status === 'pending';
  const unitLabel = item.unit_name ? `${item.unit_name}.${item.method || ''}` : '(内部)';
  return `
    <div class="card" data-pid="${item.id}">
      <div class="card-header">
        <div>
          <strong>#${item.id}</strong>
          <span class="muted">${esc(unitLabel)}</span>
          ${statusBadge(item.status)}
          <span class="muted">Tier ${item.tier}</span>
        </div>
        <div class="muted">${esc(fmtDt(item.created_at))}</div>
      </div>
      <div class="card-body">
        <div><strong>要約:</strong> ${esc(item.summary || '')}</div>
        ${item.reasoning ? `<div><strong>理由:</strong> ${esc(item.reasoning)}</div>` : ''}
        <details><summary>params</summary><pre>${esc(params)}</pre></details>
        ${item.result ? `<details><summary>結果</summary><pre>${esc(item.result)}</pre></details>` : ''}
        ${item.error ? `<div class="error"><strong>エラー:</strong> ${esc(item.error)}</div>` : ''}
        <div class="muted">${esc(remaining(item.expires_at))} / 期限 ${esc(fmtDt(item.expires_at))}</div>
      </div>
      ${isPending ? `
        <div class="card-footer">
          <button class="btn btn-primary" data-action="approve">承認</button>
          <button class="btn btn-danger" data-action="reject">却下</button>
          <button class="btn" data-action="cancel">キャンセル</button>
        </div>
      ` : ''}
    </div>
  `;
}

export function render() {
  return `
    <div class="page-pending">
      <div class="toolbar">
        <button class="btn" id="pending-refresh">再読込</button>
        <select id="pending-filter">
          <option value="">すべて</option>
          <option value="pending" selected>承認待ち</option>
          <option value="executed">実行済</option>
          <option value="rejected">却下</option>
          <option value="expired">期限切れ</option>
          <option value="failed">失敗</option>
        </select>
      </div>
      <div id="pending-list">Loading...</div>
    </div>
  `;
}

async function load() {
  const sel = document.getElementById('pending-filter');
  const status = sel ? sel.value : '';
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  params.set('limit', '100');
  const data = await api.get(`/api/pending?${params.toString()}`);
  const list = document.getElementById('pending-list');
  if (!list) return;
  const items = data.items || [];
  if (!items.length) {
    list.innerHTML = '<div class="muted">該当なし</div>';
    return;
  }
  list.innerHTML = items.map(renderItem).join('');
  list.querySelectorAll('.card').forEach(card => {
    const pid = card.dataset.pid;
    card.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => handleAction(pid, btn.dataset.action));
    });
  });
}

async function handleAction(pid, action) {
  if (!confirm(`#${pid} を ${action} します。よろしいですか？`)) return;
  try {
    await api.post(`/api/pending/${pid}/${action}`, {});
    toast(`#${pid} ${action}`, 'success');
    await load();
  } catch (e) {
    toast(`${action} 失敗: ${e.message || e}`, 'error');
  }
}

export async function mount() {
  document.getElementById('pending-refresh')?.addEventListener('click', load);
  document.getElementById('pending-filter')?.addEventListener('change', load);
  await load();
  timer = setInterval(load, 15000);
}

export function unmount() {
  if (timer) { clearInterval(timer); timer = null; }
}
