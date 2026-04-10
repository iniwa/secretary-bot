/** Maintenance page. */
import { api, apiBatch } from '../api.js';
import { toast } from '../app.js';

function $(id) { return document.getElementById(id); }

let _active = false;

const BREAKER_BADGE = {
  closed:    'badge-success',
  open:      'badge-error',
  half_open: 'badge-warning',
};

export function render() {
  return `
<style>
  .maint-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1.25rem;
  }
  @media (max-width: 860px) {
    .maint-grid { grid-template-columns: 1fr; }
  }
  .maint-grid .card-header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.6rem;
  }
  .card-body { margin-top: 0.75rem; }
  .card-desc {
    font-size: 0.8125rem;
    color: var(--text-secondary);
    margin-bottom: 0.75rem;
    line-height: 1.5;
  }
  .warning-text {
    font-size: 0.75rem;
    color: var(--warning);
    background: var(--warning-muted);
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius-sm);
    margin-bottom: 0.85rem;
  }
  .result-box {
    margin-top: 0.75rem;
    padding: 0.75rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 0.8125rem;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
    display: none;
  }
  .result-box.visible { display: block; }
  .result-item {
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--border);
  }
  .result-item:last-child { border-bottom: none; }
  .result-label {
    font-weight: 600;
    color: var(--text-secondary);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .result-value {
    color: var(--text-primary);
  }
  .card-full {
    grid-column: 1 / -1;
  }
  .agent-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
  }
  .agent-row:last-child { border-bottom: none; }
  .agent-name {
    font-weight: 500;
    color: var(--text-primary);
    min-width: 100px;
  }
  .agent-status {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.8125rem;
    min-width: 80px;
  }
  .agent-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    flex: 1;
    min-width: 0;
  }
  .agent-controls {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-left: auto;
    flex-shrink: 0;
  }
  .block-reasons {
    display: flex;
    gap: 0.35rem;
    flex-wrap: wrap;
  }
  .pause-remaining {
    font-size: 0.75rem;
    color: var(--warning);
  }
  .btn-row {
    display: flex;
    gap: 0.75rem;
    flex-wrap: wrap;
    align-items: center;
  }
  .btn[disabled] {
    opacity: 0.5;
    pointer-events: none;
  }
  .spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid var(--text-muted);
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

<div class="maint-grid">

  <!-- Code Update -->
  <div class="card">
    <div class="card-header">
      <h3>Code Update</h3>
      <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
        <span class="mono" id="m-current-version" style="color:var(--text-muted);font-size:0.8125rem">---</span>
        <span id="m-version-status" style="font-size:0.75rem"></span>
      </div>
    </div>
    <div class="card-body">
      <div class="card-desc">Pull latest code from git, update submodules, and restart agents.</div>
      <div class="warning-text">Container will restart after update</div>
      <button class="btn btn-primary" id="m-update-code">Update Code</button>
      <div class="result-box" id="m-update-result"></div>
    </div>
  </div>

  <!-- Container Restart -->
  <div class="card">
    <div class="card-header"><h3>Container Restart</h3></div>
    <div class="card-body">
      <div class="card-desc">Restart the bot container. The page will become temporarily unavailable.</div>
      <div class="warning-text">Page will be unreachable during restart</div>
      <button class="btn btn-danger" id="m-restart">Restart Container</button>
      <div class="result-box" id="m-restart-result"></div>
    </div>
  </div>

  <!-- Loaded Units -->
  <div class="card card-full">
    <div class="card-header"><h3>Loaded Units</h3></div>
    <div class="card-body">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th>Delegate To</th>
              <th>Circuit Breaker</th>
            </tr>
          </thead>
          <tbody id="m-units-body">
            <tr><td colspan="4" style="color:var(--text-muted)">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Agent Management -->
  <div class="card card-full">
    <div class="card-header">
      <h3>Agent Management</h3>
      <div style="display:flex;gap:0.5rem">
        <button class="btn btn-sm" id="m-restart-agents">Restart All Agents</button>
        <button class="btn btn-sm" id="m-ollama-recheck">Recheck Ollama</button>
      </div>
    </div>
    <div class="card-body">
      <div id="m-agents-list">
        <div style="color:var(--text-muted);font-size:0.8125rem">Loading...</div>
      </div>
      <div class="result-box" id="m-agents-result"></div>
    </div>
  </div>

  <!-- Submodule Update -->
  <div class="card">
    <div class="card-header">
      <h3>Submodule Update</h3>
      <span class="mono" id="m-current-relay-version" style="color:var(--text-muted);font-size:0.8125rem">---</span>
    </div>
    <div class="card-body">
      <div class="card-desc">Update the Input Relay submodule on all agents.</div>
      <button class="btn btn-primary" id="m-update-relay">Update Input Relay</button>
      <div class="result-box" id="m-relay-result"></div>
    </div>
  </div>

</div>`;
}

// ---- helpers ----

function setLoading(btn, loading) {
  if (loading) {
    // 既に loading 状態なら何もしない。2 回目の保存でスピナー HTML を
    // _origHTML に焼き付けてしまうと、finally 復元でボタンが「Running」の
    // まま戻らなくなる（2 回目以降の click が Running から帰らない症状の根本原因）。
    if (btn.dataset.loadingState === '1') return;
    btn._origHTML = btn.innerHTML;
    btn.dataset.loadingState = '1';
    btn.innerHTML = '<span class="spinner"></span> Running...';
    btn.disabled = true;
  } else {
    btn.innerHTML = btn._origHTML || btn.textContent;
    btn.dataset.loadingState = '';
    btn.disabled = false;
  }
}

function showResult(boxId, html) {
  const box = $(boxId);
  box.innerHTML = html;
  box.classList.add('visible');
}

function resultItem(label, value) {
  return `<div class="result-item"><span class="result-label">${label}: </span><span class="result-value">${esc(String(value))}</span></div>`;
}

/** Agent 実行結果配列を resultItem の連続 HTML に変換。 */
function agentResultList(label, agents) {
  if (!agents || agents.length === 0) return '';
  return agents.map(a => {
    const name = a.name || a.id || 'unknown';
    const status = a.success ? 'OK' : 'Failed';
    const detail = a.message || a.detail || a.error || a.status || '';
    return resultItem(`${label}: ${name}`, detail ? `${status} - ${detail}` : status);
  }).join('');
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function breakerBadge(state) {
  const cls = BREAKER_BADGE[state] || 'badge-muted';
  return `<span class="badge ${cls}">${esc(state || 'unknown')}</span>`;
}

/**
 * 再起動完了を /health ポーリングで待つ。
 * - 一度でも接続失敗 → 復帰成功を検知したらOK
 * - あるいは version (commit hash) が変わったらOK
 */
async function waitForRestart(previousVersion, { timeoutMs = 60000, intervalMs = 1500 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let wentDown = false;
  // 最初に少し待つ（background_tasks の 2 秒遅延再起動に被らないように）
  await new Promise(r => setTimeout(r, 2000));
  while (Date.now() < deadline) {
    try {
      const res = await fetch('/health', { cache: 'no-store' });
      if (!res.ok) {
        wentDown = true;
      } else {
        const data = await res.json().catch(() => null);
        const version = data?.version || null;
        if (previousVersion && version && version !== previousVersion) return true;
        if (wentDown) return true;
      }
    } catch (e) {
      wentDown = true;
    }
    await new Promise(r => setTimeout(r, intervalMs));
  }
  return false;
}

function delegateBadge(delegateTo) {
  if (!delegateTo) return '<span style="color:var(--text-muted)">-</span>';
  return `<span class="badge badge-info">${esc(delegateTo)}</span>`;
}

// ---- data loading ----

async function loadUnits() {
  try {
    const data = await api('/api/units/loaded');
    if (!_active) return;
    const units = data?.units || [];
    if (units.length === 0) {
      $('m-units-body').innerHTML = '<tr><td colspan="4" style="color:var(--text-muted)">No units loaded</td></tr>';
      return;
    }
    $('m-units-body').innerHTML = units.map(u => `
      <tr>
        <td>${esc(u.name)}</td>
        <td>${esc(u.description || '-')}</td>
        <td>${delegateBadge(u.delegate_to)}</td>
        <td>${breakerBadge(u.breaker_state)}</td>
      </tr>
    `).join('');
  } catch (err) {
    if (!_active) return;
    console.error('Load units:', err);
    $('m-units-body').innerHTML = '<tr><td colspan="4" style="color:var(--error)">Failed to load</td></tr>';
  }
}

async function loadAgents() {
  try {
    const data = await api('/api/status');
    if (!_active) return;
    const agents = data?.agents || [];
    if (agents.length === 0) {
      $('m-agents-list').innerHTML = '<div style="color:var(--text-muted);font-size:0.8125rem">No agents configured</div>';
      return;
    }
    $('m-agents-list').innerHTML = agents.map(a => {
      const isRestarting = a.status === 'restarting';
      const isPaused = !!a.paused;
      let dotClass, statusLabel;
      if (isRestarting) {
        dotClass = 'warning pulse';
        statusLabel = `Restarting (${a.restart_elapsed}s)`;
      } else if (isPaused) {
        dotClass = 'warning';
        const rem = a.pause_remaining;
        const remText = rem != null ? ` (${Math.ceil(rem / 60)}min)` : '';
        statusLabel = `Paused${remText}`;
      } else if (a.alive) {
        dotClass = 'online';
        statusLabel = 'Online';
      } else {
        dotClass = 'error';
        statusLabel = 'Offline';
      }
      const currentMode = a.mode || 'auto';
      const agentIdEsc = esc(String(a.id));
      const agentNameEsc = esc(a.name || a.id);

      // ブロック理由バッジ
      const reasons = a.block_reasons || [];
      const reasonsHtml = reasons.length > 0
        ? `<span class="block-reasons">${reasons.map(r => `<span class="badge badge-warning">${esc(r)}</span>`).join('')}</span>`
        : '';

      // 一時停止コントロール
      const pauseCtrl = isPaused
        ? `<button class="btn btn-sm agent-unpause-btn" data-agent-id="${agentIdEsc}">Unpause</button>`
        : `<select class="form-input agent-pause-select" data-agent-id="${agentIdEsc}" style="width:auto;max-width:120px">
             <option value="">Pause...</option>
             <option value="30">30min</option>
             <option value="60">1h</option>
             <option value="180">3h</option>
           </select>`;

      return `
        <div class="agent-row">
          <span class="agent-info">
            <span class="agent-name">${agentNameEsc}</span>
            <span class="agent-status">
              <span class="status-dot ${dotClass}"></span>
              ${statusLabel}
            </span>
            ${reasonsHtml}
          </span>
          <span class="agent-controls">
            ${pauseCtrl}
            <button class="btn btn-sm agent-restart-btn" data-action="restart-one" data-agent-id="${agentIdEsc}" data-agent-name="${agentNameEsc}">Restart</button>
            <select class="form-input agent-mode-select" data-agent-id="${agentIdEsc}" style="width:auto;max-width:140px"
              ${isPaused ? 'disabled' : ''}>
              <option value="auto"${currentMode === 'auto' ? ' selected' : ''}>auto</option>
              <option value="allow"${currentMode === 'allow' ? ' selected' : ''}>allow</option>
              <option value="deny"${currentMode === 'deny' ? ' selected' : ''}>deny</option>
            </select>
          </span>
        </div>`;
    }).join('');

    // Attach mode change listeners
    document.querySelectorAll('.agent-mode-select').forEach(sel => {
      sel.addEventListener('change', async (e) => {
        const agentId = e.target.dataset.agentId;
        const mode = e.target.value;
        try {
          await api('/api/delegation-mode', { method: 'POST', body: { agent_id: agentId, mode } });
          toast(`Agent mode set to ${mode}`, 'success');
        } catch (err) {
          console.error('Set delegation mode:', err);
          toast('Failed to set agent mode', 'error');
        }
      });
    });

    // Attach per-agent restart listeners
    document.querySelectorAll('.agent-restart-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const target = e.currentTarget;
        const agentId = target.dataset.agentId;
        const name = target.dataset.agentName || agentId;
        if (!window.confirm(`Restart agent "${name}"?`)) return;
        try {
          await api(`/api/agents/${encodeURIComponent(agentId)}/restart`, { method: 'POST' });
          toast('Agent restart requested', 'success');
        } catch (err) {
          console.error('Restart agent:', err);
          toast('Restart failed: ' + err.message, 'error');
        }
        setTimeout(() => loadAgents(), 8000);
      });
    });

    // Attach pause select listeners (30min / 1h / 3h)
    document.querySelectorAll('.agent-pause-select').forEach(sel => {
      sel.addEventListener('change', async (e) => {
        const minutes = parseInt(e.target.value, 10);
        if (!minutes) return;
        const agentId = e.target.dataset.agentId;
        e.target.value = '';  // リセット
        try {
          await api(`/api/agents/${encodeURIComponent(agentId)}/pause`, {
            method: 'POST', body: { duration_minutes: minutes },
          });
          toast(`${minutes} 分間一時停止しました`, 'success');
          loadAgents();
        } catch (err) {
          console.error('Pause agent:', err);
          toast('Pause failed: ' + err.message, 'error');
        }
      });
    });

    // Attach unpause listeners
    document.querySelectorAll('.agent-unpause-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const agentId = e.currentTarget.dataset.agentId;
        try {
          await api(`/api/agents/${encodeURIComponent(agentId)}/pause`, { method: 'DELETE' });
          toast('一時停止を解除しました', 'success');
          loadAgents();
        } catch (err) {
          console.error('Unpause agent:', err);
          toast('Unpause failed: ' + err.message, 'error');
        }
      });
    });
  } catch (err) {
    if (!_active) return;
    console.error('Load agents:', err);
    $('m-agents-list').innerHTML = '<div style="color:var(--error);font-size:0.8125rem">Failed to load agents</div>';
  }
}

async function loadVersion() {
  try {
    const v = await api('/api/version');
    if (!_active) return;
    const mainEl = $('m-current-version');
    const relayEl = $('m-current-relay-version');
    if (mainEl) mainEl.textContent = v?.main || '---';
    if (relayEl) relayEl.textContent = v?.input_relay || '---';
  } catch (err) {
    if (!_active) return;
    console.error('Load version:', err);
  }
}

async function verifyVersions() {
  const statusEl = $('m-version-status');
  if (!statusEl) return;
  try {
    const data = await api('/api/agents/versions');
    if (!_active) return;
    const pi = data?.pi || '?';
    const agents = data?.agents || [];
    const allMatch = !!data?.all_match;
    const anyDead = !!data?.any_dead;

    if (allMatch && !anyDead) {
      statusEl.innerHTML = '<span style="color:var(--success)">&#10003; in sync</span>';
      return;
    }

    if (!allMatch) {
      const parts = agents.map(a => {
        const name = esc(a.name || a.id || '?');
        const ver = esc(a.version || '?');
        return `${name}:${ver}`;
      }).join(', ');
      statusEl.innerHTML = `<span style="color:var(--warning)">&#9888; Mismatch: Pi=${esc(pi)}, Agents=[${parts}]</span>`;
      return;
    }

    // allMatch && anyDead
    const deadCount = agents.filter(a => !a.alive).length;
    statusEl.innerHTML = `<span style="color:var(--warning)">&#9888; ${deadCount} agent(s) offline</span>`;
  } catch (err) {
    if (!_active) return;
    console.error('Verify versions:', err);
    statusEl.innerHTML = '';
  }
}

// ---- mount ----

export function unmount() {
  _active = false;
}

export async function mount() {
  _active = true;
  // イベントリスナーを先に登録（データロード完了を待たずにボタンを押せるようにする）
  // Code Update button
  $('m-update-code').addEventListener('click', async () => {
    const btn = $('m-update-code');
    setLoading(btn, true);
    // 再起動後のリロード判定用に現在の version を事前取得
    let previousVersion = null;
    try {
      const h = await fetch('/health', { cache: 'no-store' });
      if (h.ok) previousVersion = (await h.json())?.version || null;
    } catch (_) { /* noop */ }
    try {
      const res = await api('/api/update-code', { method: 'POST' });
      let html = '';
      html += resultItem('Updated', res.updated ? 'Yes' : 'No');
      html += resultItem('Message', res.message || '-');
      if (res.restarted !== undefined) {
        html += resultItem('Restarted', res.restarted ? 'Yes' : 'No');
      }
      if (res.restart_detail) {
        html += resultItem('Restart Detail', res.restart_detail);
      }
      html += agentResultList('Update', res.agents);
      html += agentResultList('Restart', res.agents_restart);
      showResult('m-update-result', html);
      toast(res.updated ? 'Code updated successfully' : 'Already up to date', res.updated ? 'success' : 'info');

      // 再起動が走る場合は /health を監視してリロード
      if (res.restarted) {
        btn.innerHTML = '<span class="spinner"></span> Waiting for restart...';
        toast('再起動を待機中…', 'info');
        const ok = await waitForRestart(previousVersion);
        if (ok) {
          btn.innerHTML = '<span class="spinner"></span> Reloading...';
          toast('再起動完了 — ページを再読み込みします', 'success');
          setTimeout(() => location.reload(), 500);
          return; // finally の setLoading(false) はスキップ（遷移するため）
        }
        toast('再起動確認タイムアウト — 手動で再読み込みしてください', 'warning');
      }
    } catch (err) {
      console.error('Update code:', err);
      showResult('m-update-result', resultItem('Error', err.message));
      toast('Code update failed', 'error');
    } finally {
      setLoading(btn, false);
    }
  });

  // Container Restart button
  $('m-restart').addEventListener('click', async () => {
    if (!window.confirm('Are you sure you want to restart the container? The page will be temporarily unavailable.')) {
      return;
    }
    const btn = $('m-restart');
    setLoading(btn, true);
    try {
      const res = await api('/api/restart', { method: 'POST' });
      let html = '';
      html += resultItem('Restarted', res.restarted ? 'Yes' : 'No');
      if (res.detail) {
        html += resultItem('Detail', res.detail);
      }
      showResult('m-restart-result', html);
      toast('Container restart initiated', 'success');
    } catch (err) {
      console.error('Restart:', err);
      showResult('m-restart-result', resultItem('Error', err.message));
      toast('Restart request failed', 'error');
    } finally {
      setLoading(btn, false);
    }
  });

  // Restart All Agents button（コード更新なしで全 Agent を自己再起動させる）
  $('m-restart-agents').addEventListener('click', async () => {
    if (!window.confirm('全 Windows Agent を再起動しますか？ STT/OBS が一時的に停止します。')) {
      return;
    }
    const btn = $('m-restart-agents');
    setLoading(btn, true);
    try {
      const res = await api('/api/agents/restart-all', { method: 'POST' });
      let html = '';
      html += resultItem('Message', res.message || '-');
      html += agentResultList('Restart', res.agents);
      showResult('m-agents-result', html);
      toast(res.message || 'Restart requested', res.success ? 'success' : 'warning');
      // 数秒後に状態再取得（Agent 復帰検知用）
      setTimeout(() => loadAgents(), 8000);
    } catch (err) {
      console.error('Restart agents:', err);
      showResult('m-agents-result', resultItem('Error', err.message));
      toast('Agent restart failed', 'error');
    } finally {
      setLoading(btn, false);
    }
  });

  // Recheck Ollama button
  $('m-ollama-recheck').addEventListener('click', async () => {
    const btn = $('m-ollama-recheck');
    setLoading(btn, true);
    try {
      await api('/api/ollama-recheck', { method: 'POST' });
      toast('Ollama recheck triggered', 'success');
    } catch (err) {
      console.error('Ollama recheck:', err);
      toast('Ollama recheck failed', 'error');
    } finally {
      setLoading(btn, false);
    }
  });

  // Submodule Update button
  // Pi 側で submodule 更新 → commit & push → 全 Agent へ git pull 反映
  $('m-update-relay').addEventListener('click', async () => {
    const btn = $('m-update-relay');
    setLoading(btn, true);
    try {
      const res = await api('/api/tools/input-relay/update', { method: 'POST' });
      let html = '';
      html += resultItem('Updated', res.updated ? 'Yes' : 'No');
      if (res.old_hash || res.new_hash) {
        html += resultItem('Submodule', `${res.old_hash || '?'} → ${res.new_hash || '?'}`);
      }
      if (res.message) {
        html += resultItem('Message', res.message);
      }
      html += agentResultList('Update', res.agents);
      html += agentResultList('Tool Restart', res.agents_tool_restart);
      showResult('m-relay-result', html);
      toast(
        res.updated ? `Input Relay updated (${res.new_hash})` : 'Already up to date',
        res.updated ? 'success' : 'info'
      );
      // 更新後の最新ハッシュを再取得して表示更新
      loadVersion();
    } catch (err) {
      console.error('Update relay:', err);
      showResult('m-relay-result', resultItem('Error', err.message));
      toast('Input Relay update failed', 'error');
    } finally {
      setLoading(btn, false);
    }
  });

  // データロード（イベントリスナー登録後に非同期実行）
  Promise.all([loadUnits(), loadAgents(), loadVersion(), verifyVersions()]);
}
