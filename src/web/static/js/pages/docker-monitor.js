/** Docker Monitor page — errors list, exclusion patterns, Discord notification toggle. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeTab = 'errors';
let errors = [];
let errorsTotal = 0;
let errorsLoading = false;
let showDismissed = false;
let exclusions = [];
let notifyDiscord = false;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtTime(str) {
  if (!str) return '---';
  try {
    const d = new Date(String(str).replace(' ', 'T'));
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `${mm}/${dd} ${hh}:${mi}`;
  } catch { return String(str); }
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .dm-tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .dm-tab {
    padding: 0.4rem 1rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 0.8125rem;
    font-weight: 500;
    transition: all var(--ease);
  }
  .dm-tab:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .dm-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .dm-panel {
    display: none;
  }
  .dm-panel.active {
    display: block;
  }
  .dm-toolbar {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }
  .dm-stats {
    font-size: 0.8125rem;
    color: var(--text-muted);
    margin-right: auto;
  }
  .dm-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .dm-card {
    padding: 0.9rem 1.1rem;
  }
  .dm-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.4rem;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  .dm-container-name {
    font-weight: 600;
    font-size: 0.875rem;
    color: var(--accent);
    font-family: monospace;
  }
  .dm-count-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    color: var(--text-secondary);
  }
  .dm-message {
    margin: 0.4rem 0;
    padding: 0.5rem 0.7rem;
    background: var(--bg-raised);
    border-radius: var(--radius);
    font-family: monospace;
    font-size: 0.75rem;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 8rem;
    overflow-y: auto;
    color: var(--text);
  }
  .dm-meta {
    display: flex;
    gap: 1rem;
    font-size: 0.7rem;
    color: var(--text-muted);
    flex-wrap: wrap;
  }
  .dm-card-footer {
    display: flex;
    justify-content: flex-end;
    gap: 0.4rem;
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--border);
  }
  .dm-empty {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
    font-size: 0.9rem;
  }
  .dm-add-form {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .dm-add-form input {
    padding: 0.4rem 0.75rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--text);
    font-size: 0.8125rem;
  }
  .dm-add-form input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .dm-add-pattern {
    flex: 2;
    min-width: 150px;
    font-family: monospace;
  }
  .dm-add-reason {
    flex: 3;
    min-width: 150px;
  }
  .dm-exc-card {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.75rem;
    padding: 0.7rem 1rem;
  }
  .dm-exc-body {
    flex: 1;
    min-width: 0;
  }
  .dm-exc-pattern {
    font-family: monospace;
    font-size: 0.8125rem;
    color: var(--text);
    word-break: break-all;
  }
  .dm-exc-reason {
    font-size: 0.7rem;
    color: var(--text-muted);
    margin-top: 0.2rem;
  }
  .dm-settings-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
  }
  .dm-settings-label {
    font-size: 0.9rem;
    color: var(--text);
    font-weight: 500;
  }
  .dm-settings-desc {
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-top: 0.25rem;
  }
  .dm-toggle {
    position: relative;
    width: 44px;
    height: 24px;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: 999px;
    cursor: pointer;
    transition: all var(--ease);
    flex-shrink: 0;
  }
  .dm-toggle.on {
    background: var(--accent);
    border-color: var(--accent);
  }
  .dm-toggle::after {
    content: '';
    position: absolute;
    top: 2px;
    left: 2px;
    width: 18px;
    height: 18px;
    background: #fff;
    border-radius: 50%;
    transition: transform var(--ease);
  }
  .dm-toggle.on::after {
    transform: translateX(20px);
  }
  .dm-checkbox-label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8125rem;
    color: var(--text-secondary);
    cursor: pointer;
    user-select: none;
  }

  @media (max-width: 600px) {
    .dm-card, .dm-exc-card, .dm-settings-row {
      padding: 0.75rem;
    }
    .dm-toolbar {
      flex-direction: column;
      align-items: stretch;
    }
    .dm-stats {
      margin-right: 0;
    }
  }
</style>

<div class="dm-page">
  <div class="dm-tabs">
    <button class="dm-tab active" data-tab="errors">Errors</button>
    <button class="dm-tab" data-tab="exclusions">Exclusions</button>
    <button class="dm-tab" data-tab="settings">Settings</button>
  </div>

  <!-- Errors panel -->
  <div class="dm-panel active" id="dm-panel-errors">
    <div class="dm-toolbar">
      <div class="dm-stats" id="dm-errors-stats">Loading...</div>
      <label class="dm-checkbox-label">
        <input type="checkbox" id="dm-show-dismissed"> 対応済みを表示
      </label>
      <button class="btn btn-sm" id="dm-refresh-errors">更新</button>
      <button class="btn btn-sm btn-danger" id="dm-dismiss-all">全て対応済みに</button>
    </div>
    <div class="dm-list" id="dm-errors-list">
      <div class="dm-empty">Loading...</div>
    </div>
  </div>

  <!-- Exclusions panel -->
  <div class="dm-panel" id="dm-panel-exclusions">
    <div class="dm-add-form">
      <input type="text" class="dm-add-pattern" id="dm-exc-pattern" placeholder="除外パターン (部分一致)" />
      <input type="text" class="dm-add-reason" id="dm-exc-reason" placeholder="理由 (任意)" />
      <button class="btn btn-sm" id="dm-exc-add">追加</button>
    </div>
    <div class="dm-list" id="dm-exclusions-list">
      <div class="dm-empty">Loading...</div>
    </div>
  </div>

  <!-- Settings panel -->
  <div class="dm-panel" id="dm-panel-settings">
    <div class="card dm-settings-row">
      <div>
        <div class="dm-settings-label">Discord 通知</div>
        <div class="dm-settings-desc">
          エラー検出時に Discord へ通知します。OFF の場合は WebGUI 上でのみ確認可能です。
        </div>
      </div>
      <div class="dm-toggle" id="dm-notify-toggle"></div>
    </div>
  </div>
</div>`;
}

// ============================================================
// Errors loading
// ============================================================
async function loadErrors() {
  if (errorsLoading) return;
  errorsLoading = true;
  try {
    const data = await api('/api/docker-monitor/errors', {
      params: { dismissed: showDismissed ? 1 : 0, limit: 200, offset: 0 },
    });
    errors = data?.items || [];
    errorsTotal = data?.total ?? 0;
    renderErrors();
  } catch (err) {
    toast('エラー一覧の取得に失敗しました: ' + err.message, 'error');
  } finally {
    errorsLoading = false;
  }
}

function renderErrors() {
  const stats = $('dm-errors-stats');
  if (stats) {
    const label = showDismissed ? '対応済みエラー' : '未対応エラー';
    stats.textContent = `${label}: ${errorsTotal}件`;
  }

  const list = $('dm-errors-list');
  if (!list) return;

  if (!errors.length) {
    list.innerHTML = `<div class="dm-empty">${showDismissed ? '対応済みエラーはありません。' : '未対応のエラーはありません。'}</div>`;
    return;
  }

  list.innerHTML = errors.map(e => `
    <div class="card dm-card" data-error-id="${e.id}">
      <div class="dm-card-header">
        <span class="dm-container-name">${esc(e.container_name)}</span>
        <span class="dm-count-badge">${e.count}回検出</span>
      </div>
      <pre class="dm-message">${esc(e.message)}</pre>
      <div class="dm-meta">
        <span>初回: ${fmtTime(e.first_seen)}</span>
        <span>最終: ${fmtTime(e.last_seen)}</span>
      </div>
      <div class="dm-card-footer">
        ${!showDismissed ? `<button class="btn btn-sm" data-action="dismiss">対応済み</button>` : ''}
        <button class="btn btn-sm btn-danger" data-action="delete">削除</button>
      </div>
    </div>
  `).join('');
}

async function dismissError(id) {
  try {
    await api(`/api/docker-monitor/errors/${id}/dismiss`, { method: 'POST' });
    toast('対応済みにしました', 'success');
    await loadErrors();
  } catch (err) {
    toast('対応済み化に失敗: ' + err.message, 'error');
  }
}

async function deleteError(id) {
  if (!confirm('このエラーレコードを削除しますか？')) return;
  try {
    await api(`/api/docker-monitor/errors/${id}`, { method: 'DELETE' });
    toast('削除しました', 'success');
    await loadErrors();
  } catch (err) {
    toast('削除に失敗: ' + err.message, 'error');
  }
}

async function dismissAllErrors() {
  if (!confirm('表示中の未対応エラーをすべて対応済みにしますか？')) return;
  try {
    await api('/api/docker-monitor/errors/dismiss-all', { method: 'POST' });
    toast('すべて対応済みにしました', 'success');
    await loadErrors();
  } catch (err) {
    toast('一括対応に失敗: ' + err.message, 'error');
  }
}

// ============================================================
// Exclusions loading
// ============================================================
async function loadExclusions() {
  try {
    const data = await api('/api/docker-monitor/exclusions');
    exclusions = data?.items || [];
    renderExclusions();
  } catch (err) {
    toast('除外パターンの取得に失敗: ' + err.message, 'error');
  }
}

function renderExclusions() {
  const list = $('dm-exclusions-list');
  if (!list) return;

  if (!exclusions.length) {
    list.innerHTML = `<div class="dm-empty">除外パターンは登録されていません。</div>`;
    return;
  }

  list.innerHTML = exclusions.map(e => `
    <div class="card dm-exc-card" data-exc-id="${e.id}">
      <div class="dm-exc-body">
        <div class="dm-exc-pattern">${esc(e.pattern)}</div>
        ${e.reason ? `<div class="dm-exc-reason">${esc(e.reason)}</div>` : ''}
      </div>
      <button class="btn btn-sm btn-danger" data-action="delete-exc">削除</button>
    </div>
  `).join('');
}

async function addExclusion() {
  const patternEl = $('dm-exc-pattern');
  const reasonEl = $('dm-exc-reason');
  const pattern = patternEl?.value?.trim() || '';
  const reason = reasonEl?.value?.trim() || '';
  if (!pattern) {
    toast('除外パターンを入力してください', 'error');
    return;
  }
  try {
    await api('/api/docker-monitor/exclusions', {
      method: 'POST',
      body: { pattern, reason },
    });
    toast('除外パターンを追加しました', 'success');
    if (patternEl) patternEl.value = '';
    if (reasonEl) reasonEl.value = '';
    await loadExclusions();
  } catch (err) {
    toast('追加に失敗: ' + err.message, 'error');
  }
}

async function deleteExclusion(id) {
  if (!confirm('この除外パターンを削除しますか？')) return;
  try {
    await api(`/api/docker-monitor/exclusions/${id}`, { method: 'DELETE' });
    toast('削除しました', 'success');
    await loadExclusions();
  } catch (err) {
    toast('削除に失敗: ' + err.message, 'error');
  }
}

// ============================================================
// Settings
// ============================================================
async function loadSettings() {
  try {
    const data = await api('/api/docker-monitor/settings');
    notifyDiscord = !!data?.notify_discord;
    renderSettings();
  } catch (err) {
    toast('設定の取得に失敗: ' + err.message, 'error');
  }
}

function renderSettings() {
  const toggle = $('dm-notify-toggle');
  if (toggle) toggle.classList.toggle('on', notifyDiscord);
}

async function toggleNotify() {
  const next = !notifyDiscord;
  try {
    await api('/api/docker-monitor/settings', {
      method: 'PUT',
      body: { notify_discord: next },
    });
    notifyDiscord = next;
    renderSettings();
    toast(`Discord 通知を ${next ? 'ON' : 'OFF'} にしました`, 'success');
  } catch (err) {
    toast('設定変更に失敗: ' + err.message, 'error');
  }
}

// ============================================================
// Tab switching
// ============================================================
function switchTab(tab) {
  if (tab === activeTab) return;
  activeTab = tab;

  document.querySelectorAll('.dm-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  document.querySelectorAll('.dm-panel').forEach(el => {
    el.classList.toggle('active', el.id === `dm-panel-${tab}`);
  });

  if (tab === 'errors') loadErrors();
  else if (tab === 'exclusions') loadExclusions();
  else if (tab === 'settings') loadSettings();
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  // Tab switching
  document.querySelectorAll('.dm-tab').forEach(el => {
    el.addEventListener('click', () => switchTab(el.dataset.tab));
  });

  // Errors toolbar
  $('dm-show-dismissed')?.addEventListener('change', e => {
    showDismissed = e.target.checked;
    loadErrors();
  });
  $('dm-refresh-errors')?.addEventListener('click', () => loadErrors());
  $('dm-dismiss-all')?.addEventListener('click', () => dismissAllErrors());

  // Errors list delegation
  $('dm-errors-list')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const card = btn.closest('.dm-card');
    if (!card) return;
    const id = card.dataset.errorId;
    if (btn.dataset.action === 'dismiss') dismissError(id);
    else if (btn.dataset.action === 'delete') deleteError(id);
  });

  // Exclusions
  $('dm-exc-add')?.addEventListener('click', () => addExclusion());
  $('dm-exc-pattern')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addExclusion();
  });
  $('dm-exc-reason')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addExclusion();
  });
  $('dm-exclusions-list')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-action="delete-exc"]');
    if (!btn) return;
    const card = btn.closest('.dm-exc-card');
    if (!card) return;
    deleteExclusion(card.dataset.excId);
  });

  // Settings toggle
  $('dm-notify-toggle')?.addEventListener('click', () => toggleNotify());

  // Initial load for active tab
  await loadErrors();
}

export function unmount() {
  activeTab = 'errors';
  errors = [];
  errorsTotal = 0;
  errorsLoading = false;
  showDismissed = false;
  exclusions = [];
  notifyDiscord = false;
}
