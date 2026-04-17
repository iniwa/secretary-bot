/** Prompts page — prompt_crafter セッションの閲覧・編集。 */
import { api } from '../api.js';
import { toast } from '../app.js';
import { esc, fmtTime, stashSet } from '../lib/common.js';

let activeSession = null;
let sessions = [];

function $(id) { return document.getElementById(id); }

export function render() {
  return `
<div class="pc-grid">
  <div class="pc-card">
    <h3>✏️ プロンプト編集</h3>
    <div class="pc-label">指示（自然言語。既存セッションがあれば差分編集）</div>
    <textarea id="pc-instruction" class="pc-input" placeholder="例: 森の中の少女、夕暮れ、後ろ姿"></textarea>
    <div class="pc-btn-row">
      <button id="pc-submit" class="btn btn-primary">送信</button>
      <button id="pc-clear-active" class="btn btn-danger">アクティブ破棄</button>
      <button id="pc-reload" class="btn">再読み込み</button>
    </div>
    <div id="pc-note" class="pc-note"></div>
  </div>

  <div class="pc-card">
    <h3>🌟 アクティブセッション</h3>
    <div id="pc-active-body"></div>
  </div>
</div>

<div class="pc-card" style="margin-top: 1rem;">
  <h3>📜 最近のセッション</h3>
  <div id="pc-list-body"></div>
</div>
`;
}

function renderActive() {
  const el = $('pc-active-body');
  if (!el) return;
  if (!activeSession) {
    el.innerHTML = '<div class="pc-empty">アクティブなセッションはありません。</div>';
    return;
  }
  el.innerHTML = `
    <div class="pc-label">セッション #${esc(activeSession.session_id)}</div>
    <div class="pc-label">positive</div>
    <div class="pc-prompt">${esc(activeSession.positive || '(empty)')}</div>
    <div class="pc-label">negative</div>
    <div class="pc-prompt">${esc(activeSession.negative || '(empty)')}</div>
    <div class="pc-btn-row">
      <button id="pc-to-imggen" class="btn btn-primary">🎨 画像生成へ</button>
    </div>
  `;
  $('pc-to-imggen')?.addEventListener('click', () => handleToImageGen(activeSession));
}

function handleToImageGen(s) {
  if (!s) return;
  stashSet({
    source: 'prompt_crafter',
    session_id: s.session_id ?? s.id,
    positive: s.positive || '',
    negative: s.negative || '',
    params: {},
  });
  location.hash = '#/generate?prefill=prompt';
  toast('Generate に取り込みました', 'info');
}

function renderList() {
  const el = $('pc-list-body');
  if (!el) return;
  if (!sessions || sessions.length === 0) {
    el.innerHTML = '<div class="pc-empty">セッションがありません。</div>';
    return;
  }
  el.innerHTML = sessions.map((s) => `
    <div class="pc-session-row">
      <div class="pc-session-text">
        <div class="pc-session-positive">${esc(s.positive || '(empty)')}</div>
        <div class="pc-session-meta">#${esc(s.id)} · ${fmtTime(s.updated_at)} · expires ${fmtTime(s.expires_at)}</div>
      </div>
      <button class="btn btn-danger btn-sm" data-del="${esc(s.id)}">削除</button>
    </div>
  `).join('');
  el.querySelectorAll('[data-del]').forEach((b) => {
    b.addEventListener('click', async () => {
      const id = b.dataset.del;
      if (!confirm(`セッション #${id} を削除しますか？`)) return;
      try {
        await api(`/api/image/prompts/${id}`, { method: 'DELETE' });
        toast('削除しました', 'success');
        await loadAll();
      } catch (err) {
        toast(`削除失敗: ${err.message || err}`, 'error');
      }
    });
  });
}

async function loadActive() {
  try {
    const res = await api('/api/image/prompts/active');
    activeSession = res?.session || null;
  } catch (err) {
    console.error('active load failed', err);
    activeSession = null;
  }
  renderActive();
}

async function loadList() {
  try {
    const res = await api('/api/image/prompts', { params: { limit: 20 } });
    sessions = res?.sessions || [];
  } catch (err) {
    console.error('list load failed', err);
    sessions = [];
  }
  renderList();
}

async function loadAll() {
  await Promise.all([loadActive(), loadList()]);
}

async function handleSubmit() {
  const btn = $('pc-submit');
  const instruction = $('pc-instruction')?.value?.trim();
  if (!instruction) {
    toast('指示を入力してください', 'error');
    return;
  }
  btn.disabled = true;
  $('pc-note').textContent = '生成中...';
  try {
    const res = await api('/api/image/prompts/craft', {
      method: 'POST',
      body: { instruction },
    });
    const note = res?.note ? `メモ: ${res.note}` : `セッション #${res?.session_id} を更新しました。`;
    $('pc-note').textContent = note;
    $('pc-instruction').value = '';
    toast('プロンプトを更新', 'success');
    await loadAll();
  } catch (err) {
    console.error('craft failed', err);
    $('pc-note').textContent = `エラー: ${err.message || err}`;
    toast('生成失敗', 'error');
  } finally {
    btn.disabled = false;
  }
}

async function handleClearActive() {
  if (!confirm('アクティブセッションを破棄しますか？')) return;
  try {
    await api('/api/image/prompts/active', { method: 'DELETE' });
    toast('破棄しました', 'success');
    await loadAll();
  } catch (err) {
    toast(`破棄失敗: ${err.message || err}`, 'error');
  }
}

export async function mount() {
  $('pc-submit')?.addEventListener('click', handleSubmit);
  $('pc-clear-active')?.addEventListener('click', handleClearActive);
  $('pc-reload')?.addEventListener('click', loadAll);
  await loadAll();
}

export function unmount() {
  activeSession = null;
  sessions = [];
}
