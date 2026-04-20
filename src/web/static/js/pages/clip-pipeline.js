/** Clip Pipeline (Auto-Kirinuki) page. */
import { api } from '../api.js';
import { toast } from '../app.js';

let sse = null;
let jobs = [];
let capability = null;     // array of {agent_id, ok, capability, error?}
let pollTimer = null;
let inputsCache = { base: '', files: [] };
let selectedFullPath = '';  // video_path に使う絶対パス
let manualMode = false;

function $(id) { return document.getElementById(id); }

function esc(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtTime(v) {
  if (v === null || v === undefined || v === '') return '---';
  // epoch 秒（int 又は数値文字列）を ms に換算。ISO 文字列はそのまま。
  let d;
  if (typeof v === 'number' || /^\d+$/.test(String(v))) {
    const n = Number(v);
    d = new Date(n < 1e12 ? n * 1000 : n);
  } else {
    d = new Date(v);
  }
  if (isNaN(d.getTime())) return '---';
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mi}`;
}

function fmtBytes(size) {
  const n = Number(size);
  if (!Number.isFinite(n) || n <= 0) return '-';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

function statusBadge(status) {
  const colorMap = {
    queued: '#8a8',
    dispatching: '#88a',
    warming_cache: '#a88',
    running: '#4af',
    done: '#4a4',
    failed: '#c44',
    cancelled: '#888',
  };
  const color = colorMap[status] || '#aaa';
  return `<span class="cp-badge" style="background:${color}">${esc(status)}</span>`;
}

/** NAS の出力ディレクトリを基底パス + basename(ステム) で合成。 */
function computeOutputDir(fullPath, outputsBase) {
  if (!fullPath || !outputsBase) return '';
  const stem = fullPath.split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
  const sep = outputsBase.includes('\\') || /^[A-Z]:/i.test(outputsBase) ? '\\' : '/';
  return outputsBase.replace(/[\\/]+$/, '') + sep + stem;
}

/** capability 配列から、入力/出力 NAS パスが分かる最初の Agent を選ぶ。 */
function pickNasAgent() {
  if (!Array.isArray(capability)) return null;
  for (const a of capability) {
    if (a.ok && a.capability && a.capability.nas_inputs_base) return a;
  }
  return null;
}

/** 現在のドロップダウン選択 Agent の capability を取得。 */
function currentAgentCap() {
  const sel = $('cp-agent');
  if (!sel || !Array.isArray(capability)) return null;
  const agentId = sel.value;
  const hit = capability.find(a => a.ok && (a.capability?.agent_id || a.agent_id) === agentId);
  return hit ? (hit.capability || {}) : null;
}

export function render() {
  return `
<style>
  .cp-grid { display: grid; gap: 1rem; grid-template-columns: 1fr; }
  @media (min-width: 900px) { .cp-grid { grid-template-columns: 1fr 1fr; } }
  .cp-card { background: var(--bg-raised); border: 1px solid var(--border);
             border-radius: 0.5rem; padding: 1rem; }
  .cp-card h3 { margin: 0 0 0.6rem 0; font-size: 0.95rem; }
  .cp-form-row { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.6rem; }
  .cp-form-row label { font-size: 0.78rem; color: var(--text-secondary); }
  .cp-form-row input, .cp-form-row select, .cp-form-row textarea {
    width: 100%; padding: 0.35rem 0.5rem; border-radius: 0.3rem;
    border: 1px solid var(--border); background: var(--bg-body); color: var(--text);
    font-size: 0.85rem; font-family: inherit;
  }
  .cp-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; }
  .cp-params { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.4rem; }
  .cp-badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
              color: #fff; font-size: 0.72rem; }
  .cp-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  .cp-table th, .cp-table td { padding: 0.4rem 0.5rem;
    border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
  .cp-table th { color: var(--text-secondary); font-weight: 600; font-size: 0.78rem; }
  .cp-path { font-family: monospace; font-size: 0.78rem; word-break: break-all;
             max-width: 32ch; }
  .cp-progress-bar { background: var(--bg-body); height: 6px; border-radius: 3px;
                     overflow: hidden; margin-top: 3px; min-width: 80px; }
  .cp-progress-bar-fill { background: #4af; height: 100%; transition: width .3s; }
  .cp-step { font-size: 0.72rem; color: var(--text-secondary); }
  .cp-agent-list { display: flex; flex-wrap: wrap; gap: 0.6rem; }
  .cp-agent { flex: 1 1 280px; border: 1px solid var(--border); border-radius: 0.4rem;
              padding: 0.6rem; background: var(--bg-body); font-size: 0.8rem; }
  .cp-agent-title { font-weight: 600; margin-bottom: 0.35rem; }
  .cp-agent dl { display: grid; grid-template-columns: auto 1fr; gap: 0.2rem 0.6rem;
                 margin: 0; }
  .cp-agent dt { color: var(--text-secondary); font-size: 0.75rem; }
  .cp-agent dd { margin: 0; font-size: 0.78rem; font-family: monospace; }
  .cp-actions { display: flex; gap: 0.4rem; }
  .cp-btn-small { padding: 2px 8px; font-size: 0.75rem; border-radius: 0.25rem;
                  border: 1px solid var(--border); background: var(--bg-body);
                  color: var(--text); cursor: pointer; }
  .cp-btn-small:hover { background: var(--bg-raised); }
  .cp-err { color: #c44; font-size: 0.75rem; }
  .cp-nas-paths { display: flex; flex-direction: column; gap: 0.3rem;
                  margin-bottom: 0.8rem; padding: 0.5rem 0.6rem;
                  background: var(--bg-body); border: 1px dashed var(--border);
                  border-radius: 0.3rem; }
  .cp-nas-row { display: flex; align-items: center; gap: 0.4rem; font-size: 0.78rem; }
  .cp-nas-row .cp-nas-label { color: var(--text-secondary); min-width: 3.2em; }
  .cp-nas-row .cp-nas-val { font-family: monospace; flex: 1;
                            word-break: break-all; color: var(--text); }
  .cp-copy-chip { padding: 2px 8px; font-size: 0.72rem; border-radius: 999px;
                  border: 1px solid var(--border); background: var(--bg-raised);
                  color: var(--text); cursor: pointer; }
  .cp-copy-chip:hover { background: var(--bg-body); }
  .cp-preview { font-family: monospace; font-size: 0.78rem;
                padding: 0.3rem 0.45rem; border-radius: 0.3rem;
                background: var(--bg-body); border: 1px solid var(--border);
                word-break: break-all; min-height: 1.2em; }
  .cp-preview-row { display: flex; align-items: stretch; gap: 0.3rem; }
  .cp-preview-row .cp-preview { flex: 1; }
  .cp-file-info { font-size: 0.72rem; color: var(--text-secondary); margin-top: 0.2rem; }
  .cp-toggle-row { display: flex; align-items: center; gap: 0.4rem;
                   margin-bottom: 0.6rem; font-size: 0.78rem;
                   color: var(--text-secondary); }
</style>

<div class="cp-grid">
  <div class="cp-card">
    <h3>新規ジョブ</h3>

    <div class="cp-nas-paths" id="cp-nas-paths">
      <div style="color:var(--text-secondary);font-size:0.78rem">NAS パス読み込み中...</div>
    </div>

    <form id="cp-form">
      <div class="cp-toggle-row">
        <label><input id="cp-manual-toggle" type="checkbox"> 手入力に切替</label>
        <span style="flex:1"></span>
        <button type="button" id="cp-inputs-refresh" class="cp-btn-small">inputs 再取得</button>
      </div>

      <div class="cp-form-grid">
        <div class="cp-form-row" id="cp-agent-row">
          <label for="cp-agent">Agent</label>
          <select id="cp-agent"></select>
        </div>
        <div class="cp-form-row" id="cp-video-select-row">
          <label for="cp-video-select">video (inputs フォルダから選択)</label>
          <select id="cp-video-select"></select>
        </div>
      </div>

      <div class="cp-form-row" id="cp-manual-row" style="display:none">
        <label for="cp-video-path-manual">video_path (Agent から見える絶対パス / NAS UNC)</label>
        <input id="cp-video-path-manual" type="text"
               placeholder="N:\\auto-kirinuki\\inputs\\stream_20260419.mkv">
      </div>

      <div class="cp-form-row" id="cp-video-preview-row">
        <label>video_path</label>
        <div class="cp-preview-row">
          <div class="cp-preview" id="cp-video-preview">-</div>
          <button type="button" class="cp-copy-chip" data-copy-target="cp-video-preview">copy</button>
        </div>
        <div class="cp-file-info" id="cp-video-info"></div>
      </div>

      <div class="cp-form-grid">
        <div class="cp-form-row">
          <label for="cp-mode">mode</label>
          <select id="cp-mode">
            <option value="normal">normal</option>
            <option value="test">test (先頭3分)</option>
          </select>
        </div>
        <div class="cp-form-row">
          <label for="cp-whisper">whisper_model</label>
          <input id="cp-whisper" type="text" placeholder="large-v3">
        </div>
        <div class="cp-form-row">
          <label for="cp-ollama">ollama_model</label>
          <input id="cp-ollama" type="text" placeholder="qwen3:14b">
        </div>
        <div class="cp-form-row" id="cp-output-preview-row">
          <label>output_dir (空なら自動)</label>
          <div class="cp-preview-row">
            <div class="cp-preview" id="cp-output-preview">(自動)</div>
            <button type="button" class="cp-copy-chip" data-copy-target="cp-output-preview">copy</button>
          </div>
        </div>
        <div class="cp-form-row" id="cp-output-manual-row" style="display:none">
          <label for="cp-output-dir">output_dir (空なら自動)</label>
          <input id="cp-output-dir" type="text" placeholder="(自動)">
        </div>
      </div>
      <div class="cp-form-row">
        <label>params</label>
        <div class="cp-params">
          <div class="cp-form-row">
            <label for="cp-top-n">top_n (0=全件)</label>
            <input id="cp-top-n" type="number" min="0" value="0">
          </div>
          <div class="cp-form-row">
            <label for="cp-min-clip">min_clip_sec</label>
            <input id="cp-min-clip" type="number" min="1" value="30">
          </div>
          <div class="cp-form-row">
            <label for="cp-max-clip">max_clip_sec</label>
            <input id="cp-max-clip" type="number" min="1" value="180">
          </div>
          <div class="cp-form-row">
            <label for="cp-mic-track">mic_track</label>
            <input id="cp-mic-track" type="number" min="0" value="1">
          </div>
          <div class="cp-form-row">
            <label><input id="cp-use-demucs" type="checkbox" checked> use_demucs</label>
          </div>
          <div class="cp-form-row">
            <label><input id="cp-do-export-clips" type="checkbox"> do_export_clips</label>
          </div>
        </div>
      </div>
      <button type="submit" class="btn btn-primary">登録</button>
    </form>
  </div>

  <div class="cp-card">
    <h3>Agent capability <button id="cp-cap-refresh" class="cp-btn-small">再取得</button></h3>
    <div id="cp-capability" class="cp-agent-list">
      <div style="color:var(--text-secondary)">読み込み中...</div>
    </div>
  </div>
</div>

<div class="cp-card" style="margin-top:1rem;">
  <h3>ジョブ一覧 <button id="cp-jobs-refresh" class="cp-btn-small">再読込</button></h3>
  <table class="cp-table" id="cp-jobs-table">
    <thead><tr>
      <th>作成</th>
      <th>動画</th>
      <th>状態</th>
      <th>進捗</th>
      <th>Agent</th>
      <th>Whisper</th>
      <th>操作</th>
    </tr></thead>
    <tbody id="cp-jobs-body">
      <tr><td colspan="7" style="text-align:center;color:var(--text-secondary)">読み込み中...</td></tr>
    </tbody>
  </table>
</div>`;
}

export async function mount() {
  $('cp-form').addEventListener('submit', onSubmit);
  $('cp-cap-refresh').addEventListener('click', async () => {
    await loadCapability();
    await loadInputsForSelected();
  });
  $('cp-jobs-refresh').addEventListener('click', loadJobs);
  $('cp-inputs-refresh').addEventListener('click', loadInputsForSelected);
  $('cp-manual-toggle').addEventListener('change', onToggleManual);
  $('cp-agent').addEventListener('change', loadInputsForSelected);
  $('cp-video-select').addEventListener('change', onVideoSelect);
  $('cp-video-path-manual').addEventListener('input', syncManualVideo);
  $('cp-output-dir').addEventListener('input', () => { /* manual output is read on submit */ });

  // Copy chip handlers (delegated)
  document.querySelectorAll('[data-copy-target]').forEach(btn => {
    btn.addEventListener('click', () => {
      const el = document.getElementById(btn.dataset.copyTarget);
      if (!el) return;
      const text = el.textContent.trim();
      if (!text || text === '-' || text === '(自動)') return;
      copyToClipboard(text);
    });
  });

  await Promise.all([loadCapability(), loadJobs()]);
  await loadInputsForSelected();
  connectSSE();
  // Also periodic refresh in case SSE misses
  pollTimer = setInterval(loadJobs, 15000);
}

export function unmount() {
  if (sse) { sse.close(); sse = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('コピーしました', 'success');
  } catch (err) {
    toast(`コピー失敗: ${err.message}`, 'error');
  }
}

function onToggleManual() {
  manualMode = $('cp-manual-toggle').checked;
  $('cp-agent-row').style.display = manualMode ? 'none' : '';
  $('cp-video-select-row').style.display = manualMode ? 'none' : '';
  $('cp-manual-row').style.display = manualMode ? '' : 'none';
  $('cp-output-preview-row').style.display = manualMode ? 'none' : '';
  $('cp-output-manual-row').style.display = manualMode ? '' : 'none';
  // Reset selection when turning toggle off
  if (!manualMode) {
    $('cp-video-path-manual').value = '';
  } else {
    selectedFullPath = '';
    $('cp-video-preview').textContent = '-';
    $('cp-output-preview').textContent = '(自動)';
    $('cp-video-info').textContent = '';
  }
  syncManualVideo();
}

function syncManualVideo() {
  if (!manualMode) return;
  // manual モードでは preview を input の値と同期（表示のため）
  const v = $('cp-video-path-manual').value.trim();
  $('cp-video-preview').textContent = v || '-';
}

function onVideoSelect() {
  const sel = $('cp-video-select');
  const opt = sel.options[sel.selectedIndex];
  if (!opt || !opt.dataset.fullPath) {
    selectedFullPath = '';
    $('cp-video-preview').textContent = '-';
    $('cp-output-preview').textContent = '(自動)';
    $('cp-video-info').textContent = '';
    return;
  }
  selectedFullPath = opt.dataset.fullPath;
  $('cp-video-preview').textContent = selectedFullPath;

  const cap = currentAgentCap();
  const outBase = cap?.nas_outputs_base || '';
  const outDir = computeOutputDir(selectedFullPath, outBase);
  $('cp-output-preview').textContent = outDir || '(自動)';

  const size = opt.dataset.size ? fmtBytes(opt.dataset.size) : '-';
  const mtime = opt.dataset.mtime ? fmtTime(opt.dataset.mtime) : '-';
  $('cp-video-info').textContent = `サイズ: ${size} / 更新: ${mtime}`;
}

function renderNasPaths() {
  const box = $('cp-nas-paths');
  if (!box) return;
  const a = pickNasAgent();
  if (!a) {
    box.innerHTML = '<div style="color:var(--text-secondary);font-size:0.78rem">Agent 未応答 / NAS パス未設定</div>';
    return;
  }
  const c = a.capability || {};
  const inBase = c.nas_inputs_base || '';
  const outBase = c.nas_outputs_base || '';
  box.innerHTML = `
    <div class="cp-nas-row">
      <span class="cp-nas-label">入力:</span>
      <span class="cp-nas-val" id="cp-nas-in">${esc(inBase || '-')}</span>
      <button type="button" class="cp-copy-chip" data-nas="in">copy</button>
    </div>
    <div class="cp-nas-row">
      <span class="cp-nas-label">出力:</span>
      <span class="cp-nas-val" id="cp-nas-out">${esc(outBase || '-')}</span>
      <button type="button" class="cp-copy-chip" data-nas="out">copy</button>
    </div>`;
  box.querySelectorAll('[data-nas]').forEach(btn => {
    btn.addEventListener('click', () => {
      const v = btn.dataset.nas === 'in' ? inBase : outBase;
      if (v) copyToClipboard(v);
    });
  });
}

function renderAgentSelect() {
  const sel = $('cp-agent');
  if (!sel) return;
  const oldVal = sel.value;
  const okAgents = (capability || []).filter(a => a.ok);
  if (okAgents.length === 0) {
    sel.innerHTML = '<option value="">(利用可能な Agent なし)</option>';
    return;
  }
  sel.innerHTML = okAgents.map(a => {
    const id = a.capability?.agent_id || a.agent_id || '?';
    return `<option value="${esc(id)}">${esc(id)}</option>`;
  }).join('');
  // Preserve prior selection if still present
  if (oldVal && okAgents.some(a => (a.capability?.agent_id || a.agent_id) === oldVal)) {
    sel.value = oldVal;
  }
}

async function loadInputsForSelected() {
  const sel = $('cp-agent');
  const vs = $('cp-video-select');
  const agentId = sel ? sel.value : '';
  if (!agentId) {
    if (vs) vs.innerHTML = '<option value="">(Agent なし)</option>';
    inputsCache = { base: '', files: [] };
    return;
  }
  vs.innerHTML = '<option value="">読み込み中...</option>';
  inputsCache = await loadInputs(agentId);
  renderVideoSelect();
  onVideoSelect();  // update preview based on current selection
}

async function loadInputs(agentId) {
  if (!agentId) return { files: [], base: '' };
  try {
    const res = await api('/api/clip-pipeline/inputs', { params: { agent_id: agentId } });
    return { base: res.base || '', files: res.files || [] };
  } catch (err) {
    toast(`inputs 取得失敗: ${err.message}`, 'error');
    return { files: [], base: '' };
  }
}

function renderVideoSelect() {
  const vs = $('cp-video-select');
  if (!vs) return;
  const files = inputsCache.files || [];
  const opts = ['<option value="">-- ファイルを選択 --</option>'];
  for (const f of files) {
    const label = `${f.name} (${fmtBytes(f.size)}, ${fmtTime(f.mtime)})`;
    opts.push(
      `<option value="${esc(f.full_path)}"` +
      ` data-full-path="${esc(f.full_path)}"` +
      ` data-size="${esc(f.size ?? '')}"` +
      ` data-mtime="${esc(f.mtime ?? '')}"` +
      `>${esc(label)}</option>`
    );
  }
  vs.innerHTML = opts.join('');
  selectedFullPath = '';
  $('cp-video-preview').textContent = '-';
  $('cp-output-preview').textContent = '(自動)';
  $('cp-video-info').textContent = '';
}

async function onSubmit(e) {
  e.preventDefault();
  let videoPath = '';
  let outputDir = null;
  if (manualMode) {
    videoPath = $('cp-video-path-manual').value.trim();
    outputDir = $('cp-output-dir').value.trim() || null;
  } else {
    videoPath = selectedFullPath;
    const outPrev = $('cp-output-preview').textContent.trim();
    outputDir = (outPrev && outPrev !== '(自動)') ? outPrev : null;
  }
  if (!videoPath) {
    alert('video_path が選択されていません');
    return;
  }
  const body = {
    video_path: videoPath,
    mode: $('cp-mode').value,
    whisper_model: $('cp-whisper').value.trim(),
    ollama_model: $('cp-ollama').value.trim(),
    output_dir: outputDir,
    params: {
      top_n: Number($('cp-top-n').value) || 0,
      min_clip_sec: Number($('cp-min-clip').value) || 30,
      max_clip_sec: Number($('cp-max-clip').value) || 180,
      mic_track: Number($('cp-mic-track').value) || 0,
      use_demucs: $('cp-use-demucs').checked,
      do_export_clips: $('cp-do-export-clips').checked,
    },
  };
  try {
    const res = await api('/api/clip-pipeline/jobs', { method: 'POST', body });
    toast(`ジョブ登録: ${res.job_id.slice(0, 8)}...`, 'success');
    await loadJobs();
  } catch (err) {
    toast(`登録失敗: ${err.message}`, 'error');
  }
}

async function loadCapability() {
  const box = $('cp-capability');
  box.innerHTML = '<div style="color:var(--text-secondary)">読み込み中...</div>';
  try {
    const res = await api('/api/clip-pipeline/capability');
    capability = res.agents || [];
    if (capability.length === 0) {
      box.innerHTML = '<div style="color:var(--text-secondary)">登録 Agent なし</div>';
    } else {
      box.innerHTML = capability.map(renderAgent).join('');
    }
    renderNasPaths();
    renderAgentSelect();
  } catch (err) {
    box.innerHTML = `<div class="cp-err">エラー: ${esc(err.message)}</div>`;
    capability = [];
    renderNasPaths();
    renderAgentSelect();
  }
}

function renderAgent(a) {
  if (!a.ok) {
    return `<div class="cp-agent">
      <div class="cp-agent-title">${esc(a.agent_id || '?')}</div>
      <div class="cp-err">${esc(a.error || 'unreachable')}</div>
    </div>`;
  }
  const c = a.capability || {};
  const gpu = c.gpu_info || {};
  return `<div class="cp-agent">
    <div class="cp-agent-title">${esc(c.agent_id || a.agent_id)} (${esc(c.role || '-')})</div>
    <dl>
      <dt>GPU</dt><dd>${esc(gpu.name || '-')}</dd>
      <dt>VRAM</dt><dd>${esc(gpu.vram_free_mb ?? '-')} / ${esc(gpu.vram_total_mb ?? '-')} MB</dd>
      <dt>busy</dt><dd>${c.busy ? 'yes' : 'no'}</dd>
      <dt>ffmpeg</dt><dd>${esc(c.ffmpeg_version || '-')}</dd>
      <dt>Whisper (SSD)</dt><dd>${esc((c.whisper_models_local || []).join(', ') || '-')}</dd>
      <dt>Whisper (NAS)</dt><dd>${esc((c.whisper_models_nas || []).join(', ') || '-')}</dd>
    </dl>
  </div>`;
}

async function loadJobs() {
  try {
    const res = await api('/api/clip-pipeline/jobs', { params: { limit: 30 } });
    jobs = res.jobs || [];
    renderJobs();
  } catch (err) {
    const tb = $('cp-jobs-body');
    if (tb) tb.innerHTML = `<tr><td colspan="7" class="cp-err">エラー: ${esc(err.message)}</td></tr>`;
  }
}

function renderJobs() {
  const tb = $('cp-jobs-body');
  if (!tb) return;
  if (jobs.length === 0) {
    tb.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-secondary)">ジョブなし</td></tr>';
    return;
  }
  tb.innerHTML = jobs.map(j => {
    const video = (j.video_path || '').split(/[\\/]/).pop() || '-';
    const progress = Math.max(0, Math.min(100, Number(j.progress) || 0));
    const step = j.step ? `<div class="cp-step">${esc(j.step)}</div>` : '';
    const cancelable = !['done', 'failed', 'cancelled'].includes(j.status);
    return `<tr>
      <td>${fmtTime(j.created_at)}</td>
      <td class="cp-path" title="${esc(j.video_path)}">${esc(video)}</td>
      <td>${statusBadge(j.status)}${j.last_error ? `<div class="cp-err" title="${esc(j.last_error)}">${esc(j.last_error).slice(0, 40)}...</div>` : ''}</td>
      <td>
        ${progress}%
        <div class="cp-progress-bar"><div class="cp-progress-bar-fill" style="width:${progress}%"></div></div>
        ${step}
      </td>
      <td>${esc(j.assigned_agent || '-')}</td>
      <td>${esc(j.whisper_model || '-')}</td>
      <td>
        ${cancelable ? `<button class="cp-btn-small" data-cancel="${esc(j.job_id)}">取消</button>` : ''}
      </td>
    </tr>`;
  }).join('');
  tb.querySelectorAll('[data-cancel]').forEach(btn => {
    btn.addEventListener('click', () => cancelJob(btn.dataset.cancel));
  });
}

async function cancelJob(jobId) {
  if (!confirm(`ジョブ ${jobId.slice(0, 8)}... を取消しますか？`)) return;
  try {
    await api(`/api/clip-pipeline/jobs/${jobId}/cancel`, { method: 'POST' });
    toast('取消要求送信', 'success');
    await loadJobs();
  } catch (err) {
    toast(`取消失敗: ${err.message}`, 'error');
  }
}

function connectSSE() {
  if (sse) sse.close();
  sse = new EventSource('/api/clip-pipeline/jobs/stream');
  sse.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      handleEvent(ev);
    } catch { /* ignore */ }
  };
  sse.onerror = () => {
    // auto-reconnect by the browser; nothing to do
  };
}

function handleEvent(ev) {
  const jobId = ev.job_id;
  if (!jobId) return;
  const idx = jobs.findIndex(j => j.job_id === jobId);
  if (idx === -1) {
    // Unknown job — refresh whole list
    loadJobs();
    return;
  }
  const j = jobs[idx];
  if (ev.status) j.status = ev.status;
  if (ev.step) j.step = ev.step;
  if (typeof ev.progress === 'number') j.progress = Math.round(ev.progress);
  if (ev.agent_id) j.assigned_agent = ev.agent_id;
  if (ev.detail && ev.detail.message && ev.status === 'failed') j.last_error = ev.detail.message;
  renderJobs();
}
