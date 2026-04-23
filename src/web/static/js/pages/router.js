/** Router page — チャットルーターが選択肢にしているユニット一覧。 */
import { api } from '../api.js';

let _refreshTimer = null;

function $(id) { return document.getElementById(id); }

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function breakerBadge(state) {
  if (!state) return '';
  const cls = state === 'closed' ? 'badge-success'
    : state === 'open' ? 'badge-error'
    : 'badge-warning';
  return `<span class="badge ${cls}">${esc(state)}</span>`;
}

function delegateBadge(d) {
  if (!d) return '<span style="color:var(--text-muted)">local</span>';
  return `<span class="badge badge-info">${esc(d)}</span>`;
}

export function render() {
  return `
<style>
  .router-page { display: flex; flex-direction: column; gap: 1rem; }
  .router-help {
    color: var(--text-muted);
    font-size: 0.8125rem;
    line-height: 1.5;
  }
  .router-prompt {
    background: var(--surface-2, #1a1d24);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.75rem 1rem;
    font-family: var(--font-mono, monospace);
    font-size: 0.8125rem;
    white-space: pre-wrap;
    color: var(--text-secondary);
    max-height: 240px;
    overflow: auto;
  }
  .router-table .unit-name {
    font-weight: 600;
    font-family: var(--font-mono, monospace);
  }
  .router-table .unit-desc {
    color: var(--text-secondary);
    font-size: 0.875rem;
  }
  .session-row .session-key { font-family: var(--font-mono, monospace); }
  .empty {
    text-align: center;
    padding: 1.5rem 1rem;
    color: var(--text-muted);
    font-size: 0.875rem;
  }
</style>

<div class="router-page">
  <div class="card">
    <div class="card-header">
      <h3 style="margin:0;">ルート可能ユニット</h3>
      <button class="btn btn-sm" id="r-reload">再読込</button>
    </div>
    <div class="router-help" style="margin: 0.4rem 0 0.6rem;">
      チャット入力を受け取った <code>UnitRouter</code> が LLM に提示する候補一覧です。
      <code>CHAT_ROUTABLE = False</code> のユニットは候補から除外されます。
    </div>
    <div class="table-wrap">
      <table class="table-responsive router-table">
        <thead>
          <tr>
            <th>名前</th>
            <th>説明（ルーター用）</th>
            <th>委託</th>
            <th>Breaker</th>
          </tr>
        </thead>
        <tbody id="r-routable-body">
          <tr><td colspan="4" class="empty">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <h3 style="margin:0;">候補から除外されているユニット</h3>
    </div>
    <div class="router-help" style="margin: 0.4rem 0 0.6rem;">
      自動通知やバックグラウンド処理など、チャット指示で呼び出さないユニット。
    </div>
    <div class="table-wrap">
      <table class="table-responsive router-table">
        <thead>
          <tr>
            <th>名前</th>
            <th>説明</th>
            <th>委託</th>
            <th>Breaker</th>
          </tr>
        </thead>
        <tbody id="r-excluded-body">
          <tr><td colspan="4" class="empty">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <h3 style="margin:0;">継続セッション</h3>
      <span class="router-help" id="r-session-meta"></span>
    </div>
    <div class="router-help" style="margin: 0.4rem 0 0.6rem;">
      チャネル単位で「直前に選ばれたユニット」を保持し、短い続き発話（「うん」「もう一回」など）を
      同じユニットへ流します。タイムアウト経過で自動失効します。
    </div>
    <div class="table-wrap">
      <table class="table-responsive">
        <thead>
          <tr>
            <th>セッションキー</th>
            <th>ユニット</th>
            <th>経過</th>
            <th>残り</th>
          </tr>
        </thead>
        <tbody id="r-sessions-body">
          <tr><td colspan="4" class="empty">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <h3 style="margin:0;">LLM プロンプト（候補一覧部分）</h3>
    </div>
    <div class="router-help" style="margin: 0.4rem 0 0.6rem;">
      実際にユニットルーターが LLM へ渡す「## ユニット一覧」の内容を再現したものです。
    </div>
    <pre class="router-prompt" id="r-prompt">Loading...</pre>
  </div>
</div>`;
}

function fmtAge(sec) {
  if (sec == null) return '-';
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m${s.toString().padStart(2, '0')}s`;
}

async function refresh() {
  let data;
  try {
    data = await api('/api/router/info');
  } catch (err) {
    $('r-routable-body').innerHTML = `<tr><td colspan="4" class="empty" style="color:var(--error)">Failed: ${esc(err.message || err)}</td></tr>`;
    $('r-excluded-body').innerHTML = '';
    $('r-sessions-body').innerHTML = '';
    $('r-prompt').textContent = '(load failed)';
    return;
  }

  const routable = data.routable || [];
  const excluded = data.excluded || [];
  const sessions = data.sessions || [];
  const timeout = data.session_timeout_sec || 0;

  if (routable.length === 0) {
    $('r-routable-body').innerHTML = '<tr><td colspan="4" class="empty">No routable units</td></tr>';
  } else {
    $('r-routable-body').innerHTML = routable.map(u => `
      <tr>
        <td data-label="名前" class="unit-name">${esc(u.name)}</td>
        <td data-label="説明" class="unit-desc">${esc(u.description || '-')}</td>
        <td data-label="委託">${delegateBadge(u.delegate_to)}</td>
        <td data-label="Breaker">${breakerBadge(u.breaker_state)}</td>
      </tr>
    `).join('');
  }

  if (excluded.length === 0) {
    $('r-excluded-body').innerHTML = '<tr><td colspan="4" class="empty">なし</td></tr>';
  } else {
    $('r-excluded-body').innerHTML = excluded.map(u => `
      <tr>
        <td data-label="名前" class="unit-name">${esc(u.name)}</td>
        <td data-label="説明" class="unit-desc">${esc(u.description || '-')}</td>
        <td data-label="委託">${delegateBadge(u.delegate_to)}</td>
        <td data-label="Breaker">${breakerBadge(u.breaker_state)}</td>
      </tr>
    `).join('');
  }

  $('r-session-meta').textContent = timeout ? `タイムアウト: ${timeout}s` : '';
  if (sessions.length === 0) {
    $('r-sessions-body').innerHTML = '<tr><td colspan="4" class="empty">継続中のセッションはありません</td></tr>';
  } else {
    $('r-sessions-body').innerHTML = sessions.map(s => `
      <tr class="session-row">
        <td data-label="キー" class="session-key">${esc(s.session_key)}</td>
        <td data-label="ユニット" class="unit-name">${esc(s.unit)}</td>
        <td data-label="経過">${fmtAge(s.age_sec)}</td>
        <td data-label="残り">${fmtAge(s.expires_in_sec)}</td>
      </tr>
    `).join('');
  }

  const promptText = routable
    .map(u => `- ${u.name}: ${u.description || ''}`)
    .join('\n') || '(なし)';
  $('r-prompt').textContent = promptText;
}

export async function mount() {
  await refresh();
  $('r-reload')?.addEventListener('click', refresh);
  _refreshTimer = setInterval(refresh, 15000);
}

export function unmount() {
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}
