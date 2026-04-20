/** LoRA page — LoRA 学習プロジェクト管理。
 *
 *  プロジェクト名は kohya 学習時のトリガーワードを兼ねるため、英小文字＋
 *  数字＋ `_` で 2〜32 文字に制限される（サーバ側 nas_io.validate_project_name
 *  がソース。下の正規表現は UX 用の事前チェック）。
 */
import { api } from '../api.js';
import { toast } from '../lib/toast.js';
import { esc, fmtTime } from '../lib/common.js';

let projects = [];
let activeId = null;
let datasetItems = [];

function $(id) { return document.getElementById(id); }

export function render() {
  return `
<div class="pc-grid">
  <div class="pc-card">
    <h3>🎯 LoRA プロジェクト</h3>
    <div class="pc-btn-row">
      <button id="lora-new" class="btn btn-primary">＋ 新規</button>
      <button id="lora-reload" class="btn">再読み込み</button>
    </div>
    <div id="lora-list-body" style="margin-top:0.6rem;"></div>
  </div>

  <div class="pc-card">
    <h3 id="lora-editor-title">📝 詳細</h3>
    <div class="pc-label">プロジェクト名（= トリガーワード, 英小文字・数字・<code>_</code>、2〜32 文字）</div>
    <input id="lora-name" class="pc-input" type="text" placeholder="例: my_character_v1">
    <div class="pc-label">説明（任意）</div>
    <input id="lora-desc" class="pc-input" type="text" placeholder="例: オリキャラ A の v1（線画寄り）">
    <div class="pc-label">ベースモデル（任意・空欄でデフォルト）</div>
    <input id="lora-base" class="pc-input" type="text" placeholder="例: ChenkinNoob-XL-V0.5.safetensors">
    <div class="pc-label">ステータス</div>
    <select id="lora-status" class="pc-input" disabled>
      <option value="draft">draft</option>
      <option value="ready">ready</option>
      <option value="training">training</option>
      <option value="done">done</option>
      <option value="failed">failed</option>
    </select>
    <div class="pc-btn-row">
      <button id="lora-save" class="btn btn-primary">保存</button>
      <button id="lora-delete" class="btn btn-danger">削除</button>
      <button id="lora-clear" class="btn">新規に戻す</button>
    </div>
    <div id="lora-note" class="pc-note"></div>
    <div id="lora-paths" style="margin-top:0.6rem;font-size:0.78rem;color:var(--text-muted);"></div>
  </div>
</div>

<div class="pc-card" id="lora-dataset-card" style="margin-top:1rem;display:none;">
  <h3>📦 データセット <span id="lora-ds-count" class="badge"></span></h3>
  <div id="lora-ds-drop" class="lora-ds-drop">
    画像をここにドラッグ＆ドロップ（png / jpg / jpeg / webp、1 枚 16 MiB まで）<br>
    <small>または <a href="#" id="lora-ds-browse">ファイル選択</a></small>
    <input id="lora-ds-input" type="file" accept="image/png,image/jpeg,image/webp" multiple style="display:none;">
  </div>
  <div id="lora-ds-progress" style="margin-top:0.4rem;font-size:0.8rem;color:var(--text-muted);"></div>
  <div id="lora-ds-grid" class="lora-ds-grid" style="margin-top:0.6rem;"></div>
</div>
`;
}

// ============================================================
// List
// ============================================================
function renderList() {
  const el = $('lora-list-body');
  if (!el) return;
  if (!projects.length) {
    el.innerHTML = '<div class="pc-empty">プロジェクトがありません。「＋ 新規」から作成してください。</div>';
    return;
  }
  el.innerHTML = projects.map((p) => {
    const sel = p.id === activeId ? ' style="background:var(--bg-hover,#2a2a2a);"' : '';
    const desc = p.description ? esc(p.description) : '<span class="text-muted">（説明なし）</span>';
    const status = esc(p.status || 'draft');
    return `
      <div class="pc-session-row" data-id="${p.id}"${sel}>
        <div class="pc-session-text" style="cursor:pointer;">
          <div class="pc-session-positive"><code>${esc(p.name)}</code> · <span class="badge">${status}</span> · ${desc}</div>
          <div class="pc-session-meta">id=${p.id} · ${fmtTime(p.updated_at || p.created_at)}</div>
        </div>
      </div>`;
  }).join('');
  el.querySelectorAll('[data-id]').forEach((row) => {
    row.addEventListener('click', () => {
      const id = Number(row.dataset.id);
      const p = projects.find(x => x.id === id);
      if (p) selectProject(p);
    });
  });
}

async function loadList() {
  try {
    const res = await api('/api/lora/projects');
    projects = res?.items || [];
  } catch (err) {
    console.error('lora list failed', err);
    projects = [];
    toast(`一覧取得失敗: ${err.message || err}`, 'error');
  }
  renderList();
}

// ============================================================
// Editor
// ============================================================
function setEditor({ project = null } = {}) {
  if (project) {
    activeId = project.id;
    $('lora-name').value = project.name || '';
    $('lora-desc').value = project.description || '';
    $('lora-base').value = project.base_model || '';
    $('lora-status').value = project.status || 'draft';
    $('lora-name').disabled = true;
    $('lora-status').disabled = false;
    $('lora-editor-title').textContent = `📝 編集: ${project.name}`;
    const paths = [];
    if (project.dataset_path) paths.push(`<div>dataset: <code>${esc(project.dataset_path)}</code></div>`);
    if (project.output_path) paths.push(`<div>output: <code>${esc(project.output_path)}</code></div>`);
    $('lora-paths').innerHTML = paths.join('');
    $('lora-dataset-card').style.display = '';
    loadDataset().catch((err) => toast(`dataset 取得失敗: ${err.message || err}`, 'error'));
  } else {
    activeId = null;
    $('lora-name').value = '';
    $('lora-desc').value = '';
    $('lora-base').value = '';
    $('lora-status').value = 'draft';
    $('lora-name').disabled = false;
    $('lora-status').disabled = true;
    $('lora-editor-title').textContent = '📝 新規作成';
    $('lora-paths').innerHTML = '';
    $('lora-dataset-card').style.display = 'none';
    datasetItems = [];
  }
  $('lora-note').textContent = '';
  renderList();
}

function selectProject(project) {
  setEditor({ project });
}

async function handleSave() {
  const name = $('lora-name').value.trim();
  const description = $('lora-desc').value.trim();
  const baseModel = $('lora-base').value.trim();
  const status = $('lora-status').value;
  $('lora-save').disabled = true;
  try {
    if (activeId) {
      const updated = await api(`/api/lora/projects/${activeId}`, {
        method: 'PATCH',
        body: {
          description: description || null,
          base_model: baseModel || null,
          status: status || null,
        },
      });
      $('lora-note').textContent = `更新しました: ${updated.name}`;
      toast('更新', 'success');
      await loadList();
      const refreshed = projects.find(p => p.id === activeId);
      if (refreshed) setEditor({ project: refreshed });
    } else {
      if (!name) { toast('プロジェクト名が必要です', 'error'); return; }
      if (!/^[a-z0-9][a-z0-9_]{1,31}$/.test(name)) {
        toast('英小文字・数字・_ のみ、2〜32 文字', 'error');
        return;
      }
      const created = await api('/api/lora/projects', {
        method: 'POST',
        body: {
          name,
          description: description || null,
          base_model: baseModel || null,
        },
      });
      $('lora-note').textContent = `作成しました: ${created.name}`;
      toast('作成', 'success');
      await loadList();
      const refreshed = projects.find(p => p.id === created.id);
      if (refreshed) setEditor({ project: refreshed });
    }
  } catch (err) {
    console.error('lora save failed', err);
    $('lora-note').textContent = `エラー: ${err.message || err}`;
    toast('保存失敗', 'error');
  } finally {
    $('lora-save').disabled = false;
  }
}

async function handleDelete() {
  if (!activeId) { toast('新規作成中は削除できません', 'info'); return; }
  const cur = projects.find(p => p.id === activeId);
  const label = cur ? cur.name : `id=${activeId}`;
  if (!confirm(`${label} を削除しますか？\nNAS の dataset/work ディレクトリも削除されます。`)) return;
  try {
    await api(`/api/lora/projects/${activeId}?purge_files=true`, { method: 'DELETE' });
    toast('削除', 'success');
    setEditor();
    await loadList();
  } catch (err) {
    console.error('lora delete failed', err);
    toast(`削除失敗: ${err.message || err}`, 'error');
  }
}

// ============================================================
// Dataset
// ============================================================
async function loadDataset() {
  if (!activeId) { datasetItems = []; renderDataset(); return; }
  const res = await api(`/api/lora/projects/${activeId}/dataset`);
  datasetItems = res?.items || [];
  renderDataset();
}

function renderDataset() {
  const grid = $('lora-ds-grid');
  const count = $('lora-ds-count');
  if (!grid || !count) return;
  count.textContent = `${datasetItems.length} 枚`;
  if (!datasetItems.length) {
    grid.innerHTML = '<div class="pc-empty">画像未投入</div>';
    return;
  }
  grid.innerHTML = datasetItems.map((it) => {
    const url = `/api/lora/projects/${activeId}/dataset/${it.id}/image`;
    const tags = it.tags ? esc(it.tags) : '<span class="text-muted">no tags</span>';
    return `
      <div class="lora-ds-cell" data-id="${it.id}">
        <img src="${url}" loading="lazy" alt="">
        <div class="lora-ds-meta">${tags}</div>
        <button class="lora-ds-del" data-act="del" title="削除">×</button>
      </div>`;
  }).join('');
  grid.querySelectorAll('[data-id]').forEach((cell) => {
    cell.querySelector('[data-act="del"]')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const id = Number(cell.dataset.id);
      handleDatasetDelete(id);
    });
  });
}

async function handleDatasetDelete(itemId) {
  if (!confirm('この画像を削除しますか？')) return;
  try {
    await api(`/api/lora/projects/${activeId}/dataset/${itemId}`, { method: 'DELETE' });
    await loadDataset();
    toast('削除', 'success');
  } catch (err) {
    toast(`削除失敗: ${err.message || err}`, 'error');
  }
}

async function handleDatasetUpload(fileList) {
  if (!activeId) { toast('プロジェクトを選択してください', 'error'); return; }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const allowed = /\.(png|jpe?g|webp)$/i;
  const accepted = files.filter(f => allowed.test(f.name));
  const rejected = files.length - accepted.length;
  if (!accepted.length) { toast('対応形式の画像がありません', 'error'); return; }
  const fd = new FormData();
  accepted.forEach((f) => fd.append('files', f, f.name));
  const prog = $('lora-ds-progress');
  prog.textContent = `アップロード中… (${accepted.length} 枚${rejected ? `, ${rejected} 枚スキップ` : ''})`;
  try {
    const res = await fetch(`/api/lora/projects/${activeId}/dataset`, {
      method: 'POST', body: fd,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`${res.status}: ${text.slice(0, 200)}`);
    }
    prog.textContent = '';
    await loadDataset();
    toast(`${accepted.length} 枚アップロード`, 'success');
  } catch (err) {
    prog.textContent = `エラー: ${err.message || err}`;
    toast('アップロード失敗', 'error');
  }
}

function bindDropZone() {
  const drop = $('lora-ds-drop');
  const input = $('lora-ds-input');
  if (!drop || !input) return;
  $('lora-ds-browse')?.addEventListener('click', (ev) => {
    ev.preventDefault();
    input.click();
  });
  input.addEventListener('change', () => {
    handleDatasetUpload(input.files);
    input.value = '';
  });
  ['dragenter', 'dragover'].forEach((ev) => {
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add('drag-over');
    });
  });
  ['dragleave', 'drop'].forEach((ev) => {
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove('drag-over');
    });
  });
  drop.addEventListener('drop', (e) => {
    handleDatasetUpload(e.dataTransfer?.files);
  });
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  $('lora-new')?.addEventListener('click', () => setEditor());
  $('lora-reload')?.addEventListener('click', loadList);
  $('lora-save')?.addEventListener('click', handleSave);
  $('lora-delete')?.addEventListener('click', handleDelete);
  $('lora-clear')?.addEventListener('click', () => setEditor());
  bindDropZone();
  await loadList();
}
