/** Image Gen page — Generate / Jobs / Gallery の 3 セクション構成。 */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let workflows = [];
let jobs = [];
let gallery = [];
let sse = null;
let pollTimer = null;
let galleryTimer = null;

// Presets modal state
let presetModalState = {
  source: '',           // 'history:<agent_id>' | 'file'
  workflowJson: null,   // 現在編集中の workflow API format (dict)
  sourceLabel: '',
};

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtTime(iso) {
  if (!iso) return '---';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return esc(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function statusBadgeClass(status) {
  switch (status) {
    case 'done':          return 'badge-success';
    case 'running':       return 'badge-info';
    case 'warming_cache': return 'badge-info';
    case 'dispatching':   return 'badge-info';
    case 'queued':        return 'badge-accent';
    case 'failed':        return 'badge-danger';
    case 'cancelled':     return 'badge-muted';
    default:              return 'badge-muted';
  }
}

function isTerminal(s) {
  return s === 'done' || s === 'failed' || s === 'cancelled';
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .imggen-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 1rem;
  }
  @media (min-width: 1100px) {
    .imggen-grid {
      grid-template-columns: 380px 1fr;
      grid-template-areas:
        "gen   jobs"
        "gen   gallery";
    }
    .imggen-gen     { grid-area: gen; }
    .imggen-jobs    { grid-area: jobs; }
    .imggen-gallery { grid-area: gallery; }
  }
  .imggen-section h3 {
    margin: 0 0 0.6rem;
    font-size: 0.95rem;
    color: var(--text-primary);
  }
  .imggen-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin-bottom: 0.6rem;
  }
  .imggen-header h3 { margin: 0; }
  .imggen-comfy-panel {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    margin-bottom: 0.6rem;
  }
  .imggen-comfy-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.75rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--bg-base);
  }
  .imggen-comfy-row .name {
    font-weight: 600;
    color: var(--text-primary);
  }
  .imggen-comfy-row .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--text-muted, #888);
    flex-shrink: 0;
  }
  .imggen-comfy-row .dot.running { background: #22c55e; }
  .imggen-comfy-row .dot.starting { background: #eab308; }
  .imggen-comfy-row .dot.error { background: #ef4444; }
  .imggen-comfy-row .meta { color: var(--text-secondary); font-size: 0.7rem; }
  .imggen-comfy-row .spacer { flex: 1; }
  .imggen-comfy-row button,
  .imggen-comfy-row a {
    font-size: 0.7rem;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: var(--bg-base);
    color: var(--text-secondary);
    text-decoration: none;
    cursor: pointer;
  }
  .imggen-comfy-row button:hover:not(:disabled),
  .imggen-comfy-row a:hover {
    border-color: var(--accent);
    color: var(--accent);
  }
  .imggen-comfy-row button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .imggen-form label {
    display: block;
    font-size: 0.75rem;
    color: var(--text-secondary);
    margin: 0.5rem 0 0.2rem;
  }
  .imggen-form .form-input,
  .imggen-form textarea,
  .imggen-form select {
    width: 100%;
    box-sizing: border-box;
  }
  .imggen-form textarea {
    min-height: 80px;
    resize: vertical;
    font-family: inherit;
    font-size: 0.8125rem;
    padding: 0.4rem 0.55rem;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--bg-base);
    color: var(--text-primary);
  }
  .imggen-params {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.4rem 0.6rem;
    margin-top: 0.3rem;
  }
  .imggen-params .form-input { font-size: 0.8125rem; }
  .imggen-submit { margin-top: 0.8rem; width: 100%; }
  .imggen-status-line {
    margin-top: 0.6rem;
    font-size: 0.75rem;
    color: var(--text-secondary);
    word-break: break-all;
  }
  .imggen-job-row {
    display: grid;
    grid-template-columns: 110px 1fr auto;
    gap: 0.5rem;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.8125rem;
  }
  .imggen-job-row:last-child { border-bottom: none; }
  .imggen-job-id {
    font-family: var(--mono, ui-monospace, monospace);
    font-size: 0.7rem;
    color: var(--text-muted);
  }
  .imggen-progress {
    height: 4px;
    background: var(--bg-base);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 0.2rem;
  }
  .imggen-progress-bar {
    height: 100%;
    background: var(--accent);
    transition: width 0.2s ease;
  }
  .imggen-err {
    color: var(--danger, #d33);
    font-size: 0.7rem;
    margin-top: 0.2rem;
    word-break: break-word;
  }
  .imggen-gallery-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 0.4rem;
  }
  .imggen-gallery-item {
    aspect-ratio: 1 / 1;
    background: var(--bg-base);
    border-radius: 4px;
    overflow: hidden;
    cursor: pointer;
    border: 1px solid var(--border);
  }
  .imggen-gallery-item img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .imggen-lightbox {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    cursor: zoom-out;
  }
  .imggen-lightbox img {
    max-width: 95vw;
    max-height: 95vh;
    object-fit: contain;
  }
  .imggen-empty {
    text-align: center;
    padding: 1.5rem;
    color: var(--text-muted);
    font-size: 0.8125rem;
  }

  /* Presets */
  .imggen-presets-list {
    display: flex; flex-direction: column; gap: 0.3rem;
  }
  .imggen-preset-row {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 0.5rem;
    align-items: center;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 0.8rem;
    background: var(--bg-base);
  }
  .imggen-preset-row .meta { color: var(--text-secondary); font-size: 0.7rem; }
  .imggen-preset-row .tag {
    display: inline-block;
    padding: 0.05rem 0.4rem;
    border-radius: 2px;
    background: var(--bg-elev, #2a2a2a);
    font-size: 0.65rem;
    margin-right: 0.3rem;
    color: var(--text-secondary);
  }

  /* Modal */
  .imggen-modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000;
  }
  .imggen-modal {
    background: var(--bg-surface, #1d1d1d);
    border: 1px solid var(--border);
    border-radius: 8px;
    width: min(900px, 94vw);
    max-height: 90vh;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .imggen-modal-header {
    padding: 0.6rem 0.9rem;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    font-size: 0.95rem; color: var(--text-primary);
  }
  .imggen-modal-body {
    padding: 0.8rem 0.9rem;
    overflow-y: auto;
    display: flex; flex-direction: column; gap: 0.7rem;
  }
  .imggen-modal-footer {
    padding: 0.6rem 0.9rem;
    border-top: 1px solid var(--border);
    display: flex; gap: 0.5rem; justify-content: flex-end;
  }
  .imggen-source-tabs {
    display: flex; gap: 0.4rem; flex-wrap: wrap;
  }
  .imggen-source-tabs button {
    font-size: 0.75rem; padding: 0.25rem 0.6rem;
    border: 1px solid var(--border);
    background: var(--bg-base);
    color: var(--text-secondary);
    border-radius: 4px; cursor: pointer;
  }
  .imggen-source-tabs button.active {
    border-color: var(--accent);
    color: var(--accent);
  }
  .imggen-history-list {
    max-height: 220px; overflow-y: auto;
    border: 1px solid var(--border); border-radius: 4px;
    background: var(--bg-base);
  }
  .imggen-history-item {
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.75rem;
    cursor: pointer;
    display: flex; gap: 0.5rem; align-items: center;
  }
  .imggen-history-item:hover { background: var(--bg-elev, #2a2a2a); }
  .imggen-history-item.selected { border-left: 3px solid var(--accent); }
  .imggen-history-item .pid { color: var(--text-muted); font-family: var(--mono, monospace); font-size: 0.65rem; }

  .imggen-ph-table {
    width: 100%; border-collapse: collapse; font-size: 0.72rem;
  }
  .imggen-ph-table th, .imggen-ph-table td {
    border-bottom: 1px solid var(--border);
    padding: 0.25rem 0.4rem; text-align: left;
    vertical-align: top;
  }
  .imggen-ph-table th { color: var(--text-muted); font-weight: normal; font-size: 0.65rem; }
  .imggen-ph-table td.val { font-family: var(--mono, monospace); word-break: break-all; max-width: 260px; }
  .imggen-ph-table td.val .is-ph { color: var(--accent); }
  .imggen-ph-actions select {
    font-size: 0.7rem; padding: 0.1rem 0.3rem;
  }
  .imggen-meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 0.4rem 0.6rem;
  }
  .imggen-json-preview {
    width: 100%; box-sizing: border-box;
    min-height: 160px; max-height: 280px;
    font-family: var(--mono, monospace); font-size: 0.7rem;
    padding: 0.4rem; border-radius: 4px;
    border: 1px solid var(--border);
    background: var(--bg-base); color: var(--text-primary);
    resize: vertical;
  }
</style>

<div class="imggen-grid">
  <!-- Generate -->
  <section class="card imggen-section imggen-gen">
    <div class="imggen-header">
      <h3>Generate</h3>
    </div>
    <div id="ig-comfy-panel" class="imggen-comfy-panel"></div>
    <div class="imggen-form">
      <label for="ig-workflow">Workflow</label>
      <select id="ig-workflow" class="form-input"><option value="">Loading...</option></select>

      <label for="ig-positive">Positive prompt</label>
      <textarea id="ig-positive" placeholder="masterpiece, best quality, ..."></textarea>

      <label for="ig-negative">Negative prompt</label>
      <textarea id="ig-negative" placeholder="lowres, bad anatomy, ..."></textarea>

      <div class="imggen-params">
        <div>
          <label for="ig-width">Width</label>
          <input id="ig-width" class="form-input" type="number" min="64" step="8" placeholder="1024">
        </div>
        <div>
          <label for="ig-height">Height</label>
          <input id="ig-height" class="form-input" type="number" min="64" step="8" placeholder="1024">
        </div>
        <div>
          <label for="ig-steps">Steps</label>
          <input id="ig-steps" class="form-input" type="number" min="1" placeholder="30">
        </div>
        <div>
          <label for="ig-cfg">CFG</label>
          <input id="ig-cfg" class="form-input" type="number" step="0.1" placeholder="5.5">
        </div>
        <div>
          <label for="ig-seed">Seed (-1 random)</label>
          <input id="ig-seed" class="form-input" type="number" placeholder="-1">
        </div>
        <div>
          <label for="ig-sampler">Sampler</label>
          <input id="ig-sampler" class="form-input" type="text" placeholder="euler_ancestral">
        </div>
        <div>
          <label for="ig-scheduler">Scheduler</label>
          <input id="ig-scheduler" class="form-input" type="text" placeholder="normal">
        </div>
      </div>

      <button id="ig-submit" class="btn btn-primary imggen-submit">投入</button>
      <div id="ig-status" class="imggen-status-line"></div>
    </div>
  </section>

  <!-- Jobs -->
  <section class="card imggen-section imggen-jobs">
    <h3>Jobs</h3>
    <div id="ig-jobs-body">
      <div class="imggen-empty">Loading...</div>
    </div>
  </section>

  <!-- Gallery -->
  <section class="card imggen-section imggen-gallery">
    <h3>Gallery</h3>
    <div id="ig-gallery-body">
      <div class="imggen-empty">Loading...</div>
    </div>
  </section>
</div>

<!-- Presets (プリセット管理) -->
<section class="card imggen-section" style="margin-top:1rem;">
  <div class="imggen-header">
    <h3>Presets（プリセット管理）</h3>
    <button id="ig-preset-new" class="btn btn-sm btn-primary">新規登録</button>
  </div>
  <div id="ig-presets-body">
    <div class="imggen-empty">Loading...</div>
  </div>
</section>

<div id="ig-preset-modal-root"></div>
`;
}

// ============================================================
// Workflows
// ============================================================
async function loadWorkflows() {
  const sel = $('ig-workflow');
  if (!sel) return;
  try {
    const data = await api('/api/image/workflows');
    workflows = data?.workflows || [];
    if (workflows.length === 0) {
      sel.innerHTML = '<option value="">(no workflows)</option>';
      return;
    }
    sel.innerHTML = workflows.map(w => {
      const label = `${w.name}${w.description ? ' — ' + w.description : ''}${w.main_pc_only ? ' [main]' : ''}`;
      return `<option value="${esc(w.name)}">${esc(label)}</option>`;
    }).join('');
  } catch (err) {
    console.error('workflows load failed', err);
    sel.innerHTML = '<option value="">(load failed)</option>';
  }
}

// ============================================================
// ComfyUI control panel (status / start / stop / open)
// ============================================================
let comfyAgents = [];
let comfyStatusTimer = null;
const comfyBusy = new Set();  // agent_id 単位の操作中フラグ

async function loadComfyPanel() {
  try {
    const data = await api('/api/image/agents');
    comfyAgents = data?.agents || [];
  } catch (err) {
    console.error('agents load failed', err);
    comfyAgents = [];
  }
  renderComfyPanel({});
  refreshComfyStatus();
}

function renderComfyPanel(statusMap) {
  const el = $('ig-comfy-panel');
  if (!el) return;
  if (!comfyAgents.length) { el.innerHTML = ''; return; }
  el.innerHTML = comfyAgents.map(a => {
    const st = statusMap[a.id] || { loading: true };
    let dotClass = '';
    let statusLabel = '読み込み中...';
    let pidPart = '';
    if (!st.loading) {
      if (st.unreachable) {
        dotClass = 'error';
        statusLabel = 'Agent 応答なし';
      } else if (st.available) {
        dotClass = 'running';
        statusLabel = '稼働中';
        if (st.pid) pidPart = ` (PID ${st.pid})`;
      } else if (st.running) {
        dotClass = 'starting';
        statusLabel = '起動中 / 応答待ち';
      } else {
        statusLabel = '停止';
      }
    }
    const busy = comfyBusy.has(a.id);
    const isUp = !st.loading && (st.running || st.available);
    const actionBtn = isUp
      ? `<button data-comfy-action="stop" data-agent="${esc(a.id)}" ${busy ? 'disabled' : ''}>停止</button>`
      : `<button data-comfy-action="start" data-agent="${esc(a.id)}" ${busy ? 'disabled' : ''}>起動</button>`;
    return `
      <div class="imggen-comfy-row">
        <span class="dot ${dotClass}"></span>
        <span class="name">${esc(a.name || a.id)}</span>
        <span class="meta">${esc(statusLabel)}${esc(pidPart)}</span>
        <span class="spacer"></span>
        ${actionBtn}
        <a href="${esc(a.comfyui_url)}" target="_blank" rel="noopener" title="${esc(a.comfyui_url)}">開く</a>
      </div>
    `;
  }).join('');
  // バインド
  el.querySelectorAll('button[data-comfy-action]').forEach(btn => {
    btn.addEventListener('click', () => handleComfyAction(btn.dataset.agent, btn.dataset.comfyAction));
  });
}

async function refreshComfyStatus() {
  if (!comfyAgents.length) return;
  const results = await Promise.all(comfyAgents.map(async a => {
    try {
      const s = await api(`/api/image/agents/${encodeURIComponent(a.id)}/comfyui/status`);
      return [a.id, s];
    } catch (err) {
      return [a.id, { unreachable: true }];
    }
  }));
  const map = {};
  results.forEach(([id, s]) => { map[id] = s; });
  renderComfyPanel(map);
}

async function handleComfyAction(agentId, action) {
  if (!agentId || !action) return;
  if (comfyBusy.has(agentId)) return;
  comfyBusy.add(agentId);
  refreshComfyStatus();
  try {
    await api(`/api/image/agents/${encodeURIComponent(agentId)}/comfyui/${action}`, { method: 'POST' });
    toast(`${action === 'start' ? '起動' : '停止'}リクエストを送信しました`, 'info');
  } catch (err) {
    toast(`ComfyUI ${action} 失敗: ${err?.message || err}`, 'error');
  } finally {
    comfyBusy.delete(agentId);
    refreshComfyStatus();
  }
}

// ============================================================
// Jobs
// ============================================================
function renderJobs() {
  const el = $('ig-jobs-body');
  if (!el) return;
  if (!jobs || jobs.length === 0) {
    el.innerHTML = '<div class="imggen-empty">No jobs yet</div>';
    return;
  }
  el.innerHTML = jobs.map(j => {
    const badge = statusBadgeClass(j.status);
    const prog = Math.max(0, Math.min(100, Number(j.progress) || 0));
    const canCancel = !isTerminal(j.status);
    const err = j.last_error ? `<div class="imggen-err">${esc(j.last_error)}</div>` : '';
    return `
      <div class="imggen-job-row" data-jid="${esc(j.job_id)}">
        <div>
          <span class="badge ${badge}">${esc(j.status)}</span>
          <div class="imggen-job-id">${esc((j.job_id || '').slice(0, 12))}</div>
        </div>
        <div>
          <div class="text-xs">${esc(j.workflow_name || '-')}</div>
          <div class="imggen-progress"><div class="imggen-progress-bar" style="width:${prog}%"></div></div>
          ${err}
        </div>
        <div>
          ${canCancel
            ? `<button class="btn btn-sm btn-danger" data-cancel="${esc(j.job_id)}">Cancel</button>`
            : `<span class="text-xs text-muted">${fmtTime(j.finished_at || j.created_at)}</span>`}
        </div>
      </div>`;
  }).join('');
  el.onclick = (e) => {
    const btn = e.target.closest('button[data-cancel]');
    if (btn) handleCancel(btn.dataset.cancel);
  };
}

async function loadJobs() {
  try {
    const data = await api('/api/image/jobs', { params: { limit: 30 } });
    jobs = data?.jobs || [];
    renderJobs();
  } catch (err) {
    console.error('jobs load failed', err);
  }
}

async function handleCancel(jobId) {
  if (!confirm('Cancel this job?')) return;
  try {
    await api(`/api/image/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
    toast('Cancel requested', 'info');
    await loadJobs();
  } catch (err) {
    console.error('cancel failed', err);
    toast('Cancel failed', 'error');
  }
}

// ============================================================
// SSE
// ============================================================
function connectSSE() {
  if (sse) sse.close();
  sse = new EventSource('/api/image/jobs/stream');
  sse.onmessage = (ev) => {
    try {
      const evt = JSON.parse(ev.data);
      const jid = evt.job_id;
      if (!jid) return;
      const idx = jobs.findIndex(j => j.job_id === jid);
      if (idx < 0) {
        // 新規 or 未ロード → 再取得
        loadJobs();
        return;
      }
      // 差分反映
      const j = jobs[idx];
      if (evt.status) j.status = evt.status;
      if (typeof evt.progress === 'number') j.progress = evt.progress;
      if (evt.event === 'error' && evt.detail?.error) j.last_error = evt.detail.error;
      if (evt.event === 'result' || evt.status === 'done') {
        // done なら gallery も更新
        setTimeout(loadGallery, 500);
      }
      renderJobs();
    } catch (e) { /* ignore */ }
  };
  sse.onerror = () => {
    try { sse.close(); } catch { /* nop */ }
    sse = null;
    setTimeout(connectSSE, 3000);
  };
}

// ============================================================
// Gallery
// ============================================================
function renderGallery() {
  const el = $('ig-gallery-body');
  if (!el) return;
  if (!gallery || gallery.length === 0) {
    el.innerHTML = '<div class="imggen-empty">No images yet</div>';
    return;
  }
  el.innerHTML = `<div class="imggen-gallery-grid">${gallery.map((g, i) => `
    <div class="imggen-gallery-item" data-idx="${i}">
      <img loading="lazy" src="${esc(g.thumb_url)}" alt="">
    </div>`).join('')}</div>`;
  el.onclick = (e) => {
    const item = e.target.closest('[data-idx]');
    if (!item) return;
    const g = gallery[Number(item.dataset.idx)];
    if (g) openLightbox(g.url);
  };
}

function openLightbox(url) {
  const el = document.createElement('div');
  el.className = 'imggen-lightbox';
  el.innerHTML = `<img src="${esc(url)}" alt="">`;
  el.addEventListener('click', () => el.remove());
  document.body.appendChild(el);
}

async function loadGallery() {
  try {
    const data = await api('/api/image/gallery', { params: { limit: 40 } });
    gallery = data?.items || [];
    renderGallery();
  } catch (err) {
    console.error('gallery load failed', err);
  }
}

// ============================================================
// Submit
// ============================================================
function readNum(id) {
  const v = $(id)?.value?.trim();
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function readStr(id) {
  const v = $(id)?.value?.trim();
  return v || null;
}

async function handleSubmit() {
  const btn = $('ig-submit');
  const statusEl = $('ig-status');
  const workflow_name = $('ig-workflow')?.value;
  if (!workflow_name) {
    toast('Workflow is required', 'error');
    return;
  }
  const positive = $('ig-positive')?.value?.trim() || '';
  const negative = $('ig-negative')?.value?.trim() || '';
  const params = {};
  const width = readNum('ig-width'); if (width !== null) params.WIDTH = width;
  const height = readNum('ig-height'); if (height !== null) params.HEIGHT = height;
  const steps = readNum('ig-steps'); if (steps !== null) params.STEPS = steps;
  const cfg = readNum('ig-cfg'); if (cfg !== null) params.CFG = cfg;
  const seed = readNum('ig-seed'); if (seed !== null) params.SEED = seed;
  const sampler = readStr('ig-sampler'); if (sampler) params.SAMPLER = sampler;
  const scheduler = readStr('ig-scheduler'); if (scheduler) params.SCHEDULER = scheduler;

  btn.disabled = true;
  statusEl.textContent = 'Submitting...';
  try {
    const res = await api('/api/image/generate', {
      method: 'POST',
      body: { workflow_name, positive, negative, params },
    });
    const jid = res?.job_id || '';
    statusEl.textContent = `Enqueued: ${jid}`;
    toast('Job enqueued', 'success');
    await loadJobs();
  } catch (err) {
    console.error('generate failed', err);
    statusEl.textContent = `Error: ${err.message || err}`;
    toast('Generate failed', 'error');
  } finally {
    btn.disabled = false;
  }
}

// ============================================================
// Presets — list / delete
// ============================================================
const _PLACEHOLDER_OPTIONS = [
  'POSITIVE', 'NEGATIVE', 'SEED', 'STEPS', 'CFG', 'WIDTH', 'HEIGHT',
  'CKPT', 'VAE', 'SAMPLER', 'SCHEDULER', 'FILENAME_PREFIX', 'DENOISE',
  'LORA_1', 'LORA_2', 'LORA_3', 'STRENGTH_1', 'STRENGTH_2', 'STRENGTH_3',
];

async function loadPresets() {
  const el = $('ig-presets-body');
  if (!el) return;
  try {
    const data = await api('/api/image/workflows');
    const list = data?.workflows || [];
    if (list.length === 0) {
      el.innerHTML = '<div class="imggen-empty">まだプリセットがありません</div>';
      return;
    }
    el.innerHTML = `<div class="imggen-presets-list">${list.map(w => {
      const cat = w.category ? `<span class="tag">${esc(w.category)}</span>` : '';
      const mpc = w.main_pc_only ? '<span class="tag">main-pc</span>' : '';
      const nodes = (w.required_nodes || []).length;
      const loras = (w.required_loras || []).length;
      return `
        <div class="imggen-preset-row">
          <div>
            <div><strong>${esc(w.name)}</strong> ${cat}${mpc}</div>
            <div class="meta">${esc(w.description || '(no description)')}</div>
            <div class="meta">nodes: ${nodes} / loras: ${loras} / timeout: ${w.default_timeout_sec}s</div>
          </div>
          <button class="btn btn-sm" data-preset-view="${w.id}">表示</button>
          <button class="btn btn-sm btn-danger" data-preset-del="${w.id}" data-preset-name="${esc(w.name)}">削除</button>
        </div>`;
    }).join('')}</div>`;
    el.onclick = async (e) => {
      const del = e.target.closest('button[data-preset-del]');
      const view = e.target.closest('button[data-preset-view]');
      if (del) {
        const id = Number(del.dataset.presetDel);
        const name = del.dataset.presetName;
        if (!confirm(`プリセット "${name}" を削除しますか？`)) return;
        try {
          await api(`/api/image/workflows/${id}`, { method: 'DELETE' });
          toast('削除しました', 'info');
          await Promise.all([loadPresets(), loadWorkflows()]);
        } catch (err) {
          toast(`削除失敗: ${err?.message || err}`, 'error');
        }
      }
      if (view) {
        const id = Number(view.dataset.presetView);
        try {
          const data = await api(`/api/image/workflows/${id}`);
          openPresetModal({ edit: data });
        } catch (err) {
          toast(`読み込み失敗: ${err?.message || err}`, 'error');
        }
      }
    };
  } catch (err) {
    console.error('presets load failed', err);
    el.innerHTML = '<div class="imggen-empty">プリセット取得失敗</div>';
  }
}

// ============================================================
// Presets — modal
// ============================================================
function closePresetModal() {
  const root = $('ig-preset-modal-root');
  if (root) root.innerHTML = '';
  presetModalState = { source: '', workflowJson: null, sourceLabel: '' };
}

function openPresetModal({ edit = null } = {}) {
  presetModalState.workflowJson = edit ? (edit.workflow_json || null) : null;
  presetModalState.sourceLabel = edit ? `edit: ${edit.name}` : '';
  presetModalState.source = edit ? 'edit' : '';
  renderPresetModal(edit);
}

function renderPresetModal(edit = null) {
  const root = $('ig-preset-modal-root');
  if (!root) return;
  const tabsHtml = comfyAgents.map(a =>
    `<button data-ph-source="history:${esc(a.id)}">ComfyUI履歴: ${esc(a.name || a.id)}</button>`
  ).join('') + `<button data-ph-source="file">ファイルから</button>`;

  const phHtml = renderPlaceholderEditor();
  const metaHtml = renderMetaForm(edit);

  root.innerHTML = `
    <div class="imggen-modal-backdrop" id="ig-preset-modal-bg">
      <div class="imggen-modal" role="dialog">
        <div class="imggen-modal-header">
          <span>プリセット${edit ? '編集' : '登録'}</span>
          <button id="ig-preset-modal-close" class="btn btn-sm">×</button>
        </div>
        <div class="imggen-modal-body">
          ${edit ? '' : `
            <div>
              <label class="text-xs" style="color:var(--text-secondary);">ソース選択</label>
              <div class="imggen-source-tabs">${tabsHtml}</div>
              <input id="ig-preset-file" type="file" accept=".json,application/json" style="display:none;">
              <div id="ig-preset-history" style="margin-top:0.4rem;"></div>
            </div>`}
          <div>
            <label class="text-xs" style="color:var(--text-secondary);">Placeholder 編集（文字列値のみ一覧）</label>
            <div id="ig-preset-ph">${phHtml}</div>
          </div>
          <div>
            <label class="text-xs" style="color:var(--text-secondary);">Workflow JSON (直接編集可)</label>
            <textarea id="ig-preset-json" class="imggen-json-preview">${esc(
              presetModalState.workflowJson ? JSON.stringify(presetModalState.workflowJson, null, 2) : ''
            )}</textarea>
          </div>
          <div>
            <label class="text-xs" style="color:var(--text-secondary);">メタ情報</label>
            ${metaHtml}
          </div>
        </div>
        <div class="imggen-modal-footer">
          <button id="ig-preset-cancel" class="btn btn-sm">キャンセル</button>
          <button id="ig-preset-save" class="btn btn-sm btn-primary">${edit ? '更新' : '登録'}</button>
        </div>
      </div>
    </div>`;

  // Event binding
  $('ig-preset-modal-close')?.addEventListener('click', closePresetModal);
  $('ig-preset-cancel')?.addEventListener('click', closePresetModal);
  $('ig-preset-modal-bg')?.addEventListener('click', (e) => {
    if (e.target.id === 'ig-preset-modal-bg') closePresetModal();
  });
  $('ig-preset-save')?.addEventListener('click', () => handlePresetSave(edit));
  $('ig-preset-json')?.addEventListener('input', (e) => {
    // 編集をモデルへ反映（バリデートは保存時）
    try {
      const parsed = JSON.parse(e.target.value);
      if (parsed && typeof parsed === 'object') {
        presetModalState.workflowJson = parsed;
        $('ig-preset-ph').innerHTML = renderPlaceholderEditor();
        bindPlaceholderActions();
      }
    } catch { /* 無効なJSON中は無視 */ }
  });

  // ソースタブ
  root.querySelectorAll('[data-ph-source]').forEach(btn => {
    btn.addEventListener('click', () => handleSourceSelect(btn.dataset.phSource));
  });

  bindPlaceholderActions();
}

function renderMetaForm(edit) {
  const m = edit || {};
  return `
    <div class="imggen-meta-grid">
      <div>
        <label class="text-xs">name</label>
        <input id="ig-meta-name" class="form-input" type="text"
          value="${esc(m.name || '')}" ${edit ? 'readonly' : ''}
          placeholder="英数/_/-、1〜64文字">
      </div>
      <div>
        <label class="text-xs">category</label>
        <input id="ig-meta-category" class="form-input" type="text" value="${esc(m.category || 't2i')}">
      </div>
      <div>
        <label class="text-xs">default_timeout_sec</label>
        <input id="ig-meta-timeout" class="form-input" type="number" min="10" value="${Number(m.default_timeout_sec) || 300}">
      </div>
      <div>
        <label class="text-xs">main_pc_only</label>
        <select id="ig-meta-mpc" class="form-input">
          <option value="false" ${!m.main_pc_only ? 'selected' : ''}>false</option>
          <option value="true"  ${ m.main_pc_only ? 'selected' : ''}>true</option>
        </select>
      </div>
    </div>
    <label class="text-xs" style="margin-top:0.3rem; display:block;">description</label>
    <input id="ig-meta-desc" class="form-input" type="text" value="${esc(m.description || '')}">
  `;
}

function renderPlaceholderEditor() {
  const wf = presetModalState.workflowJson;
  if (!wf || typeof wf !== 'object') {
    return '<div class="imggen-empty" style="padding:0.6rem;">まだワークフローが読み込まれていません</div>';
  }
  const literals = extractStringLiterals(wf);
  if (literals.length === 0) {
    return '<div class="imggen-empty" style="padding:0.6rem;">編集可能な文字列フィールドが見つかりません</div>';
  }
  const optHtml = _PLACEHOLDER_OPTIONS.map(k => `<option value="${k}">{{${k}}}</option>`).join('');
  return `
    <table class="imggen-ph-table">
      <thead>
        <tr><th>node</th><th>class_type</th><th>key</th><th>value</th><th>アクション</th></tr>
      </thead>
      <tbody>
        ${literals.map(x => {
          const isPh = /^\{\{[A-Z0-9_]+\}\}$/.test(x.value);
          const valHtml = isPh
            ? `<span class="is-ph">${esc(x.value)}</span>`
            : esc(x.value.length > 80 ? x.value.slice(0, 80) + '…' : x.value);
          return `
            <tr data-nid="${esc(x.nodeId)}" data-key="${esc(x.key)}">
              <td>${esc(x.nodeId)}</td>
              <td>${esc(x.classType)}</td>
              <td>${esc(x.key)}</td>
              <td class="val">${valHtml}</td>
              <td class="imggen-ph-actions">
                <select data-ph-key>
                  <option value="">--</option>
                  ${optHtml}
                </select>
                <button class="btn btn-sm" data-ph-apply>↔</button>
                ${isPh ? `<button class="btn btn-sm" data-ph-clear title="プレースホルダ解除">解除</button>` : ''}
              </td>
            </tr>`;
        }).join('')}
      </tbody>
    </table>
  `;
}

function bindPlaceholderActions() {
  document.querySelectorAll('#ig-preset-ph button[data-ph-apply]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      if (!tr) return;
      const sel = tr.querySelector('select[data-ph-key]');
      const key = sel?.value;
      if (!key) { toast('プレースホルダを選択', 'error'); return; }
      const nid = tr.dataset.nid;
      const k = tr.dataset.key;
      if (presetModalState.workflowJson?.[nid]?.inputs) {
        presetModalState.workflowJson[nid].inputs[k] = `{{${key}}}`;
        refreshModalAfterEdit();
      }
    });
  });
  document.querySelectorAll('#ig-preset-ph button[data-ph-clear]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      if (!tr) return;
      const nid = tr.dataset.nid;
      const k = tr.dataset.key;
      const def = prompt('新しい値を入力（空でキャンセル）:', '');
      if (def == null) return;
      if (presetModalState.workflowJson?.[nid]?.inputs) {
        presetModalState.workflowJson[nid].inputs[k] = def;
        refreshModalAfterEdit();
      }
    });
  });
}

function refreshModalAfterEdit() {
  const jsonEl = $('ig-preset-json');
  if (jsonEl) jsonEl.value = JSON.stringify(presetModalState.workflowJson, null, 2);
  const phEl = $('ig-preset-ph');
  if (phEl) phEl.innerHTML = renderPlaceholderEditor();
  bindPlaceholderActions();
}

function extractStringLiterals(wf) {
  const out = [];
  for (const [nid, node] of Object.entries(wf)) {
    if (!node || typeof node !== 'object' || nid === '_meta') continue;
    const inputs = node.inputs || {};
    for (const [k, v] of Object.entries(inputs)) {
      if (typeof v !== 'string') continue;
      out.push({ nodeId: nid, classType: node.class_type || '', key: k, value: v });
    }
  }
  return out;
}

async function handleSourceSelect(src) {
  presetModalState.source = src;
  // タブのアクティブ表示
  document.querySelectorAll('[data-ph-source]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.phSource === src);
  });
  const histEl = $('ig-preset-history');
  if (src === 'file') {
    if (histEl) histEl.innerHTML = '';
    const f = $('ig-preset-file');
    f?.click();
    f.onchange = async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const parsed = JSON.parse(text);
        if (!parsed || typeof parsed !== 'object') throw new Error('invalid JSON');
        presetModalState.workflowJson = parsed;
        presetModalState.sourceLabel = `file: ${file.name}`;
        refreshModalAfterEdit();
        toast(`読み込みました: ${file.name}`, 'info');
      } catch (err) {
        toast(`JSON 解析失敗: ${err?.message || err}`, 'error');
      }
    };
    return;
  }
  if (src.startsWith('history:')) {
    const agentId = src.slice('history:'.length);
    if (histEl) histEl.innerHTML = '<div class="imggen-empty" style="padding:0.5rem;">履歴取得中...</div>';
    try {
      const data = await api(`/api/image/agents/${encodeURIComponent(agentId)}/comfyui/history?limit=20`);
      const items = data?.items || [];
      if (!data?.available) {
        histEl.innerHTML = '<div class="imggen-empty" style="padding:0.5rem;">ComfyUI が停止しています。先に起動してください。</div>';
        return;
      }
      if (items.length === 0) {
        histEl.innerHTML = '<div class="imggen-empty" style="padding:0.5rem;">履歴がありません</div>';
        return;
      }
      histEl.innerHTML = `<div class="imggen-history-list">${items.map((it, i) => {
        const files = (it.output_files || []).join(', ');
        return `
          <div class="imggen-history-item" data-hidx="${i}">
            <span class="pid">${esc(String(it.prompt_id).slice(0, 8))}</span>
            <span>${esc(it.completed ? '✓' : (it.status_str || '?'))}</span>
            <span style="flex:1; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(files)}</span>
          </div>`;
      }).join('')}</div>`;
      histEl.querySelectorAll('.imggen-history-item').forEach(it => {
        it.addEventListener('click', () => {
          const idx = Number(it.dataset.hidx);
          const picked = items[idx]?.workflow;
          if (!picked) { toast('このエントリに API 形式がありません', 'error'); return; }
          presetModalState.workflowJson = picked;
          presetModalState.sourceLabel = `history: ${agentId}`;
          histEl.querySelectorAll('.imggen-history-item').forEach(x => x.classList.remove('selected'));
          it.classList.add('selected');
          refreshModalAfterEdit();
        });
      });
    } catch (err) {
      histEl.innerHTML = `<div class="imggen-empty" style="padding:0.5rem;">取得失敗: ${esc(err?.message || err)}</div>`;
    }
  }
}

async function handlePresetSave(edit) {
  const name = edit ? edit.name : ($('ig-meta-name')?.value || '').trim();
  if (!/^[a-zA-Z0-9_\-]{1,64}$/.test(name)) {
    toast('name は英数/_/- の 1〜64 文字', 'error');
    return;
  }
  let wfJson = presetModalState.workflowJson;
  // textarea を正とする
  const raw = $('ig-preset-json')?.value || '';
  if (raw.trim()) {
    try {
      wfJson = JSON.parse(raw);
    } catch (err) {
      toast(`JSON 解析失敗: ${err.message}`, 'error');
      return;
    }
  }
  if (!wfJson || typeof wfJson !== 'object') {
    toast('Workflow JSON が空です', 'error');
    return;
  }
  const body = {
    name,
    workflow_json: wfJson,
    description: ($('ig-meta-desc')?.value || '').trim(),
    category: ($('ig-meta-category')?.value || 't2i').trim(),
    default_timeout_sec: Number($('ig-meta-timeout')?.value) || 300,
    main_pc_only: ($('ig-meta-mpc')?.value === 'true'),
  };
  const btn = $('ig-preset-save');
  if (btn) btn.disabled = true;
  try {
    await api('/api/image/workflows', { method: 'POST', body });
    toast(edit ? '更新しました' : '登録しました', 'success');
    closePresetModal();
    await Promise.all([loadPresets(), loadWorkflows()]);
  } catch (err) {
    toast(`保存失敗: ${err?.message || err}`, 'error');
    if (btn) btn.disabled = false;
  }
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  $('ig-submit')?.addEventListener('click', handleSubmit);
  $('ig-preset-new')?.addEventListener('click', () => openPresetModal({}));

  // 並列で初期ロード
  await Promise.all([
    loadWorkflows(),
    loadJobs(),
    loadGallery(),
    loadComfyPanel(),
    loadPresets(),
  ]);

  // SSE（SSE が動けば jobs はそれで更新されるが、念のためポーリングも 10s で回す）
  connectSSE();
  pollTimer = setInterval(loadJobs, 10000);
  galleryTimer = setInterval(loadGallery, 30000);
  comfyStatusTimer = setInterval(refreshComfyStatus, 15000);
}

export function unmount() {
  if (sse) { try { sse.close(); } catch { /* nop */ } sse = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (galleryTimer) { clearInterval(galleryTimer); galleryTimer = null; }
  if (comfyStatusTimer) { clearInterval(comfyStatusTimer); comfyStatusTimer = null; }
  closePresetModal();
  workflows = [];
  jobs = [];
  gallery = [];
  comfyAgents = [];
}
