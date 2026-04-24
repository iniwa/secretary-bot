/** Jobs page — 投入済みジョブの一覧・SSE 自動更新・Cancel / 再実行。 */
import { toast } from '../lib/toast.js';
import { GenerationAPI } from '../lib/generation_api.js';
import {
  esc, fmtTime, statusBadgeClass, isTerminal, stashSet,
} from '../lib/common.js';

// ============================================================
// State
// ============================================================
const PAGE_SIZE = 200;
let jobs = [];
let sse = null;
let pollTimer = null;
let filterStatus = '';
let hasMore = false;

function $(id) { return document.getElementById(id); }

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<section class="card imggen-section">
  <div class="imggen-header">
    <h3>Jobs</h3>
    <div style="display:flex;gap:0.4rem;align-items:center;">
      <select id="ij-filter" class="form-input" style="width:auto;font-size:0.75rem;padding:0.2rem 0.4rem;">
        <option value="">すべて</option>
        <option value="queued">queued</option>
        <option value="running">running</option>
        <option value="done">done</option>
        <option value="failed">failed</option>
        <option value="cancelled">cancelled</option>
      </select>
      <button id="ij-reload" class="btn btn-sm">再読込</button>
      <button id="ij-purge" class="btn btn-sm btn-danger" title="終端ステータス（done/failed/cancelled）のジョブを DB から削除します。NAS 上の画像ファイルは残ります。">過去Jobをクリア</button>
    </div>
  </div>
  <div id="ij-body">
    <div class="imggen-empty">Loading...</div>
  </div>
  <div id="ij-more-wrap" style="display:none;text-align:center;margin-top:0.6rem;">
    <button id="ij-more" class="btn btn-sm">もっと読み込む</button>
  </div>
</section>
`;
}

// ============================================================
// Jobs
// ============================================================
function renderJobs() {
  const el = $('ij-body');
  if (!el) return;
  const view = filterStatus ? jobs.filter(j => j.status === filterStatus) : jobs;
  if (!view.length) {
    el.innerHTML = '<div class="imggen-empty">No jobs</div>';
    return;
  }
  el.innerHTML = view.map(j => {
    const badge = statusBadgeClass(j.status);
    const prog = Math.max(0, Math.min(100, Number(j.progress) || 0));
    const canCancel = !isTerminal(j.status);
    const err = j.last_error ? `<div class="imggen-err">${esc(j.last_error)}</div>` : '';
    const isDone = j.status === 'done';
    const actionBtns = [];
    if (canCancel) {
      actionBtns.push(`<button class="btn btn-sm btn-danger" data-cancel="${esc(j.job_id)}">Cancel</button>`);
    }
    if (isDone) {
      actionBtns.push(`<button class="btn btn-sm" data-gallery="${esc(j.job_id)}">Gallery</button>`);
    }
    actionBtns.push(`<button class="btn btn-sm" data-reuse="${esc(j.job_id)}" title="このジョブの設定で生成フォームを埋める">再現</button>`);
    const pos = j.positive ? esc((j.positive || '').slice(0, 80)) : '';
    return `
      <div class="imggen-job-row" data-jid="${esc(j.job_id)}">
        <div>
          <span class="badge ${badge}">${esc(j.status)}</span>
          <div class="imggen-job-id">${esc((j.job_id || '').slice(0, 12))}</div>
        </div>
        <div>
          <div class="text-xs">${esc(j.workflow_name || '-')}${j.modality && j.modality !== 'image' ? ` <span class="tag">${esc(j.modality)}</span>` : ''}</div>
          ${pos ? `<div class="text-xs text-muted" style="word-break:break-all;">${pos}</div>` : ''}
          <div class="imggen-progress"><div class="imggen-progress-bar" style="width:${prog}%"></div></div>
          ${err}
          <div class="text-xs text-muted" style="margin-top:0.15rem;">${fmtTime(j.finished_at || j.created_at)}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:0.2rem;">
          ${actionBtns.join('')}
        </div>
      </div>`;
  }).join('');
  el.onclick = (e) => {
    const cancel = e.target.closest('button[data-cancel]');
    const gallery = e.target.closest('button[data-gallery]');
    const reuse = e.target.closest('button[data-reuse]');
    if (cancel) return handleCancel(cancel.dataset.cancel);
    if (gallery) {
      location.hash = `#/gallery?job=${encodeURIComponent(gallery.dataset.gallery)}`;
      return;
    }
    if (reuse) return handleReuse(reuse.dataset.reuse);
  };
}

function updateMoreButton() {
  const wrap = $('ij-more-wrap');
  if (!wrap) return;
  wrap.style.display = hasMore ? '' : 'none';
}

async function loadJobs() {
  try {
    const data = await GenerationAPI.listJobs({ limit: PAGE_SIZE });
    jobs = data?.jobs || [];
    hasMore = jobs.length >= PAGE_SIZE;
    renderJobs();
    updateMoreButton();
  } catch (err) {
    console.error('jobs load failed', err);
    const el = $('ij-body');
    if (el) el.innerHTML = '<div class="imggen-empty">取得失敗</div>';
  }
}

async function loadMore() {
  if (!hasMore) return;
  const btn = $('ij-more');
  if (btn) btn.disabled = true;
  try {
    const data = await GenerationAPI.listJobs({
      limit: PAGE_SIZE, offset: jobs.length,
    });
    const more = data?.jobs || [];
    // job_id 重複を避けつつ末尾に追加（SSE で既に追加されているケース対策）
    const existing = new Set(jobs.map(j => j.job_id));
    for (const j of more) {
      if (!existing.has(j.job_id)) jobs.push(j);
    }
    hasMore = more.length >= PAGE_SIZE;
    renderJobs();
    updateMoreButton();
  } catch (err) {
    console.error('load more failed', err);
    toast('追加読み込みに失敗', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handlePurge() {
  const ok = confirm(
    '終端ジョブ（done / failed / cancelled）を DB から削除します。\n'
    + '・NAS 上の画像ファイルは残ります\n'
    + '・done を削除するとギャラリーの索引からも消えます\n\n'
    + '続行しますか？',
  );
  if (!ok) return;
  const btn = $('ij-purge');
  if (btn) btn.disabled = true;
  try {
    const res = await GenerationAPI.purgeJobs({
      statuses: ['done', 'failed', 'cancelled'],
    });
    toast(`${res?.deleted ?? 0} 件の過去 Job を削除しました`, 'info');
    await loadJobs();
  } catch (err) {
    console.error('purge failed', err);
    toast('クリア失敗', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleCancel(jobId) {
  if (!confirm('このジョブをキャンセルしますか？')) return;
  try {
    await GenerationAPI.cancelJob(jobId);
    toast('Cancel リクエストを送信', 'info');
    await loadJobs();
  } catch (err) {
    console.error('cancel failed', err);
    toast('Cancel 失敗', 'error');
  }
}

async function handleReuse(jobId) {
  try {
    const job = await GenerationAPI.getJob(jobId);
    if (!job) { toast('ジョブが見つかりません', 'error'); return; }
    stashSet({
      source: 'job',
      job_id: jobId,
      workflow_name: job.workflow_name,
      positive: job.positive,
      negative: job.negative,
      params: job.params || {},
      modality: job.modality || 'image',
    });
    location.hash = '#/generate?prefill=job';
    toast('生成フォームに取り込みました', 'info');
  } catch (err) {
    console.error('reuse failed', err);
    toast('取り込み失敗', 'error');
  }
}

// ============================================================
// SSE
// ============================================================
function connectSSE() {
  if (sse) try { sse.close(); } catch { /* nop */ }
  sse = new EventSource('/api/generation/jobs/stream');
  sse.onmessage = (ev) => {
    try {
      const evt = JSON.parse(ev.data);
      const jid = evt.job_id;
      if (!jid) return;
      const idx = jobs.findIndex(j => j.job_id === jid);
      if (idx < 0) {
        loadJobs();
        return;
      }
      const j = jobs[idx];
      if (evt.status) j.status = evt.status;
      if (typeof evt.progress === 'number') j.progress = evt.progress;
      if (evt.event === 'error' && evt.detail?.error) j.last_error = evt.detail.error;
      if (evt.event === 'result' || evt.status === 'done') {
        j.status = 'done';
      }
      renderJobs();
    } catch { /* ignore */ }
  };
  sse.onerror = () => {
    try { sse.close(); } catch { /* nop */ }
    sse = null;
    setTimeout(connectSSE, 3000);
  };
}

// ============================================================
// Mount / Show / Hide
// ============================================================
export async function mount() {
  $('ij-reload')?.addEventListener('click', loadJobs);
  $('ij-purge')?.addEventListener('click', handlePurge);
  $('ij-more')?.addEventListener('click', loadMore);
  const sel = $('ij-filter');
  if (sel) {
    sel.addEventListener('change', () => {
      filterStatus = sel.value || '';
      renderJobs();
    });
  }
  await loadJobs();
}

export function onShow() {
  // 表示中のみ SSE と定期ポーリングを動かす
  if (!sse) connectSSE();
  if (!pollTimer) {
    pollTimer = setInterval(loadJobs, 15000);
    loadJobs();  // 戻ってきた瞬間に最新を 1 回叩く
  }
}

export function onHide() {
  if (sse) { try { sse.close(); } catch { /* nop */ } sse = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}
