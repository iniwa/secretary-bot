/** Kobo Watch page — 楽天 Kobo シリーズ新刊監視の一覧・追加・削除・即時チェック。 */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtDate(iso) {
  if (!iso) return '---';
  return String(iso).slice(0, 16).replace('T', ' ');
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .kw-page { display: flex; flex-direction: column; gap: 1rem; }
  .kw-toolbar {
    display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;
  }
  .kw-add {
    display: grid;
    grid-template-columns: minmax(140px, 1fr) minmax(180px, 2fr) auto auto;
    gap: 0.5rem;
    align-items: center;
  }
  .kw-add .form-input { width: 100%; }
  .kw-target-row .kw-title { font-weight: 600; }
  .kw-target-row .kw-sub  { color: var(--text-muted); font-size: 0.8125rem; }
  .kw-detections { font-size: 0.875rem; }
  .kw-detections li { padding: 0.3rem 0; border-bottom: 1px solid var(--border); }
  .kw-detections li:last-child { border-bottom: none; }
  .kw-label-kobo { color: var(--success); }
  .kw-label-paper { color: var(--text-muted); }
  .kw-empty {
    text-align: center; padding: 2rem 1rem;
    color: var(--text-muted); font-size: 0.875rem;
  }
  @media (max-width: 720px) {
    .kw-add { grid-template-columns: 1fr; }
  }
</style>

<div class="kw-page">
  <div class="card">
    <h3 style="margin-top:0;">新規登録</h3>
    <form class="kw-add" id="kw-add-form">
      <input type="text" class="form-input" id="kw-author" placeholder="著者（必須）" required>
      <input type="text" class="form-input" id="kw-title" placeholder="タイトルキーワード（任意）">
      <label style="display:flex;gap:0.3rem;align-items:center;white-space:nowrap;">
        <input type="checkbox" id="kw-kobo-only"> Kobo 版のみ通知
      </label>
      <button type="submit" class="btn btn-primary">登録</button>
    </form>
  </div>

  <div class="card">
    <div class="kw-toolbar">
      <h3 style="margin:0;flex:1;">監視中</h3>
      <button class="btn" id="kw-check-btn">今すぐチェック</button>
      <button class="btn" id="kw-reload-btn">再読込</button>
    </div>
    <div class="table-wrap" style="margin-top:0.5rem;">
      <table class="table-responsive">
        <thead>
          <tr>
            <th>#</th>
            <th>著者 / タイトル</th>
            <th>設定</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody id="kw-targets-tbody">
          <tr><td colspan="4" class="kw-empty">読み込み中...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h3 style="margin-top:0;">検出履歴（最新 50 件）</h3>
    <ul id="kw-detections" class="kw-detections">
      <li class="kw-empty">読み込み中...</li>
    </ul>
  </div>
</div>`;
}

// ============================================================
// Rendering
// ============================================================
function renderTargets(targets) {
  const tbody = $('kw-targets-tbody');
  if (!tbody) return;
  if (!targets || targets.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="kw-empty">まだ登録がないよ</td></tr>';
    return;
  }
  tbody.innerHTML = targets.map(t => {
    const kw = t.title_keyword ? esc(t.title_keyword) : '<span class="kw-sub">(タイトル指定なし)</span>';
    return `
      <tr class="kw-target-row" data-id="${t.id}">
        <td>#${t.id}</td>
        <td>
          <div class="kw-title">${esc(t.author)}</div>
          <div class="kw-sub">${kw}</div>
        </td>
        <td>
          <label style="display:block;">
            <input type="checkbox" data-action="toggle-enabled" ${t.enabled ? 'checked' : ''}>
            有効
          </label>
          <label style="display:block;">
            <input type="checkbox" data-action="toggle-kobo-only" ${t.notify_kobo_only ? 'checked' : ''}>
            Kobo 版のみ
          </label>
        </td>
        <td>
          <button class="btn btn-sm btn-danger" data-action="delete">削除</button>
        </td>
      </tr>
    `;
  }).join('');
}

function renderDetections(detections) {
  const ul = $('kw-detections');
  if (!ul) return;
  if (!detections || detections.length === 0) {
    ul.innerHTML = '<li class="kw-empty">検出履歴なし</li>';
    return;
  }
  ul.innerHTML = detections.map(d => {
    const koboLabel = d.kobo_available
      ? '<span class="kw-label-kobo">📱 Kobo版あり</span>'
      : '<span class="kw-label-paper">📕 紙のみ</span>';
    const notified = d.notified_at
      ? `✅ 通知済 ${esc(fmtDate(d.notified_at))}`
      : (d.suppressed_reason
          ? `⚠️ 抑制 (${esc(d.suppressed_reason)})`
          : '⏳ 未通知');
    const kobo = d.kobo_url ? ` · <a href="${esc(d.kobo_url)}" target="_blank" rel="noopener">Kobo</a>` : '';
    return `
      <li>
        <code>${esc(d.isbn)}</code> ${koboLabel} — ${notified}
        <span class="kw-sub"> · target #${d.target_id} · ${esc(fmtDate(d.created_at))}${kobo}</span>
      </li>
    `;
  }).join('');
}

// ============================================================
// Data loading
// ============================================================
async function loadAll() {
  try {
    const [targets, detections] = await Promise.all([
      api('/api/kobo-watch/targets'),
      api('/api/kobo-watch/detections', { params: { limit: 50 } }),
    ]);
    renderTargets(targets?.targets || []);
    renderDetections(detections?.detections || []);
  } catch (e) {
    console.error(e);
    toast(`データ取得失敗: ${e.message}`, 'error');
  }
}

// ============================================================
// Actions
// ============================================================
async function handleAdd(e) {
  e.preventDefault();
  const author = $('kw-author').value.trim();
  const title = $('kw-title').value.trim();
  const koboOnly = $('kw-kobo-only').checked;
  if (!author) {
    toast('著者は必須だよ', 'error');
    return;
  }
  try {
    const res = await api('/api/kobo-watch/targets', {
      method: 'POST',
      body: {
        author, title_keyword: title || null,
        notify_kobo_only: koboOnly,
      },
    });
    let msg = `登録したよ（既刊 ${res.backfilled} 件を既知として保存）`;
    if (res.backfill_error === 'no_credentials') {
      msg = '登録したよ（楽天 API キー未設定のため backfill スキップ）';
    } else if (res.backfill_error) {
      msg = `登録したよ（backfill 失敗: ${res.backfill_error}）`;
    }
    toast(msg, 'success');
    $('kw-add-form').reset();
    await loadAll();
  } catch (e) {
    toast(`登録失敗: ${e.message}`, 'error');
  }
}

async function handleTableAction(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const row = btn.closest('[data-id]');
  if (!row) return;
  const id = Number(row.dataset.id);
  const action = btn.dataset.action;

  if (action === 'delete') {
    if (!confirm('本当に削除する？既知ISBNや検出履歴も消えるよ。')) return;
    try {
      await api(`/api/kobo-watch/targets/${id}`, { method: 'DELETE' });
      toast('削除したよ', 'success');
      await loadAll();
    } catch (e) {
      toast(`削除失敗: ${e.message}`, 'error');
    }
  } else if (action === 'toggle-enabled') {
    try {
      await api(`/api/kobo-watch/targets/${id}`, {
        method: 'PATCH',
        body: { enabled: btn.checked },
      });
    } catch (e) {
      toast(`更新失敗: ${e.message}`, 'error');
      btn.checked = !btn.checked;
    }
  } else if (action === 'toggle-kobo-only') {
    try {
      await api(`/api/kobo-watch/targets/${id}`, {
        method: 'PATCH',
        body: { notify_kobo_only: btn.checked },
      });
    } catch (e) {
      toast(`更新失敗: ${e.message}`, 'error');
      btn.checked = !btn.checked;
    }
  }
}

async function handleCheckNow() {
  const btn = $('kw-check-btn');
  if (!btn) return;
  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = 'チェック中…';
  try {
    const res = await api('/api/kobo-watch/check-now', { method: 'POST' });
    toast(`新刊 ${res.detected} 件を検出`, 'success');
    await loadAll();
  } catch (e) {
    toast(`実行失敗: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

// ============================================================
// Lifecycle
// ============================================================
let boundHandlers = null;

export async function mount() {
  const form = $('kw-add-form');
  const tbody = $('kw-targets-tbody');
  const checkBtn = $('kw-check-btn');
  const reloadBtn = $('kw-reload-btn');

  if (form) form.addEventListener('submit', handleAdd);
  if (tbody) {
    tbody.addEventListener('click', handleTableAction);
    tbody.addEventListener('change', handleTableAction);
  }
  if (checkBtn) checkBtn.addEventListener('click', handleCheckNow);
  if (reloadBtn) reloadBtn.addEventListener('click', loadAll);

  boundHandlers = { form, tbody, checkBtn, reloadBtn };
  await loadAll();
}

export function unmount() {
  if (!boundHandlers) return;
  const { form, tbody, checkBtn, reloadBtn } = boundHandlers;
  if (form) form.removeEventListener('submit', handleAdd);
  if (tbody) {
    tbody.removeEventListener('click', handleTableAction);
    tbody.removeEventListener('change', handleTableAction);
  }
  if (checkBtn) checkBtn.removeEventListener('click', handleCheckNow);
  if (reloadBtn) reloadBtn.removeEventListener('click', loadAll);
  boundHandlers = null;
}
