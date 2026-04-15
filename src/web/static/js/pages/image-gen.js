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
  .imggen-comfy-links {
    display: flex;
    gap: 0.3rem;
    flex-wrap: wrap;
  }
  .imggen-comfy-links a {
    font-size: 0.7rem;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: var(--bg-base);
    color: var(--text-secondary);
    text-decoration: none;
  }
  .imggen-comfy-links a:hover {
    border-color: var(--accent);
    color: var(--accent);
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
</style>

<div class="imggen-grid">
  <!-- Generate -->
  <section class="card imggen-section imggen-gen">
    <div class="imggen-header">
      <h3>Generate</h3>
      <div id="ig-comfy-links" class="imggen-comfy-links"></div>
    </div>
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
</div>`;
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
// ComfyUI links
// ============================================================
async function loadComfyLinks() {
  const el = $('ig-comfy-links');
  if (!el) return;
  try {
    const data = await api('/api/image/agents');
    const agents = data?.agents || [];
    if (!agents.length) {
      el.innerHTML = '';
      return;
    }
    el.innerHTML = agents.map(a => {
      const label = `ComfyUI (${esc(a.name || a.id)})`;
      return `<a href="${esc(a.comfyui_url)}" target="_blank" rel="noopener" title="${esc(a.comfyui_url)}">${label}</a>`;
    }).join('');
  } catch (err) {
    console.error('agents load failed', err);
    el.innerHTML = '';
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
// Mount / Unmount
// ============================================================
export async function mount() {
  $('ig-submit')?.addEventListener('click', handleSubmit);

  // 並列で初期ロード
  await Promise.all([
    loadWorkflows(),
    loadJobs(),
    loadGallery(),
    loadComfyLinks(),
  ]);

  // SSE（SSE が動けば jobs はそれで更新されるが、念のためポーリングも 10s で回す）
  connectSSE();
  pollTimer = setInterval(loadJobs, 10000);
  galleryTimer = setInterval(loadGallery, 30000);
}

export function unmount() {
  if (sse) { try { sse.close(); } catch { /* nop */ } sse = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (galleryTimer) { clearInterval(galleryTimer); galleryTimer = null; }
  workflows = [];
  jobs = [];
  gallery = [];
}
