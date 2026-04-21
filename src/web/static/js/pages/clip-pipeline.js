/** Clip Pipeline (Auto-Kirinuki) page. */
import { api } from '../api.js';
import { toast } from '../app.js';

let sse = null;
let jobs = [];
let capability = null;     // array of {agent_id, ok, capability, error?}
let pollTimer = null;
let inputsCache = { base: '', files: [] };
let selectedFullPaths = [];  // 複数選択: Agent に送信する絶対パスの配列
let manualMode = false;
let ollamaModels = [];      // /api/ollama-models から取得
let mimiOllamaModel = '';   // /api/llm-config の ollama_model（Mimi と共有する初期値）

// ログ表示用の状態（SSE の log / step イベントをジョブ別に蓄積）
const logsByJob = new Map();  // jobId -> Array<{ts:number, kind:'log'|'step', message:string}>
let activeLogJobId = null;    // 現在ログ表示中のジョブ ID（最新イベント発生ジョブを自動追従）
let followLatestLog = true;   // ドロップダウンで明示選択すると false になる
const MAX_LOG_LINES = 500;

const STORAGE_KEY = 'clip-pipeline:form:v1';

// Agent の NAS / SSD に未配置でも選べるよう、faster-whisper が自動 DL できる
// 既知のモデル名をフォールバック表示に使う。
const KNOWN_WHISPER_MODELS = [
  'large-v3-turbo', 'large-v3', 'distil-large-v3',
  'medium', 'small', 'base', 'tiny',
];

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveSettings(patch) {
  const cur = loadSettings();
  const next = { ...cur, ...patch };
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* ignore */ }
}

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
  const hit = capability.find(a => a.ok && a.agent_id === agentId);
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
                word-break: break-all; min-height: 1.2em;
                white-space: pre-wrap; max-height: 8rem; overflow-y: auto; }
  .cp-preview-row { display: flex; align-items: stretch; gap: 0.3rem; }
  .cp-preview-row .cp-preview { flex: 1; }
  .cp-file-info { font-size: 0.72rem; color: var(--text-secondary); margin-top: 0.2rem; }
  .cp-toggle-row { display: flex; align-items: center; gap: 0.4rem;
                   margin-bottom: 0.6rem; font-size: 0.78rem;
                   color: var(--text-secondary); }
  .cp-log-section { margin-top: 0.8rem; padding-top: 0.6rem;
                    border-top: 1px dashed var(--border); }
  .cp-log-header { display: flex; align-items: center; gap: 0.4rem;
                   margin-bottom: 0.35rem; font-size: 0.78rem;
                   color: var(--text-secondary); }
  .cp-log-header select { padding: 2px 6px; font-size: 0.75rem;
                          background: var(--bg-body); color: var(--text);
                          border: 1px solid var(--border); border-radius: 0.25rem;
                          max-width: 20ch; }
  .cp-log-pane { font-family: monospace; font-size: 0.72rem;
                 line-height: 1.35; white-space: pre-wrap; word-break: break-all;
                 background: var(--bg-body); border: 1px solid var(--border);
                 border-radius: 0.3rem; padding: 0.45rem 0.55rem;
                 height: 18rem; overflow-y: auto; margin: 0; color: var(--text); }
  .cp-log-pane .cp-log-step { color: #4af; font-weight: 600; }
  .cp-log-pane .cp-log-ts { color: var(--text-secondary); }
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
          <label for="cp-video-select">video (inputs フォルダから複数選択可)</label>
          <select id="cp-video-select" multiple size="6"></select>
          <div class="cp-file-info">Ctrl / Shift + クリックで複数選択。選択したファイル数だけジョブを登録します。</div>
        </div>
      </div>

      <div class="cp-form-row" id="cp-manual-row" style="display:none">
        <label for="cp-video-path-manual">video_path (Agent から見える絶対パス / NAS UNC)</label>
        <input id="cp-video-path-manual" type="text"
               placeholder="N:\\auto-kirinuki\\inputs\\stream_20260419.mkv">
      </div>

      <div class="cp-form-row" id="cp-video-preview-row">
        <label>送信される video_path（ジョブ登録時に Agent へ渡す絶対パス）</label>
        <div class="cp-preview-row">
          <div class="cp-preview" id="cp-video-preview">-</div>
          <button type="button" class="cp-copy-chip" data-copy-target="cp-video-preview">copy</button>
        </div>
        <div class="cp-file-info" id="cp-video-info"></div>
      </div>

      <div class="cp-form-grid">
        <div class="cp-form-row">
          <label for="cp-whisper">whisper_model</label>
          <select id="cp-whisper"><option value="">(Agent 選択待ち)</option></select>
          <div class="cp-file-info" id="cp-whisper-hint"></div>
        </div>
        <div class="cp-form-row">
          <label for="cp-ollama">ollama_model <span id="cp-ollama-hint" style="color:var(--text-secondary);font-weight:normal;font-size:0.72rem"></span></label>
          <select id="cp-ollama"><option value="">(読込中)</option></select>
          <div class="cp-file-info">未選択のときは Mimi と同じモデルで実行します。ここで別モデルを選んでも Mimi 側の設定は変わりません。</div>
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
    <div class="cp-log-section">
      <div class="cp-log-header">
        <span>ジョブログ</span>
        <select id="cp-log-job"><option value="">(イベント待ち)</option></select>
        <span style="flex:1"></span>
        <button type="button" id="cp-log-clear" class="cp-btn-small">消去</button>
      </div>
      <pre class="cp-log-pane" id="cp-log-pane"><span style="color:var(--text-secondary)">SSE log / step イベント待ち...</span></pre>
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
    await Promise.all([loadCapability(), loadOllamaModels()]);
    await loadInputsForSelected();
  });
  $('cp-jobs-refresh').addEventListener('click', loadJobs);
  $('cp-inputs-refresh').addEventListener('click', loadInputsForSelected);
  $('cp-manual-toggle').addEventListener('change', onToggleManual);
  $('cp-agent').addEventListener('change', () => {
    saveSettings({ agent_id: $('cp-agent').value });
    renderWhisperSelect();
    loadInputsForSelected();
  });
  $('cp-video-select').addEventListener('change', onVideoSelect);
  $('cp-video-path-manual').addEventListener('input', syncManualVideo);
  $('cp-output-dir').addEventListener('input', () => { /* manual output is read on submit */ });

  // form の各入力を localStorage に同期
  const persistFields = [
    'cp-whisper', 'cp-ollama',
    'cp-top-n', 'cp-min-clip', 'cp-mic-track',
    'cp-use-demucs', 'cp-do-export-clips',
  ];
  persistFields.forEach(id => {
    const el = $(id);
    if (!el) return;
    const ev = (el.type === 'checkbox') ? 'change' : 'input';
    el.addEventListener(ev, () => {
      saveSettings({ [id]: el.type === 'checkbox' ? el.checked : el.value });
    });
  });

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

  // ログパネル
  const logSel = $('cp-log-job');
  if (logSel) {
    logSel.addEventListener('change', () => {
      activeLogJobId = logSel.value || null;
      followLatestLog = !activeLogJobId;  // 空選択 → auto-follow 再開
      renderLogPane();
    });
  }
  const logClear = $('cp-log-clear');
  if (logClear) {
    logClear.addEventListener('click', () => {
      if (activeLogJobId) logsByJob.delete(activeLogJobId);
      else logsByJob.clear();
      renderLogJobSelect();
      renderLogPane();
    });
  }

  restoreNumericFields();
  await Promise.all([loadCapability(), loadOllamaModels(), loadMimiLLMConfig(), loadJobs()]);
  applyOllamaDefault();
  await loadInputsForSelected();
  connectSSE();
  // Also periodic refresh in case SSE misses
  pollTimer = setInterval(loadJobs, 15000);
}

function restoreNumericFields() {
  const s = loadSettings();
  const numFields = {
    'cp-top-n': 0,
    'cp-min-clip': 30,
    'cp-mic-track': 1,
  };
  for (const [id, def] of Object.entries(numFields)) {
    const el = $(id);
    if (!el) continue;
    if (s[id] !== undefined && s[id] !== '') el.value = s[id];
    else if (!el.value) el.value = def;
  }
  const boolFields = ['cp-use-demucs', 'cp-do-export-clips'];
  for (const id of boolFields) {
    const el = $(id);
    if (!el) continue;
    if (typeof s[id] === 'boolean') el.checked = s[id];
  }
}

async function loadOllamaModels() {
  const sel = $('cp-ollama');
  if (!sel) return;
  try {
    const res = await api('/api/ollama-models');
    ollamaModels = res.models || [];
  } catch {
    ollamaModels = [];
  }
  const opts = ['<option value="">-- Mimi と同じ --</option>'];
  for (const m of ollamaModels) {
    opts.push(`<option value="${esc(m)}">${esc(m)}</option>`);
  }
  sel.innerHTML = opts.join('');
  // 明示的に保存されているときだけ復元。未設定なら "" のまま = Mimi と同じ扱い。
  const saved = loadSettings()['cp-ollama'];
  if (saved && ollamaModels.includes(saved)) sel.value = saved;
}

async function loadMimiLLMConfig() {
  try {
    const res = await api('/api/llm-config');
    mimiOllamaModel = res.ollama_model || '';
  } catch {
    mimiOllamaModel = '';
  }
  const hint = $('cp-ollama-hint');
  if (hint) hint.textContent = mimiOllamaModel ? `(Mimi: ${mimiOllamaModel})` : '';
}

/** select の値が未設定かつ localStorage にも明示保存が無い場合、
 *  表示上の初期値として Mimi のモデルを選んでおく（保存はしない）。 */
function applyOllamaDefault() {
  const sel = $('cp-ollama');
  if (!sel) return;
  const saved = loadSettings()['cp-ollama'];
  if (saved) return; // 既に永続化済みの値が適用されている
  if (mimiOllamaModel && ollamaModels.includes(mimiOllamaModel)) {
    sel.value = mimiOllamaModel;
  }
}

function renderWhisperSelect() {
  const sel = $('cp-whisper');
  if (!sel) return;
  const hint = $('cp-whisper-hint');
  const cap = currentAgentCap() || {};
  const local = cap.whisper_models_local || [];
  const nas = cap.whisper_models_nas || [];
  const seen = new Set();
  const opts = ['<option value="">-- 選択 --</option>'];
  for (const m of local) {
    if (seen.has(m)) continue;
    seen.add(m);
    opts.push(`<option value="${esc(m)}">${esc(m)} (local)</option>`);
  }
  for (const m of nas) {
    if (seen.has(m)) continue;
    seen.add(m);
    opts.push(`<option value="${esc(m)}">${esc(m)} (NAS — 同期必要)</option>`);
  }
  // 未配置モデルもフォールバックで提示（NAS に配置すれば cache-sync で使える）
  for (const m of KNOWN_WHISPER_MODELS) {
    if (seen.has(m)) continue;
    seen.add(m);
    opts.push(`<option value="${esc(m)}">${esc(m)} (未配置 — NAS 配置要)</option>`);
  }
  sel.innerHTML = opts.join('');
  const saved = loadSettings()['cp-whisper'] || '';
  if (saved && seen.has(saved)) sel.value = saved;
  if (hint) {
    if (local.length === 0 && nas.length === 0) {
      hint.textContent = 'このAgent のローカル/NAS にキャッシュ済みモデルはありません。NAS の whisper フォルダ (nas.whisper_base) にモデルディレクトリを配置してください。';
    } else {
      hint.textContent = '';
    }
  }
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
  if (!manualMode) {
    $('cp-video-path-manual').value = '';
  } else {
    selectedFullPaths = [];
    $('cp-video-preview').textContent = '-';
    $('cp-output-preview').textContent = '(自動)';
    $('cp-video-info').textContent = '';
  }
  syncManualVideo();
}

function syncManualVideo() {
  if (!manualMode) return;
  const v = $('cp-video-path-manual').value.trim();
  $('cp-video-preview').textContent = v || '-';
}

function onVideoSelect() {
  const sel = $('cp-video-select');
  if (!sel) return;
  const opts = Array.from(sel.selectedOptions || []).filter(o => o.dataset.fullPath);
  selectedFullPaths = opts.map(o => o.dataset.fullPath);

  const prev = $('cp-video-preview');
  const outPrev = $('cp-output-preview');
  const info = $('cp-video-info');
  const cap = currentAgentCap();
  const outBase = cap?.nas_outputs_base || '';

  if (opts.length === 0) {
    prev.textContent = '-';
    outPrev.textContent = '(自動)';
    info.textContent = '';
    return;
  }

  prev.textContent = selectedFullPaths.join('\n');

  if (opts.length === 1) {
    const o = opts[0];
    outPrev.textContent = computeOutputDir(o.dataset.fullPath, outBase) || '(自動)';
    const size = o.dataset.size ? fmtBytes(o.dataset.size) : '-';
    const mtime = o.dataset.mtime ? fmtTime(o.dataset.mtime) : '-';
    info.textContent = `サイズ: ${size} / 更新: ${mtime}`;
  } else {
    outPrev.textContent = `(各動画で自動生成 — ${opts.length} 件)`;
    let total = 0;
    for (const o of opts) total += Number(o.dataset.size || 0);
    info.textContent = `${opts.length} 件選択 / 合計サイズ: ${fmtBytes(total)}`;
  }
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
  // value は config 側 id（バックエンドのキー）、表示は capability 側 id があればそれを使う
  sel.innerHTML = okAgents.map(a => {
    const cfgId = a.agent_id || '?';
    const capId = a.capability?.agent_id || '';
    const label = capId && capId !== cfgId ? `${cfgId} (${capId})` : cfgId;
    return `<option value="${esc(cfgId)}">${esc(label)}</option>`;
  }).join('');
  // 復元優先度: 前回選択 > localStorage > 先頭
  const saved = loadSettings().agent_id || '';
  const pick = [oldVal, saved].find(v => v && okAgents.some(a => a.agent_id === v));
  if (pick) sel.value = pick;
  renderWhisperSelect();
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
  const opts = [];
  if (files.length === 0) {
    opts.push('<option value="" disabled>(inputs フォルダに動画なし)</option>');
  }
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
  selectedFullPaths = [];
  $('cp-video-preview').textContent = '-';
  $('cp-output-preview').textContent = '(自動)';
  $('cp-video-info').textContent = '';
}

async function onSubmit(e) {
  e.preventDefault();

  // 送信対象の video_path 群を組み立てる
  let videoPaths = [];
  let manualOutputDir = null;
  if (manualMode) {
    const v = $('cp-video-path-manual').value.trim();
    if (v) videoPaths = [v];
    manualOutputDir = $('cp-output-dir').value.trim() || null;
  } else {
    videoPaths = [...selectedFullPaths];
  }
  if (videoPaths.length === 0) {
    alert('video_path が選択されていません');
    return;
  }

  // ollama_model は空のとき Mimi と同じモデルを送信する（backend は受け取った値で動く）
  const ollamaSel = $('cp-ollama').value.trim();
  const ollamaModel = ollamaSel || mimiOllamaModel || '';

  const params = {
    top_n: Number($('cp-top-n').value) || 0,
    min_clip_sec: Number($('cp-min-clip').value) || 30,
    mic_track: Number($('cp-mic-track').value) || 0,
    use_demucs: $('cp-use-demucs').checked,
    do_export_clips: $('cp-do-export-clips').checked,
  };
  const whisperModel = $('cp-whisper').value.trim();

  // 設定永続化（ユーザー選択値のみ — ollamaSel が空なら空のまま保存し、次回も Mimi 追従）
  saveSettings({
    agent_id: $('cp-agent').value,
    'cp-whisper': whisperModel,
    'cp-ollama': ollamaSel,
    'cp-top-n': params.top_n,
    'cp-min-clip': params.min_clip_sec,
    'cp-mic-track': params.mic_track,
    'cp-use-demucs': params.use_demucs,
    'cp-do-export-clips': params.do_export_clips,
  });

  // 複数ある場合は並列に POST（Pi 側 Dispatcher が DB キュー経由で順次発射する）
  const results = await Promise.allSettled(videoPaths.map(vp => {
    const body = {
      video_path: vp,
      whisper_model: whisperModel,
      ollama_model: ollamaModel,
      output_dir: videoPaths.length === 1 ? manualOutputDir : null,
      params,
    };
    return api('/api/clip-pipeline/jobs', { method: 'POST', body });
  }));

  const ok = results.filter(r => r.status === 'fulfilled');
  const ng = results.filter(r => r.status === 'rejected');
  if (ok.length > 0) {
    toast(`ジョブ登録 ${ok.length} 件${ng.length ? ` / 失敗 ${ng.length} 件` : ''}`, ng.length ? 'warning' : 'success');
  }
  for (const r of ng) {
    const msg = r.reason?.message || String(r.reason);
    toast(`登録失敗: ${msg}`, 'error');
  }
  await loadJobs();
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

  // log / step / error イベントはジョブログへ流す
  if (ev.event === 'log') {
    const msg = ev.detail && ev.detail.message;
    if (msg) pushJobLog(jobId, 'log', String(msg));
    return;  // log だけなら progress / step を触らない
  }
  if (ev.event === 'step' && ev.step) {
    pushJobLog(jobId, 'step', `--- step: ${ev.step} ---`);
    // step 更新は下でジョブ行にも反映するのでフォールスルーさせる
  }
  if (ev.event === 'error') {
    const msg = ev.detail && ev.detail.message;
    if (msg) pushJobLog(jobId, 'step', `[error] ${String(msg)}`);
  }

  const idx = jobs.findIndex(j => j.job_id === jobId);
  if (idx === -1) {
    // Unknown job — refresh whole list
    loadJobs();
    return;
  }
  const j = jobs[idx];
  if (ev.status) j.status = ev.status;
  if (ev.step) j.step = ev.step;
  // progress は progress / result イベントでのみ更新する。
  // step や log イベントにも progress=0（デフォルト）が乗って来るため、
  // 無条件に上書きすると step 切替の瞬間にバーが 0% へ潰れる。
  if ((ev.event === 'progress' || ev.event === 'result') && typeof ev.progress === 'number') {
    j.progress = Math.round(ev.progress);
  }
  if (ev.agent_id) j.assigned_agent = ev.agent_id;
  if (ev.detail && ev.detail.message && ev.status === 'failed') j.last_error = ev.detail.message;
  renderJobs();
}

function pushJobLog(jobId, kind, message) {
  let arr = logsByJob.get(jobId);
  const isNew = !arr;
  if (isNew) { arr = []; logsByJob.set(jobId, arr); }
  arr.push({ ts: Date.now(), kind, message });
  if (arr.length > MAX_LOG_LINES) arr.splice(0, arr.length - MAX_LOG_LINES);

  if (isNew) renderLogJobSelect();
  // auto-follow: 最新イベントのジョブを追従
  if (followLatestLog && activeLogJobId !== jobId) {
    activeLogJobId = jobId;
    const sel = $('cp-log-job');
    if (sel) sel.value = jobId;
    renderLogPane();
    return;
  }
  if (jobId === activeLogJobId) {
    appendLogLine(arr[arr.length - 1]);
  }
}

function fmtLogTime(ts) {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mi}:${ss}`;
}

function shortJobId(jobId) {
  return (jobId || '').slice(0, 8);
}

function renderLogJobSelect() {
  const sel = $('cp-log-job');
  if (!sel) return;
  const ids = Array.from(logsByJob.keys());
  if (ids.length === 0) {
    sel.innerHTML = '<option value="">(イベント待ち)</option>';
    return;
  }
  const cur = activeLogJobId || '';
  sel.innerHTML = ids.map(id => {
    const label = shortJobId(id);
    return `<option value="${esc(id)}"${id === cur ? ' selected' : ''}>${esc(label)}</option>`;
  }).join('');
}

function renderLogPane() {
  const pane = $('cp-log-pane');
  if (!pane) return;
  if (!activeLogJobId) {
    pane.innerHTML = '<span style="color:var(--text-secondary)">SSE log / step イベント待ち...</span>';
    return;
  }
  const arr = logsByJob.get(activeLogJobId) || [];
  if (arr.length === 0) {
    pane.innerHTML = '<span style="color:var(--text-secondary)">(ログなし)</span>';
    return;
  }
  pane.innerHTML = arr.map(renderLogLine).join('\n');
  pane.scrollTop = pane.scrollHeight;
}

function renderLogLine(line) {
  const ts = `<span class="cp-log-ts">[${fmtLogTime(line.ts)}]</span>`;
  const cls = line.kind === 'step' ? ' class="cp-log-step"' : '';
  return `${ts} <span${cls}>${esc(line.message)}</span>`;
}

function appendLogLine(line) {
  const pane = $('cp-log-pane');
  if (!pane) return;
  // 初回（プレースホルダのみ）の場合はまるごと再描画
  if (!pane.querySelector('.cp-log-ts')) {
    renderLogPane();
    return;
  }
  const nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
  pane.insertAdjacentHTML('beforeend', '\n' + renderLogLine(line));
  if (nearBottom) pane.scrollTop = pane.scrollHeight;
}
