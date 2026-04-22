/** GPU Status page — shows each Windows Agent's `logs/gpu_status.log`
 * (nvidia-smi + ollama ps output appended by start_agent.bat at boot). */
import { api } from '../api.js';
import { toast } from '../app.js';

let agents = [];
let loading = false;

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
</style>

<div class="gpu-page">
  <div class="gpu-desc">
    各 Windows Agent の <code>windows-agent/logs/gpu_status.log</code> を表示します。<br>
    このログは <code>start_agent.bat</code> が Ollama 起動直後に <code>nvidia-smi</code> と <code>ollama ps</code> の出力を追記したものです。
    <code>ollama ps</code> の <code>PROCESSOR</code> 列が <code>100% GPU</code> なら OK、<code>CPU</code> を含む場合は VRAM 不足 or CUDA 問題の可能性あり。
  </div>
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
  await load();
}

export function unmount() {
  agents = [];
  loading = false;
}
