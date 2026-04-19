/** Wildcards page — Dynamic Prompts の辞書ファイル管理。
 *
 *  一覧・編集・削除に加え、サーバ側 /expand でのプレビューを提供。
 *  更新時は client 側キャッシュを無効化して generate ページと整合を保つ。
 */
import { api } from '../api.js';
import { toast } from '../lib/toast.js';
import { esc, fmtTime } from '../lib/common.js';
import { invalidateWildcardCache } from '../lib/wildcard.js';

let files = [];      // [{name, description, updated_at, size}]
let activeName = null;   // 現在編集中のファイル名（新規は null）

function $(id) { return document.getElementById(id); }

export function render() {
  return `
<div class="pc-grid">
  <div class="pc-card">
    <h3>📚 Wildcard 辞書</h3>
    <div class="pc-btn-row">
      <button id="wc-new" class="btn btn-primary">＋ 新規</button>
      <button id="wc-reload" class="btn">再読み込み</button>
    </div>
    <div id="wc-list-body" style="margin-top:0.6rem;"></div>
  </div>

  <div class="pc-card">
    <h3 id="wc-editor-title">📝 エディタ</h3>
    <div class="pc-label">ファイル名（英数・<code>_ . -</code>、最大64文字）</div>
    <input id="wc-name" class="pc-input" type="text" placeholder="例: hair_colors">
    <div class="pc-label">説明（任意）</div>
    <input id="wc-desc" class="pc-input" type="text" placeholder="例: 髪色の候補（blonde, red, ...）">
    <div class="pc-label">内容（1 行 = 1 候補、<code>#</code> 行はコメント、空行は無視）</div>
    <textarea id="wc-content" class="pc-input" style="min-height:200px;font-family:monospace;"
              placeholder="# コメント行\nred\nblue\ngreen"></textarea>
    <div class="pc-btn-row">
      <button id="wc-save" class="btn btn-primary">保存</button>
      <button id="wc-delete" class="btn btn-danger">削除</button>
      <button id="wc-clear" class="btn">新規に戻す</button>
    </div>
    <div id="wc-note" class="pc-note"></div>
  </div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>🔍 プレビュー</h3>
  <div class="pc-label">テンプレート（<code>{a|b}</code> / <code>{2::a|1::b}</code> / <code>{1-5}</code> / <code>__name__</code>）</div>
  <textarea id="wc-preview-tpl" class="pc-input" style="min-height:80px;font-family:monospace;"
            placeholder="例: 1girl, __hair_colors__ hair, {short|long} {1-5} braids"></textarea>
  <div class="pc-btn-row">
    <input id="wc-preview-seed" class="pc-input" type="number"
           placeholder="seed（空欄=ランダム）" style="width:200px;">
    <button id="wc-preview-run" class="btn btn-primary">展開</button>
  </div>
  <div class="pc-label" style="margin-top:0.6rem;">結果</div>
  <div id="wc-preview-result" class="pc-prompt"></div>
  <div id="wc-preview-detail" style="margin-top:0.4rem;font-size:0.8rem;color:var(--text-muted);"></div>
</div>
`;
}

// ============================================================
// List
// ============================================================
function renderList() {
  const el = $('wc-list-body');
  if (!el) return;
  if (!files.length) {
    el.innerHTML = '<div class="pc-empty">辞書ファイルがありません。「＋ 新規」から作成してください。</div>';
    return;
  }
  el.innerHTML = files.map((f) => {
    const sel = f.name === activeName ? ' style="background:var(--bg-hover,#2a2a2a);"' : '';
    const desc = f.description ? esc(f.description) : '<span class="text-muted">（説明なし）</span>';
    return `
      <div class="pc-session-row" data-name="${esc(f.name)}"${sel}>
        <div class="pc-session-text" style="cursor:pointer;" data-act="edit">
          <div class="pc-session-positive"><code>__${esc(f.name)}__</code> · ${desc}</div>
          <div class="pc-session-meta">${f.size ?? 0} bytes · ${fmtTime(f.updated_at)}</div>
        </div>
      </div>`;
  }).join('');
  el.querySelectorAll('[data-name]').forEach((row) => {
    row.addEventListener('click', () => {
      const name = row.dataset.name;
      loadFile(name).catch((err) => toast(`読み込み失敗: ${err.message || err}`, 'error'));
    });
  });
}

async function loadList() {
  try {
    const res = await api('/api/generation/wildcards');
    files = res?.files || [];
  } catch (err) {
    console.error('wildcard list failed', err);
    files = [];
    toast(`一覧取得失敗: ${err.message || err}`, 'error');
  }
  renderList();
}

// ============================================================
// Editor
// ============================================================
function setEditor({ name = '', description = '', content = '', active = null } = {}) {
  $('wc-name').value = name;
  $('wc-desc').value = description || '';
  $('wc-content').value = content || '';
  activeName = active;
  $('wc-editor-title').textContent = active ? `📝 編集: ${active}` : '📝 新規作成';
  $('wc-name').disabled = !!active;   // 既存は改名不可（DELETE → PUT を案内）
  $('wc-note').textContent = '';
  renderList();
}

async function loadFile(name) {
  try {
    const res = await api(`/api/generation/wildcards/${encodeURIComponent(name)}`);
    setEditor({
      name: res?.name || name,
      description: res?.description || '',
      content: res?.content || '',
      active: res?.name || name,
    });
  } catch (err) {
    console.error('wildcard get failed', err);
    throw err;
  }
}

async function handleSave() {
  const name = $('wc-name').value.trim();
  const description = $('wc-desc').value.trim();
  const content = $('wc-content').value;
  if (!name) { toast('ファイル名が必要です', 'error'); return; }
  if (!/^[A-Za-z0-9_.\-]{1,64}$/.test(name)) {
    toast('ファイル名は英数と _ . - のみ、64 文字以内', 'error');
    return;
  }
  $('wc-save').disabled = true;
  try {
    await api(`/api/generation/wildcards/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: { content, description: description || null },
    });
    invalidateWildcardCache();
    $('wc-note').textContent = `保存しました: ${name}`;
    toast('保存', 'success');
    await loadList();
    await loadFile(name);
  } catch (err) {
    console.error('wildcard save failed', err);
    $('wc-note').textContent = `エラー: ${err.message || err}`;
    toast('保存失敗', 'error');
  } finally {
    $('wc-save').disabled = false;
  }
}

async function handleDelete() {
  if (!activeName) { toast('新規作成中は削除できません', 'info'); return; }
  if (!confirm(`${activeName} を削除しますか？`)) return;
  try {
    await api(`/api/generation/wildcards/${encodeURIComponent(activeName)}`, { method: 'DELETE' });
    invalidateWildcardCache();
    toast('削除', 'success');
    setEditor();
    await loadList();
  } catch (err) {
    console.error('wildcard delete failed', err);
    toast(`削除失敗: ${err.message || err}`, 'error');
  }
}

// ============================================================
// Preview（サーバ側 /expand を利用して JS/Python の食い違いも検出）
// ============================================================
async function handlePreview() {
  const template = $('wc-preview-tpl').value;
  if (!template.trim()) { toast('テンプレートを入力', 'error'); return; }
  const seedStr = $('wc-preview-seed').value.trim();
  const body = { template };
  if (seedStr) {
    const n = Number(seedStr);
    if (Number.isFinite(n)) body.rng_seed = n;
  }
  const btn = $('wc-preview-run');
  btn.disabled = true;
  try {
    const res = await api('/api/generation/wildcards/expand', { method: 'POST', body });
    $('wc-preview-result').textContent = res?.text ?? '';
    const parts = [];
    const choices = res?.choices || [];
    const warns = res?.warnings || [];
    if (choices.length) {
      parts.push(`picks: ${choices.map(c => `<code>${esc(c.token)} → ${esc(c.picked)}</code>`).join(' ')}`);
    }
    if (warns.length) {
      parts.push(`<span style="color:var(--accent-danger);">警告: ${warns.map(esc).join(' / ')}</span>`);
    }
    $('wc-preview-detail').innerHTML = parts.join('<br>');
  } catch (err) {
    console.error('wildcard expand failed', err);
    $('wc-preview-result').textContent = '';
    $('wc-preview-detail').innerHTML = `<span style="color:var(--accent-danger);">エラー: ${esc(err.message || String(err))}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  $('wc-new')?.addEventListener('click', () => setEditor());
  $('wc-reload')?.addEventListener('click', loadList);
  $('wc-save')?.addEventListener('click', handleSave);
  $('wc-delete')?.addEventListener('click', handleDelete);
  $('wc-clear')?.addEventListener('click', () => setEditor());
  $('wc-preview-run')?.addEventListener('click', handlePreview);
  await loadList();
}
