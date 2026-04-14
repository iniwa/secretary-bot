/** 連続解析ワークベンチ（メイン画面） */
import { api, sseConnect } from '../api.js';
import { toast, openModal, escapeHtml, confirmDialog } from '../app.js';

let state = {
  jobs: new Map(),       // id -> job
  masters: null,         // { characters, sets }
  sse: null,
  capturing: false,
  selected: new Set(),   // 一括保存用
};

const STATUS_LABEL = {
  queued: 'QUEUED',
  capturing: 'CAPTURING',
  extracting: 'EXTRACTING',
  ready: 'READY',
  saved: 'SAVED',
  failed: 'FAILED',
};

const SLOT_LABEL = { 1: '1（攻）', 2: '2（体）', 3: '3（効）', 4: '4（主）', 5: '5（副）', 6: '6（特）' };

export function render() {
  return `
    <div class="capture-layout">
      <div class="capture-main">
        <button class="capture-btn" id="capture-btn">🎯 今の画面を解析</button>
        <div class="capture-hint">
          <span class="capture-kbd">Space</span> キーでも起動できます
        </div>
        <div class="capture-hint mt-1" id="capture-sub-hint">ゲーム画面でディスク詳細を表示した状態で押してください</div>
      </div>

      <aside class="queue-panel">
        <div class="queue-header">
          <h3>ジョブキュー</h3>
          <span id="stream-dot" class="queue-stream-dot off" title="SSE接続状態"></span>
        </div>
        <div class="queue-actions">
          <button class="btn btn-sm" id="select-all-ready">全readyを選択</button>
          <button class="btn btn-sm btn-primary" id="bulk-save" disabled>選択を一括保存</button>
          <div class="flex-1"></div>
          <button class="btn btn-sm btn-ghost" id="refresh-jobs" title="手動更新">↻</button>
        </div>
        <div class="queue-list" id="queue-list">
          <div class="queue-empty">ジョブはまだありません</div>
        </div>
      </aside>
    </div>
  `;
}

export async function mount() {
  // master data（新APIに合わせて分割呼び出し）
  try {
    const [sets, chars] = await Promise.all([
      api('/sets').catch(() => []),
      api('/characters').catch(() => []),
    ]);
    state.masters = {
      sets: Array.isArray(sets) ? sets : (sets?.sets || []),
      characters: Array.isArray(chars) ? chars : (chars?.characters || []),
    };
  } catch (err) {
    toast('マスタ取得に失敗しました', 'error');
    state.masters = { characters: [], sets: [] };
  }

  // 初期ジョブ読み込み
  await refreshJobs();

  // SSE 接続
  connectSSE();

  // イベント
  document.getElementById('capture-btn').addEventListener('click', doCapture);
  document.getElementById('refresh-jobs').addEventListener('click', refreshJobs);
  document.getElementById('select-all-ready').addEventListener('click', selectAllReady);
  document.getElementById('bulk-save').addEventListener('click', bulkSave);

  // ホットキー
  document.addEventListener('keydown', onKeydown);
}

export function unmount() {
  if (state.sse) {
    try { state.sse.close(); } catch {}
    state.sse = null;
  }
  document.removeEventListener('keydown', onKeydown);
  state.jobs.clear();
  state.selected.clear();
}

function onKeydown(e) {
  if (e.code === 'Space' && !isTypingTarget(e.target) && !document.querySelector('.modal-backdrop')) {
    e.preventDefault();
    doCapture();
  }
}

function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

async function doCapture() {
  if (state.capturing) return;
  const btn = document.getElementById('capture-btn');
  state.capturing = true;
  btn.disabled = true;
  btn.textContent = '📸 送信中...';
  try {
    const job = await api('/jobs/capture', { method: 'POST', body: {} });
    if (job && job.id != null) {
      state.jobs.set(job.id, job);
      renderJobs();
      toast(`ジョブ #${job.id} を投入しました`, 'success');
    }
  } catch (err) {
    toast(`キャプチャ失敗: ${err.message}`, 'error');
  } finally {
    state.capturing = false;
    btn.disabled = false;
    btn.textContent = '🎯 今の画面を解析';
  }
}

async function refreshJobs() {
  try {
    const list = await api('/jobs', { params: { status: 'queued,capturing,extracting,ready,failed' } });
    const jobs = Array.isArray(list) ? list : (list?.jobs || []);
    state.jobs.clear();
    for (const j of jobs) state.jobs.set(j.id, j);
    renderJobs();
  } catch (err) {
    toast(`ジョブ一覧取得失敗: ${err.message}`, 'error');
  }
}

function connectSSE() {
  if (state.sse) return;
  const dot = document.getElementById('stream-dot');
  state.sse = sseConnect('/jobs/stream', {
    onOpen: () => dot?.classList.remove('off'),
    onError: () => dot?.classList.add('off'),
    onMessage: (data) => {
      if (!data || typeof data !== 'object') return;
      // event: { type: 'job', job: {...} } or job 直接
      const job = data.job || data;
      if (job && job.id != null) {
        state.jobs.set(job.id, job);
        // saved は一覧から消す（チラつき抑制のため少し遅延）
        if (job.status === 'saved') {
          setTimeout(() => {
            state.jobs.delete(job.id);
            state.selected.delete(job.id);
            renderJobs();
          }, 800);
        }
        renderJobs();
      }
    },
  });
}

function renderJobs() {
  const list = document.getElementById('queue-list');
  if (!list) return;
  const jobs = Array.from(state.jobs.values()).sort((a, b) => b.id - a.id);
  if (!jobs.length) {
    list.innerHTML = '<div class="queue-empty">ジョブはまだありません</div>';
    document.getElementById('bulk-save').disabled = true;
    return;
  }
  list.innerHTML = jobs.map(j => jobItemHtml(j)).join('');
  // clicks
  list.querySelectorAll('.queue-item').forEach(el => {
    const id = Number(el.dataset.id);
    el.addEventListener('click', (e) => {
      if (e.target.matches('input[type="checkbox"]')) return;
      const job = state.jobs.get(id);
      if (!job) return;
      if (job.status === 'ready') openJobModal(job);
      else if (job.status === 'failed') openFailedModal(job);
    });
    const cb = el.querySelector('input[type="checkbox"]');
    if (cb) cb.addEventListener('change', () => {
      if (cb.checked) state.selected.add(id);
      else state.selected.delete(id);
      updateBulkButton();
    });
  });
  updateBulkButton();
}

function jobItemHtml(job) {
  const canSelect = job.status === 'ready';
  const checked = state.selected.has(job.id);
  const summary = summarizeJob(job);
  return `
    <div class="queue-item status-${escapeHtml(job.status)}" data-id="${job.id}">
      ${canSelect
        ? `<input type="checkbox" ${checked ? 'checked' : ''} />`
        : `<span style="width:14px;"></span>`}
      <div class="meta">
        <div class="id">#${job.id}</div>
        <div class="summary">${escapeHtml(summary)}</div>
      </div>
      <span class="status">${STATUS_LABEL[job.status] || job.status}</span>
    </div>
  `;
}

function summarizeJob(job) {
  if (job.status === 'failed') return job.error_message || 'エラー';
  const nz = parseJSON(job.normalized_json) || parseJSON(job.extracted_json);
  if (!nz) return '解析中...';
  const parts = [];
  if (nz.slot) parts.push(`部位${nz.slot}`);
  if (nz.set_name) parts.push(nz.set_name);
  if (nz.main_stat?.name) parts.push(`${nz.main_stat.name}`);
  return parts.length ? parts.join(' / ') : '解析中...';
}

function parseJSON(s) {
  if (!s) return null;
  if (typeof s === 'object') return s;
  try { return JSON.parse(s); } catch { return null; }
}

function selectAllReady() {
  state.selected.clear();
  for (const job of state.jobs.values()) {
    if (job.status === 'ready') state.selected.add(job.id);
  }
  renderJobs();
}

function updateBulkButton() {
  const btn = document.getElementById('bulk-save');
  if (!btn) return;
  btn.disabled = state.selected.size === 0;
  btn.textContent = state.selected.size
    ? `選択を一括保存 (${state.selected.size})`
    : '選択を一括保存';
}

async function bulkSave() {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  const ok = await confirmDialog(`${ids.length}件のジョブを編集なしで保存します。よろしいですか？`);
  if (!ok) return;
  let success = 0, failed = 0;
  await Promise.all(ids.map(async (id) => {
    const job = state.jobs.get(id);
    if (!job) return;
    const payload = parseJSON(job.normalized_json) || parseJSON(job.extracted_json) || {};
    try {
      await api(`/jobs/${id}/confirm`, { method: 'POST', body: payload });
      success++;
      state.selected.delete(id);
    } catch (err) {
      console.error(err);
      failed++;
    }
  }));
  toast(`保存 ${success}件 / 失敗 ${failed}件`, failed ? 'warning' : 'success');
  await refreshJobs();
}

// ============================================================
// Ready ジョブのレビューモーダル
// ============================================================
function openJobModal(job) {
  const data = parseJSON(job.normalized_json) || parseJSON(job.extracted_json) || {};
  const { bodyEl, footerEl, close } = openModal({
    title: `ジョブ #${job.id} を確認`,
  });
  bodyEl.appendChild(buildJobForm(data));
  footerEl.innerHTML = `
    <button class="btn btn-danger" data-act="discard">破棄</button>
    <div class="flex-1"></div>
    <button class="btn" data-act="cancel">キャンセル</button>
    <button class="btn btn-primary" data-act="save">保存</button>
  `;

  footerEl.querySelector('[data-act="cancel"]').addEventListener('click', close);
  footerEl.querySelector('[data-act="discard"]').addEventListener('click', async () => {
    const ok = await confirmDialog(`ジョブ #${job.id} を破棄します。よろしいですか？`);
    if (!ok) return;
    try {
      await api(`/jobs/${job.id}`, { method: 'DELETE' });
      state.jobs.delete(job.id);
      state.selected.delete(job.id);
      renderJobs();
      toast('破棄しました', 'info');
      close();
    } catch (err) {
      toast(`破棄失敗: ${err.message}`, 'error');
    }
  });
  footerEl.querySelector('[data-act="save"]').addEventListener('click', async () => {
    const payload = readJobForm(bodyEl);
    try {
      await api(`/jobs/${job.id}/confirm`, { method: 'POST', body: payload });
      state.jobs.delete(job.id);
      state.selected.delete(job.id);
      renderJobs();
      toast('保存しました', 'success');
      close();
    } catch (err) {
      toast(`保存失敗: ${err.message}`, 'error');
    }
  });
}

function buildJobForm(data) {
  const root = document.createElement('div');
  const sets = state.masters?.sets || [];
  const chars = state.masters?.characters || [];
  const subs = Array.isArray(data.sub_stats) ? data.sub_stats : [];
  // 4件に揃える
  while (subs.length < 4) subs.push({ name: '', value: '', upgrades: 0 });

  const setOptions = sets.map(s =>
    `<option value="${s.id}" ${data.set_id === s.id || data.set_name === s.name_ja ? 'selected' : ''}>${escapeHtml(s.name_ja)}</option>`
  ).join('');
  const charOptions = chars.map(c =>
    `<option value="${c.id}">${escapeHtml(c.name_ja)}</option>`
  ).join('');

  root.innerHTML = `
    <div class="form-grid">
      <label>部位 (slot)</label>
      <select name="slot">
        ${[1,2,3,4,5,6].map(s => `<option value="${s}" ${Number(data.slot) === s ? 'selected' : ''}>${SLOT_LABEL[s]}</option>`).join('')}
      </select>

      <label>セット</label>
      <select name="set_id">
        <option value="">（未選択）</option>
        ${setOptions}
      </select>

      <label>メインステ名</label>
      <input name="main_stat_name" type="text" value="${escapeHtml(data.main_stat?.name || '')}" />

      <label>メインステ値</label>
      <input name="main_stat_value" type="number" step="0.01" value="${escapeHtml(data.main_stat?.value ?? '')}" />

      <label>紐付けキャラ（任意）</label>
      <select name="character_id">
        <option value="">（なし）</option>
        ${charOptions}
      </select>
    </div>

    <div class="mt-2"><strong>サブステ</strong></div>
    <div class="sub-stats-list mt-1" data-subs>
      ${subs.map((s, i) => `
        <div class="sub-stat-row" data-sub-index="${i}">
          <input name="sub_name" type="text" placeholder="名前" value="${escapeHtml(s.name || '')}" />
          <input name="sub_value" type="number" step="0.01" placeholder="値" value="${escapeHtml(s.value ?? '')}" />
          <input name="sub_upgrades" type="number" min="0" max="5" placeholder="強化" value="${Number(s.upgrades || 0)}" />
        </div>
      `).join('')}
    </div>
  `;
  return root;
}

function readJobForm(rootEl) {
  const q = (sel) => rootEl.querySelector(sel);
  const slot = Number(q('[name="slot"]').value);
  const setIdRaw = q('[name="set_id"]').value;
  const charIdRaw = q('[name="character_id"]').value;
  const mainName = q('[name="main_stat_name"]').value.trim();
  const mainValue = parseFloat(q('[name="main_stat_value"]').value);
  const subs = Array.from(rootEl.querySelectorAll('[data-sub-index]')).map(row => {
    const name = row.querySelector('[name="sub_name"]').value.trim();
    const valueRaw = row.querySelector('[name="sub_value"]').value;
    const upgradesRaw = row.querySelector('[name="sub_upgrades"]').value;
    if (!name) return null;
    return {
      name,
      value: valueRaw === '' ? 0 : parseFloat(valueRaw),
      upgrades: upgradesRaw === '' ? 0 : parseInt(upgradesRaw, 10),
    };
  }).filter(Boolean);
  return {
    slot,
    set_id: setIdRaw ? Number(setIdRaw) : null,
    main_stat: { name: mainName, value: isNaN(mainValue) ? 0 : mainValue },
    sub_stats: subs,
    character_id: charIdRaw ? Number(charIdRaw) : null,
  };
}

function openFailedModal(job) {
  const { bodyEl, footerEl, close } = openModal({
    title: `ジョブ #${job.id} — 失敗`,
    body: `<div class="card"><div class="text-muted text-sm mb-1">エラー</div><div>${escapeHtml(job.error_message || '原因不明')}</div></div>`,
  });
  footerEl.innerHTML = `
    <button class="btn btn-danger" data-act="delete">削除</button>
    <div class="flex-1"></div>
    <button class="btn" data-act="close2">閉じる</button>
  `;
  footerEl.querySelector('[data-act="close2"]').addEventListener('click', close);
  footerEl.querySelector('[data-act="delete"]').addEventListener('click', async () => {
    try {
      await api(`/jobs/${job.id}`, { method: 'DELETE' });
      state.jobs.delete(job.id);
      renderJobs();
      toast('削除しました', 'info');
      close();
    } catch (err) {
      toast(`削除失敗: ${err.message}`, 'error');
    }
  });
}
