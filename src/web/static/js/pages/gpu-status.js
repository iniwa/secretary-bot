/** GPU Status page — shows each Windows Agent's `logs/gpu_status.log`
 * (nvidia-smi + ollama ps output appended by start_agent.bat at boot). */
import { api } from '../api.js';
import { toast } from '../app.js';

let agents = [];
let loading = false;
let liveAgents = [];
let liveLoading = false;
let serverLogAgents = [];
let serverLogLoading = false;
let serverLogGpuOnly = true;

function $(id) { return document.getElementById(id); }

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** ollama ps の最終ブロックから PROCESSOR 列を抽出してサマリ文字列を返す。 */
function summarizeProcessor(logText) {
  if (!logText) return '';
  // 最終の `--- ollama ps ---` 以降を対象にする
  const idx = logText.lastIndexOf('--- ollama ps ---');
  const tail = idx >= 0 ? logText.slice(idx) : logText;
  const lines = tail.split(/\r?\n/);
  const results = [];
  for (const line of lines) {
    // 空行、ヘッダ、区切りはスキップ
    if (!line.trim() || /^NAME\s+ID\s+SIZE/i.test(line) || /^---/.test(line)) continue;
    // `100% GPU` / `100% CPU` / `50%/50% CPU/GPU` 等をざっくり検出
    const m = line.match(/(\d+%\s*\/\s*\d+%\s*[A-Z/]+|\d+%\s*(?:GPU|CPU))/);
    if (m) results.push(m[0]);
  }
  return results.join(', ');
}

export function render() {
  return `
<style>
  .gpu-page { max-width: 1100px; margin: 0 auto; }
  .gpu-toolbar {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .gpu-desc {
    font-size: 0.8125rem;
    color: var(--text-muted);
    line-height: 1.6;
    margin-bottom: 1rem;
  }
  .gpu-agent-card {
    margin-bottom: 1.25rem;
    padding: 1rem 1.15rem;
  }
  .gpu-agent-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 0.6rem;
  }
  .gpu-agent-name {
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text-primary);
  }
  .gpu-agent-role {
    font-size: 0.7rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-left: 0.5rem;
  }
  .gpu-agent-meta {
    font-size: 0.7rem;
    color: var(--text-muted);
    font-family: monospace;
  }
  .gpu-summary {
    font-size: 0.8125rem;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
    font-family: monospace;
  }
  .gpu-summary-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 600;
    margin-right: 0.4rem;
  }
  .gpu-badge-gpu { background: rgba(46, 204, 113, 0.2); color: #2ecc71; }
  .gpu-badge-cpu { background: rgba(231, 76, 60, 0.2); color: #e74c3c; }
  .gpu-badge-mixed { background: rgba(241, 196, 15, 0.2); color: #f39c12; }
  .gpu-badge-none { background: var(--bg-raised); color: var(--text-muted); }
  .gpu-log {
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.75rem 0.9rem;
    max-height: 400px;
    overflow-y: auto;
    font-family: 'Cascadia Code', 'Fira Code', 'SF Mono', monospace;
    font-size: 0.72rem;
    line-height: 1.5;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .gpu-log-empty {
    color: var(--text-muted);
    font-size: 0.8125rem;
    padding: 1rem;
    text-align: center;
  }
  .gpu-error {
    color: var(--error);
    font-size: 0.8125rem;
  }
  .gpu-empty {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
  }
  .gpu-section-title {
    font-size: 1rem;
    font-weight: 600;
    color: var(--text-primary);
    margin: 1.5rem 0 0.75rem;
  }
  .gpu-section-title:first-child { margin-top: 0; }
  .gpu-live-row {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 0.5rem;
    font-family: monospace;
    font-size: 0.8125rem;
  }
  .gpu-live-metric {
    display: flex;
    align-items: baseline;
    gap: 0.3rem;
  }
  .gpu-live-metric-label {
    color: var(--text-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .gpu-live-metric-value {
    color: var(--text-primary);
    font-weight: 600;
  }
  .gpu-live-bar {
    flex: 1;
    min-width: 180px;
    height: 8px;
    background: var(--bg-raised);
    border-radius: 999px;
    overflow: hidden;
    align-self: center;
  }
  .gpu-live-bar-fill {
    height: 100%;
    background: var(--accent);
    transition: width var(--ease);
  }
  .gpu-live-bar-fill.warn { background: #f39c12; }
  .gpu-live-bar-fill.danger { background: #e74c3c; }
  .gpu-ps-table {
    width: 100%;
    border-collapse: collapse;
    font-family: monospace;
    font-size: 0.75rem;
    margin-top: 0.5rem;
  }
  .gpu-ps-table th, .gpu-ps-table td {
    text-align: left;
    padding: 0.3rem 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  .gpu-ps-table th {
    color: var(--text-muted);
    font-weight: 500;
    font-size: 0.7rem;
    text-transform: uppercase;
  }
  .gpu-ps-empty {
    font-size: 0.8125rem;
    color: var(--text-muted);
    font-style: italic;
    padding: 0.5rem 0;
  }
  .gpu-server-log-line {
    white-space: pre-wrap;
    word-break: break-word;
  }
  .gpu-server-log-line.hl-good { color: #2ecc71; }
  .gpu-server-log-line.hl-bad  { color: #e74c3c; font-weight: 600; }
  .gpu-server-log-line.hl-gpu  { color: var(--accent); }
  .gpu-log-path {
    font-size: 0.7rem;
    color: var(--text-muted);
    font-family: monospace;
    margin-bottom: 0.4rem;
  }
</style>

<div class="gpu-page">
  <div class="gpu-desc">
    Live Status は <code>nvidia-smi</code> と <code>ollama ps</code> を毎回実行して現在の状態を取得します。
    Ollama はデフォルト <code>keep_alive=5m</code> でモデルを保持するので、Discord や Chat でリクエストを送った直後に更新すると GPU/CPU 配分が確認できます。
  </div>

  <div class="gpu-section-title">Live Status</div>
  <div class="gpu-toolbar">
    <button class="btn btn-sm btn-primary" id="gpu-live-refresh">Refresh Live Status</button>
    <span id="gpu-live-time" style="font-size:0.75rem;color:var(--text-muted)"></span>
  </div>
  <div id="gpu-live-container">
    <div class="gpu-empty">Live status を取得するには Refresh を押してください。</div>
  </div>

  <div class="gpu-section-title">Ollama Server Log</div>
  <div class="gpu-desc" style="margin-bottom:0.75rem">
    Ollama 本体のサーバーログ（<code>%LOCALAPPDATA%\\Ollama\\server.log</code>）。
    起動時に CUDA / GPU 検出結果がここに出力される。
    <code>"no compatible GPUs were discovered"</code> や <code>"looking for compatible GPUs"</code> などのキーワードに注目。
  </div>
  <div class="gpu-toolbar">
    <button class="btn btn-sm btn-primary" id="gpu-server-log-refresh">Refresh Server Log</button>
    <label class="gpu-checkbox-label" style="font-size:0.8125rem;color:var(--text-muted);display:flex;align-items:center;gap:0.3rem">
      <input type="checkbox" id="gpu-server-log-gpu-only" checked> GPU 関連行のみ
    </label>
  </div>
  <div id="gpu-server-log-container">
    <div class="gpu-empty">Loading...</div>
  </div>

  <div class="gpu-section-title">Boot Logs (gpu_status.log)</div>
  <div class="gpu-toolbar">
    <button class="btn btn-sm" id="gpu-refresh">更新</button>
    <label style="font-size:0.8125rem;color:var(--text-muted)">
      表示行数:
      <select class="form-input" id="gpu-lines" style="width:auto;margin-left:0.4rem">
        <option value="100">100</option>
        <option value="200" selected>200</option>
        <option value="500">500</option>
        <option value="1000">1000</option>
      </select>
    </label>
  </div>
  <div id="gpu-agents-container">
    <div class="gpu-empty">Loading...</div>
  </div>
</div>`;
}

// ============================================================
// Live Status
// ============================================================
/** nvidia-smi CSV 行をパース（"name, mem_used, mem_total, util, temp"）。 */
function parseNvidiaCsv(text) {
  if (!text) return null;
  const firstLine = text.split(/\r?\n/)[0]?.trim();
  if (!firstLine) return null;
  const parts = firstLine.split(',').map(s => s.trim());
  if (parts.length < 5) return null;
  const [name, memUsed, memTotal, util, temp] = parts;
  const used = parseInt(memUsed, 10);
  const total = parseInt(memTotal, 10);
  return {
    name,
    memUsed: used,
    memTotal: total,
    memPct: total > 0 ? Math.round(used / total * 100) : 0,
    util: parseInt(util, 10),
    temp: parseInt(temp, 10),
  };
}

/** `ollama ps` のテキストを行配列にパースする。1行目はヘッダ想定。 */
function parseOllamaPs(text) {
  if (!text) return { headers: [], rows: [] };
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return { headers: [], rows: [] };
  // Ollama ps は空白区切り（タブまたは複数空白）。ヘッダ: NAME ID SIZE PROCESSOR CONTEXT UNTIL
  const headerCols = ['NAME', 'ID', 'SIZE', 'PROCESSOR', 'CONTEXT', 'UNTIL'];
  // 1行目がヘッダなら捨てて残りをデータとみなす
  const dataLines = /^NAME\s+ID\s+SIZE/i.test(lines[0]) ? lines.slice(1) : lines;
  const rows = dataLines.map(line => {
    // 複数空白で分割するが、最後の "UNTIL" が "5 minutes from now" のように空白を含むので
    // 最初の5カラムまで正規分割 → 残りを最後のカラムに結合
    const m = line.trim().split(/\s{2,}|\t/);
    return m;
  }).filter(r => r.length > 0 && r[0]);
  return { headers: headerCols, rows };
}

function memBarClass(pct) {
  if (pct >= 90) return 'danger';
  if (pct >= 70) return 'warn';
  return '';
}

function renderLiveAgentCard(agent) {
  const name = agent.agent_name || agent.agent_id || agent.agent || 'Agent';
  const role = agent.role || 'unknown';
  const host = agent.host ? `${agent.host}:${agent.port}` : '';

  if (!agent.alive) {
    return `
      <div class="card gpu-agent-card">
        <div class="gpu-agent-header">
          <div><span class="gpu-agent-name">${esc(name)}</span><span class="gpu-agent-role">${esc(role)}</span></div>
          <div class="gpu-agent-meta">${esc(host)}</div>
        </div>
        <div class="gpu-error">Offline${agent.error ? ` — ${esc(agent.error)}` : ''}</div>
      </div>`;
  }

  const nvidia = agent.nvidia_smi || {};
  const ollama = agent.ollama_ps || {};
  const gpu = parseNvidiaCsv(nvidia.stdout || '');
  const ps = parseOllamaPs(ollama.stdout || '');

  let gpuHtml = '';
  if (gpu) {
    const cls = memBarClass(gpu.memPct);
    gpuHtml = `
      <div class="gpu-live-row">
        <div class="gpu-live-metric"><span class="gpu-live-metric-label">GPU</span>
          <span class="gpu-live-metric-value">${esc(gpu.name)}</span></div>
        <div class="gpu-live-metric"><span class="gpu-live-metric-label">Temp</span>
          <span class="gpu-live-metric-value">${gpu.temp}°C</span></div>
        <div class="gpu-live-metric"><span class="gpu-live-metric-label">Util</span>
          <span class="gpu-live-metric-value">${gpu.util}%</span></div>
      </div>
      <div class="gpu-live-row">
        <div class="gpu-live-metric"><span class="gpu-live-metric-label">VRAM</span>
          <span class="gpu-live-metric-value">${gpu.memUsed} / ${gpu.memTotal} MiB (${gpu.memPct}%)</span></div>
        <div class="gpu-live-bar"><div class="gpu-live-bar-fill ${cls}" style="width:${gpu.memPct}%"></div></div>
      </div>`;
  } else if (nvidia.stderr) {
    gpuHtml = `<div class="gpu-error">nvidia-smi: ${esc(nvidia.stderr)}</div>`;
  } else {
    gpuHtml = `<div class="gpu-ps-empty">nvidia-smi 取得失敗</div>`;
  }

  let psHtml = '';
  if (ps.rows.length === 0) {
    psHtml = `<div class="gpu-ps-empty">No model loaded (Ollama idle or keep_alive expired)</div>`;
  } else {
    const bodyRows = ps.rows.map(r => {
      const cells = ps.headers.map((_, i) => {
        const v = r[i] ?? '';
        // PROCESSOR 列（index 3）に CPU が含まれていれば警告色
        if (i === 3 && /CPU/i.test(v)) {
          return `<td style="color:#e74c3c;font-weight:600">${esc(v)}</td>`;
        }
        if (i === 3 && /GPU/i.test(v) && !/CPU/i.test(v)) {
          return `<td style="color:#2ecc71;font-weight:600">${esc(v)}</td>`;
        }
        return `<td>${esc(v)}</td>`;
      }).join('');
      return `<tr>${cells}</tr>`;
    }).join('');
    psHtml = `
      <table class="gpu-ps-table">
        <thead><tr>${ps.headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>`;
  }

  return `
    <div class="card gpu-agent-card">
      <div class="gpu-agent-header">
        <div><span class="gpu-agent-name">${esc(name)}</span><span class="gpu-agent-role">${esc(role)}</span></div>
        <div class="gpu-agent-meta">${esc(host)}</div>
      </div>
      ${gpuHtml}
      ${psHtml}
    </div>`;
}

// ============================================================
// Ollama Server Log
// ============================================================
const GPU_KEYWORDS = [
  'gpu', 'cuda', 'nvidia', 'nvml', 'rocm', 'vulkan', 'compute',
  'vram', 'layer', 'inference', 'cpu-only', 'no compatible',
  'looking for compatible',
];

function isGpuRelated(line) {
  const low = line.toLowerCase();
  return GPU_KEYWORDS.some(k => low.includes(k));
}

function classifyLogLine(line) {
  const low = line.toLowerCase();
  if (/no compatible gpus|cpu[- ]only|failed to initialize|error.*(cuda|gpu|nvml)/i.test(line)) return 'hl-bad';
  if (/found.*gpu|inference compute.*cuda|using.*gpu|cuda.*v\d|nvidia.*driver|\d+\s*mib free/i.test(line)) return 'hl-good';
  if (isGpuRelated(line)) return 'hl-gpu';
  return '';
}

function renderServerLogAgent(agent) {
  const name = agent.agent_name || agent.agent_id || agent.agent || 'Agent';
  const role = agent.role || 'unknown';
  const host = agent.host ? `${agent.host}:${agent.port}` : '';

  if (!agent.alive) {
    return `
      <div class="card gpu-agent-card">
        <div class="gpu-agent-header">
          <div><span class="gpu-agent-name">${esc(name)}</span><span class="gpu-agent-role">${esc(role)}</span></div>
          <div class="gpu-agent-meta">${esc(host)}</div>
        </div>
        <div class="gpu-error">Offline${agent.error ? ` — ${esc(agent.error)}` : ''}</div>
      </div>`;
  }

  const pathHtml = agent.path
    ? `<div class="gpu-log-path">${esc(agent.path)}</div>`
    : `<div class="gpu-log-path" style="color:var(--error)">server.log not found (tried: ${esc((agent.tried || []).join(', '))})</div>`;

  const allLines = Array.isArray(agent.logs) ? agent.logs : [];
  const filtered = serverLogGpuOnly ? allLines.filter(isGpuRelated) : allLines;

  let body = '';
  if (!agent.exists) {
    body = `<div class="gpu-log-empty">server.log が見つかりませんでした。Ollama がまだ起動していないか、パスが異なります。</div>`;
  } else if (!filtered.length) {
    body = `<div class="gpu-log-empty">${serverLogGpuOnly ? 'GPU 関連行は見つかりませんでした。「GPU 関連行のみ」を外して全文確認してください。' : 'ログは空です。'}</div>`;
  } else {
    const html = filtered.map(line => {
      const cls = classifyLogLine(line);
      return `<div class="gpu-server-log-line ${cls}">${esc(line)}</div>`;
    }).join('');
    body = `<div class="gpu-log" style="max-height:500px">${html}</div>`;
  }

  const count = serverLogGpuOnly
    ? `<span class="gpu-agent-meta">${filtered.length} / ${allLines.length} 行（フィルタ後）</span>`
    : `<span class="gpu-agent-meta">${allLines.length} 行</span>`;

  return `
    <div class="card gpu-agent-card">
      <div class="gpu-agent-header">
        <div><span class="gpu-agent-name">${esc(name)}</span><span class="gpu-agent-role">${esc(role)}</span></div>
        <div>${count}</div>
      </div>
      ${pathHtml}
      ${body}
    </div>`;
}

async function loadServerLog() {
  if (serverLogLoading) return;
  serverLogLoading = true;
  const container = $('gpu-server-log-container');
  const btn = $('gpu-server-log-refresh');
  if (btn) btn.disabled = true;
  if (container) container.innerHTML = `<div class="gpu-empty">取得中...</div>`;
  try {
    const data = await api('/api/gpu-status/ollama-server-log', { params: { lines: 500 } });
    serverLogAgents = data?.agents || [];
    if (!serverLogAgents.length) {
      container.innerHTML = `<div class="gpu-empty">Agent が登録されていません。</div>`;
    } else {
      container.innerHTML = serverLogAgents.map(renderServerLogAgent).join('');
    }
  } catch (err) {
    console.error('Load ollama server log:', err);
    container.innerHTML = `<div class="gpu-empty" style="color:var(--error)">取得失敗: ${esc(err.message)}</div>`;
    toast('Ollama server log 取得失敗', 'error');
  } finally {
    serverLogLoading = false;
    if (btn) btn.disabled = false;
  }
}

function rerenderServerLog() {
  const container = $('gpu-server-log-container');
  if (!container || !serverLogAgents.length) return;
  container.innerHTML = serverLogAgents.map(renderServerLogAgent).join('');
}

async function loadLive() {
  if (liveLoading) return;
  liveLoading = true;
  const container = $('gpu-live-container');
  const btn = $('gpu-live-refresh');
  const timeEl = $('gpu-live-time');
  if (btn) btn.disabled = true;
  container.innerHTML = `<div class="gpu-empty">取得中...</div>`;
  try {
    const data = await api('/api/gpu-status/live');
    liveAgents = data?.agents || [];
    if (!liveAgents.length) {
      container.innerHTML = `<div class="gpu-empty">Agent が登録されていません。</div>`;
    } else {
      container.innerHTML = liveAgents.map(renderLiveAgentCard).join('');
    }
    if (timeEl) {
      const now = new Date();
      timeEl.textContent = `取得: ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    }
  } catch (err) {
    console.error('Load gpu live:', err);
    container.innerHTML = `<div class="gpu-empty" style="color:var(--error)">取得失敗: ${esc(err.message)}</div>`;
    toast('Live status 取得失敗', 'error');
  } finally {
    liveLoading = false;
    if (btn) btn.disabled = false;
  }
}

function badgeFor(summary) {
  if (!summary) return '<span class="gpu-summary-badge gpu-badge-none">No data</span>';
  if (/CPU\/GPU|GPU\/CPU/i.test(summary)) {
    return '<span class="gpu-summary-badge gpu-badge-mixed">Mixed</span>';
  }
  if (/GPU/i.test(summary) && !/CPU/i.test(summary)) {
    return '<span class="gpu-summary-badge gpu-badge-gpu">GPU</span>';
  }
  if (/CPU/i.test(summary)) {
    return '<span class="gpu-summary-badge gpu-badge-cpu">CPU</span>';
  }
  return '<span class="gpu-summary-badge gpu-badge-none">?</span>';
}

function renderAgentCard(agent) {
  const name = agent.agent_name || agent.agent_id || agent.agent || 'Agent';
  const role = agent.role || 'unknown';
  const host = agent.host ? `${agent.host}:${agent.port}` : '';
  const alive = agent.alive === true;

  if (!alive) {
    return `
      <div class="card gpu-agent-card">
        <div class="gpu-agent-header">
          <div>
            <span class="gpu-agent-name">${esc(name)}</span>
            <span class="gpu-agent-role">${esc(role)}</span>
          </div>
          <div class="gpu-agent-meta">${esc(host)}</div>
        </div>
        <div class="gpu-error">Offline${agent.error ? ` — ${esc(agent.error)}` : ''}</div>
      </div>`;
  }

  const logs = Array.isArray(agent.logs) ? agent.logs : [];
  const logText = logs.join('\n');
  const summary = summarizeProcessor(logText);
  const exists = agent.exists !== false;

  let body = '';
  if (!exists) {
    body = `<div class="gpu-log-empty">ログファイルがまだありません。Agent を再起動すると生成されます。</div>`;
  } else if (!logText.trim()) {
    body = `<div class="gpu-log-empty">ログは空です。</div>`;
  } else {
    body = `<pre class="gpu-log">${esc(logText)}</pre>`;
  }

  return `
    <div class="card gpu-agent-card">
      <div class="gpu-agent-header">
        <div>
          <span class="gpu-agent-name">${esc(name)}</span>
          <span class="gpu-agent-role">${esc(role)}</span>
        </div>
        <div class="gpu-agent-meta">${esc(host)}</div>
      </div>
      ${summary ? `<div class="gpu-summary">${badgeFor(summary)}${esc(summary)}</div>` : ''}
      ${body}
    </div>`;
}

async function load() {
  if (loading) return;
  loading = true;
  const container = $('gpu-agents-container');
  try {
    const lines = $('gpu-lines')?.value || 200;
    const data = await api('/api/gpu-status/logs', { params: { lines } });
    agents = data?.agents || [];
    if (!agents.length) {
      container.innerHTML = `<div class="gpu-empty">Agent が登録されていません。</div>`;
      return;
    }
    container.innerHTML = agents.map(renderAgentCard).join('');
  } catch (err) {
    console.error('Load gpu-status:', err);
    container.innerHTML = `<div class="gpu-empty" style="color:var(--error)">ログ取得に失敗しました: ${esc(err.message)}</div>`;
    toast('GPU ログ取得失敗', 'error');
  } finally {
    loading = false;
  }
}

export async function mount() {
  $('gpu-refresh')?.addEventListener('click', () => load());
  $('gpu-lines')?.addEventListener('change', () => load());
  $('gpu-live-refresh')?.addEventListener('click', () => loadLive());
  $('gpu-server-log-refresh')?.addEventListener('click', () => loadServerLog());
  $('gpu-server-log-gpu-only')?.addEventListener('change', (e) => {
    serverLogGpuOnly = e.target.checked;
    rerenderServerLog();
  });
  await Promise.all([load(), loadLive(), loadServerLog()]);
}

export function unmount() {
  agents = [];
  loading = false;
  liveAgents = [];
  liveLoading = false;
  serverLogAgents = [];
  serverLogLoading = false;
  serverLogGpuOnly = true;
}
