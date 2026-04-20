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
let tagPollTimer = null;
let activeTagTaskId = null;
let editingItemId = null;
let reviewedOnly = false;
let syncPollTimer = null;
let activeSyncTaskId = null;
let trainES = null;           // EventSource for SSE
let activeTrainTaskId = null;
let trainLastSeq = 0;
let trainLogLines = [];       // ring buffer rendered in UI

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
  <h3>📦 データセット <span id="lora-ds-count" class="badge"></span>
    <label style="margin-left:1rem;font-size:0.78rem;color:var(--text-muted);font-weight:normal;">
      <input id="lora-ds-reviewed-only" type="checkbox"> review 済みのみ
    </label>
  </h3>
  <div id="lora-ds-drop" class="lora-ds-drop">
    画像をここにドラッグ＆ドロップ（png / jpg / jpeg / webp、1 枚 16 MiB まで）<br>
    <small>または <a href="#" id="lora-ds-browse">ファイル選択</a></small>
    <input id="lora-ds-input" type="file" accept="image/png,image/jpeg,image/webp" multiple style="display:none;">
  </div>
  <div id="lora-ds-progress" style="margin-top:0.4rem;font-size:0.8rem;color:var(--text-muted);"></div>

  <div style="margin-top:0.6rem;display:flex;gap:0.4rem;align-items:center;flex-wrap:wrap;">
    <button id="lora-tag-btn" class="btn">🏷 WD14 タグ付け</button>
    <label style="font-size:0.8rem;color:var(--text-muted);">
      threshold
      <input id="lora-tag-threshold" type="number" step="0.05" min="0.05" max="0.95" value="0.35"
        style="width:4.5rem;margin-left:0.3rem;">
    </label>
    <label style="font-size:0.8rem;color:var(--text-muted);">
      <input id="lora-tag-prepend" type="checkbox" checked> トリガーワード先頭挿入
    </label>
  </div>
  <div id="lora-tag-progress" style="margin-top:0.4rem;font-size:0.8rem;color:var(--text-muted);"></div>

  <div style="margin-top:0.6rem;display:flex;gap:0.4rem;align-items:center;flex-wrap:wrap;border-top:1px solid var(--border,#333);padding-top:0.6rem;">
    <button id="lora-prepare-btn" class="btn">📝 準備 (TOML 生成)</button>
    <button id="lora-sync-btn" class="btn">🔁 Agent 同期</button>
    <small style="color:var(--text-muted);">prepare → sync の順で実行。sync 後に学習が可能になります。</small>
  </div>
  <div id="lora-prepare-progress" style="margin-top:0.4rem;font-size:0.8rem;color:var(--text-muted);"></div>
  <div id="lora-sync-progress" style="margin-top:0.2rem;font-size:0.8rem;color:var(--text-muted);"></div>

  <div id="lora-ds-grid" class="lora-ds-grid" style="margin-top:0.6rem;"></div>
</div>

<div class="pc-card" id="lora-train-card" style="margin-top:1rem;display:none;">
  <h3>🎓 学習モニタ <span id="lora-train-status" class="badge">idle</span></h3>
  <div class="pc-btn-row">
    <button id="lora-train-start" class="btn btn-primary">🚀 学習開始</button>
    <button id="lora-train-cancel" class="btn btn-danger" disabled>■ キャンセル</button>
    <small style="color:var(--text-muted);">prepare + sync 後に有効。完了で samples にチェックポイントが保存されます。</small>
  </div>
  <div id="lora-train-progress-bar" style="margin-top:0.6rem;height:6px;background:var(--bg-muted,#222);border-radius:3px;overflow:hidden;">
    <div id="lora-train-progress-fill" style="height:100%;width:0%;background:var(--accent,#4a9eff);transition:width 0.3s;"></div>
  </div>
  <div id="lora-train-meta" style="margin-top:0.4rem;font-size:0.8rem;color:var(--text-muted);">未実行</div>
  <div style="margin-top:0.6rem;font-size:0.78rem;color:var(--text-muted);">stdout tail:</div>
  <pre id="lora-train-log" style="margin:0.2rem 0 0;padding:0.4rem;background:var(--bg-muted,#1a1a1a);border:1px solid var(--border,#333);border-radius:4px;max-height:16rem;overflow:auto;font-size:0.72rem;line-height:1.35;white-space:pre-wrap;word-break:break-all;"></pre>

  <div style="margin-top:0.8rem;display:flex;justify-content:space-between;align-items:center;">
    <h4 style="margin:0;font-size:0.9rem;">📦 チェックポイント</h4>
    <button id="lora-ckpt-reload" class="btn btn-small">再読込</button>
  </div>
  <div id="lora-ckpt-list" style="margin-top:0.4rem;font-size:0.8rem;"></div>
</div>

<div id="lora-edit-modal" class="lora-modal" style="display:none;">
  <div class="lora-modal-inner">
    <div class="lora-modal-head">
      <span id="lora-edit-title">画像タグ編集</span>
      <button id="lora-edit-close" class="btn btn-small">×</button>
    </div>
    <div class="lora-modal-body">
      <img id="lora-edit-img" loading="lazy" alt="" style="max-width:100%;max-height:40vh;display:block;margin:0 auto;">
      <div class="pc-label" style="margin-top:0.6rem;">タグ（kohya caption に同期）</div>
      <textarea id="lora-edit-tags" class="pc-input" rows="4"
        placeholder="tag1, tag2, tag3"></textarea>
      <div class="pc-label">キャプション（指定時はこちらが .txt に書き込まれる）</div>
      <textarea id="lora-edit-caption" class="pc-input" rows="3"
        placeholder="（空欄ならタグをそのまま書き込み）"></textarea>
      <label style="font-size:0.85rem;display:flex;gap:0.3rem;align-items:center;margin-top:0.4rem;">
        <input id="lora-edit-reviewed" type="checkbox"> review 済みとしてマーク
      </label>
    </div>
    <div class="lora-modal-foot">
      <button id="lora-edit-save" class="btn btn-primary">保存</button>
      <button id="lora-edit-cancel" class="btn">キャンセル</button>
    </div>
  </div>
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
  stopTagPolling();
  stopSyncPolling();
  stopTrainStream();
  activeTagTaskId = null;
  activeSyncTaskId = null;
  activeTrainTaskId = null;
  trainLastSeq = 0;
  trainLogLines = [];
  resetTrainUI();
  const progEl = $('lora-tag-progress');
  if (progEl) progEl.textContent = '';
  const btnEl = $('lora-tag-btn');
  if (btnEl) btnEl.disabled = false;
  const prepProg = $('lora-prepare-progress');
  if (prepProg) prepProg.textContent = '';
  const syncProg = $('lora-sync-progress');
  if (syncProg) syncProg.textContent = '';
  const prepBtn = $('lora-prepare-btn');
  if (prepBtn) prepBtn.disabled = false;
  const syncBtn = $('lora-sync-btn');
  if (syncBtn) syncBtn.disabled = false;
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
    $('lora-train-card').style.display = '';
    loadDataset().catch((err) => toast(`dataset 取得失敗: ${err.message || err}`, 'error'));
    loadLatestTrainTask().catch(() => {});
    loadCheckpoints().catch(() => {});
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
    $('lora-train-card').style.display = 'none';
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
  const qs = reviewedOnly ? '?reviewed_only=true' : '';
  const res = await api(`/api/lora/projects/${activeId}/dataset${qs}`);
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
    const reviewed = it.reviewed_at ? ' lora-ds-reviewed' : '';
    return `
      <div class="lora-ds-cell${reviewed}" data-id="${it.id}" title="クリックで編集">
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
    cell.addEventListener('click', (ev) => {
      if (ev.target && ev.target.closest('[data-act="del"]')) return;
      const id = Number(cell.dataset.id);
      openEditModal(id);
    });
  });
}

function openEditModal(itemId) {
  const it = datasetItems.find(x => x.id === itemId);
  if (!it) return;
  editingItemId = itemId;
  $('lora-edit-title').textContent = `画像タグ編集 (id=${itemId})`;
  $('lora-edit-img').src = `/api/lora/projects/${activeId}/dataset/${itemId}/image`;
  $('lora-edit-tags').value = it.tags || '';
  $('lora-edit-caption').value = it.caption || '';
  $('lora-edit-reviewed').checked = !!it.reviewed_at;
  $('lora-edit-modal').style.display = '';
}

function closeEditModal() {
  editingItemId = null;
  $('lora-edit-modal').style.display = 'none';
}

async function handleEditSave() {
  if (!editingItemId) return;
  const tags = $('lora-edit-tags').value;
  const caption = $('lora-edit-caption').value.trim();
  const mark_reviewed = $('lora-edit-reviewed').checked;
  const btn = $('lora-edit-save');
  btn.disabled = true;
  try {
    await api(
      `/api/lora/projects/${activeId}/dataset/${editingItemId}`,
      {
        method: 'PATCH',
        body: {
          tags,
          caption: caption || null,
          mark_reviewed,
        },
      },
    );
    toast('保存', 'success');
    closeEditModal();
    await loadDataset();
  } catch (err) {
    toast(`保存失敗: ${err.message || err}`, 'error');
  } finally {
    btn.disabled = false;
  }
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
// WD14 tagging
// ============================================================
function stopTagPolling() {
  if (tagPollTimer) {
    clearInterval(tagPollTimer);
    tagPollTimer = null;
  }
}

async function handleTagStart() {
  if (!activeId) { toast('プロジェクトを選択してください', 'error'); return; }
  if (!datasetItems.length) { toast('画像が投入されていません', 'error'); return; }
  const thresholdRaw = parseFloat($('lora-tag-threshold')?.value || '0.35');
  const threshold = Number.isFinite(thresholdRaw) ? thresholdRaw : 0.35;
  const prepend_trigger = !!$('lora-tag-prepend')?.checked;
  const btn = $('lora-tag-btn');
  const prog = $('lora-tag-progress');
  btn.disabled = true;
  prog.textContent = 'タグ付けをキック中…';
  try {
    const entry = await api(`/api/lora/projects/${activeId}/dataset/tag`, {
      method: 'POST',
      body: { threshold, prepend_trigger },
    });
    activeTagTaskId = entry.task_id;
    prog.textContent = `実行中 (task_id=${entry.task_id}, agent=${entry.agent_id || '-'})`;
    toast('タグ付け開始', 'success');
    startTagPolling(entry.task_id);
  } catch (err) {
    console.error('tag start failed', err);
    prog.textContent = `エラー: ${err.message || err}`;
    toast('タグ付け失敗', 'error');
    btn.disabled = false;
  }
}

function startTagPolling(taskId) {
  stopTagPolling();
  const pid = activeId;
  tagPollTimer = setInterval(async () => {
    if (pid !== activeId) { stopTagPolling(); return; }
    try {
      const entry = await api(
        `/api/lora/projects/${pid}/dataset/tag/${encodeURIComponent(taskId)}`,
      );
      updateTagProgress(entry);
      if (entry.status === 'done' || entry.status === 'failed') {
        stopTagPolling();
        $('lora-tag-btn').disabled = false;
        if (entry.status === 'done') {
          toast(`タグ付け完了 (${entry.db_updated || 0} 件反映)`, 'success');
          await loadDataset();
        } else {
          toast(`タグ付け失敗: ${entry.error || 'unknown'}`, 'error');
        }
      }
    } catch (err) {
      console.warn('tag poll failed', err);
    }
  }, 3000);
}

function updateTagProgress(entry) {
  const prog = $('lora-tag-progress');
  if (!prog) return;
  const bits = [
    `status=${entry.status}`,
    `step=${entry.current_step || '-'}`,
  ];
  if (entry.db_updated) bits.push(`db_updated=${entry.db_updated}`);
  if (entry.error) bits.push(`err=${entry.error}`);
  prog.textContent = bits.join(' · ');
}

// ============================================================
// Prepare (TOML) + Sync
// ============================================================
async function handlePrepare() {
  if (!activeId) { toast('プロジェクトを選択してください', 'error'); return; }
  const btn = $('lora-prepare-btn');
  const prog = $('lora-prepare-progress');
  btn.disabled = true;
  prog.textContent = '生成中…';
  try {
    const produced = await api(`/api/lora/projects/${activeId}/prepare`, {
      method: 'POST',
      body: {},
    });
    const keys = ['dataset_toml', 'config_toml', 'sample_prompts'];
    const files = keys
      .map(k => produced?.[k])
      .filter(Boolean)
      .map(p => p.split(/[\\/]/).pop())
      .join(', ');
    prog.textContent = `生成: ${files || '(なし)'}`;
    toast('TOML 生成完了', 'success');
  } catch (err) {
    console.error('prepare failed', err);
    prog.textContent = `エラー: ${err.message || err}`;
    toast('TOML 生成失敗', 'error');
  } finally {
    btn.disabled = false;
  }
}

function stopSyncPolling() {
  if (syncPollTimer) {
    clearInterval(syncPollTimer);
    syncPollTimer = null;
  }
}

async function handleSyncStart() {
  if (!activeId) { toast('プロジェクトを選択してください', 'error'); return; }
  const btn = $('lora-sync-btn');
  const prog = $('lora-sync-progress');
  btn.disabled = true;
  prog.textContent = 'Agent 同期をキック中…';
  try {
    const entry = await api(`/api/lora/projects/${activeId}/sync`, {
      method: 'POST',
      body: {},
    });
    activeSyncTaskId = entry.task_id;
    prog.textContent = `実行中 (task_id=${entry.task_id}, agent=${entry.agent_id || '-'})`;
    toast('Agent 同期開始', 'success');
    startSyncPolling(entry.task_id);
  } catch (err) {
    console.error('sync start failed', err);
    prog.textContent = `エラー: ${err.message || err}`;
    toast('同期失敗', 'error');
    btn.disabled = false;
  }
}

function startSyncPolling(taskId) {
  stopSyncPolling();
  const pid = activeId;
  syncPollTimer = setInterval(async () => {
    if (pid !== activeId) { stopSyncPolling(); return; }
    try {
      const entry = await api(
        `/api/lora/projects/${pid}/sync/${encodeURIComponent(taskId)}`,
      );
      updateSyncProgress(entry);
      if (entry.status === 'done' || entry.status === 'failed') {
        stopSyncPolling();
        $('lora-sync-btn').disabled = false;
        if (entry.status === 'done') {
          toast('Agent 同期完了', 'success');
        } else {
          toast(`同期失敗: ${entry.error || 'unknown'}`, 'error');
        }
      }
    } catch (err) {
      console.warn('sync poll failed', err);
    }
  }, 3000);
}

function updateSyncProgress(entry) {
  const prog = $('lora-sync-progress');
  if (!prog) return;
  const bits = [
    `status=${entry.status}`,
    `step=${entry.current_step || '-'}`,
  ];
  if (entry.error) bits.push(`err=${entry.error}`);
  prog.textContent = bits.join(' · ');
}

// ============================================================
// Training monitor (Phase G)
// ============================================================
function resetTrainUI() {
  const statusEl = $('lora-train-status');
  if (statusEl) { statusEl.textContent = 'idle'; statusEl.className = 'badge'; }
  const fill = $('lora-train-progress-fill');
  if (fill) fill.style.width = '0%';
  const meta = $('lora-train-meta');
  if (meta) meta.textContent = '未実行';
  const logEl = $('lora-train-log');
  if (logEl) logEl.textContent = '';
  const startBtn = $('lora-train-start');
  if (startBtn) startBtn.disabled = false;
  const cancelBtn = $('lora-train-cancel');
  if (cancelBtn) cancelBtn.disabled = true;
}

async function loadLatestTrainTask() {
  if (!activeId) return;
  try {
    const res = await api(`/api/lora/projects/${activeId}/train`);
    const items = res?.items || [];
    if (!items.length) return;
    const latest = items[0];
    renderTrainSnapshot(latest);
    if (latest.status === 'running') {
      startTrainStream(latest.task_id);
    }
  } catch (err) {
    console.warn('train list failed', err);
  }
}

function renderTrainSnapshot(s) {
  const statusEl = $('lora-train-status');
  if (statusEl) {
    statusEl.textContent = s.status || 'idle';
    statusEl.className = 'badge' + (
      s.status === 'running' ? ' badge-info' :
      s.status === 'done' ? ' badge-success' :
      s.status === 'failed' ? ' badge-error' :
      s.status === 'cancelled' ? ' badge-warning' : ''
    );
  }
  const pct = s.total_steps ? Math.min(100, Math.floor(s.step * 100 / s.total_steps)) : (s.progress_pct || 0);
  const fill = $('lora-train-progress-fill');
  if (fill) fill.style.width = `${pct}%`;
  const meta = $('lora-train-meta');
  if (meta) {
    const bits = [`${pct}%`];
    if (s.step || s.total_steps) bits.push(`step ${s.step}/${s.total_steps}`);
    if (s.epoch || s.total_epochs) bits.push(`epoch ${s.epoch}/${s.total_epochs}`);
    if (s.last_loss != null) bits.push(`loss=${Number(s.last_loss).toFixed(4)}`);
    if (s.latest_sample) bits.push(`sample: ${s.latest_sample.split(/[\\/]/).pop()}`);
    if (s.latest_checkpoint) bits.push(`ckpt: ${s.latest_checkpoint.split(/[\\/]/).pop()}`);
    if (s.current_step) bits.push(s.current_step);
    if (s.error) bits.push(`err: ${s.error}`);
    meta.textContent = bits.join(' · ');
  }
  const startBtn = $('lora-train-start');
  const cancelBtn = $('lora-train-cancel');
  const running = s.status === 'running';
  if (startBtn) startBtn.disabled = running;
  if (cancelBtn) cancelBtn.disabled = !running;
}

function appendTrainLog(entry) {
  if (!entry || !entry.message) return;
  trainLogLines.push(entry.message);
  if (trainLogLines.length > 400) trainLogLines.splice(0, trainLogLines.length - 400);
  const el = $('lora-train-log');
  if (!el) return;
  const nearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 40;
  el.textContent = trainLogLines.join('\n');
  if (nearBottom) el.scrollTop = el.scrollHeight;
}

function stopTrainStream() {
  if (trainES) {
    try { trainES.close(); } catch { /* noop */ }
    trainES = null;
  }
}

function startTrainStream(taskId) {
  stopTrainStream();
  activeTrainTaskId = taskId;
  trainLastSeq = 0;
  const url = `/api/lora/projects/${activeId}/train/${encodeURIComponent(taskId)}/stream`;
  const es = new EventSource(url);
  trainES = es;
  es.addEventListener('status', (ev) => {
    try { renderTrainSnapshot(JSON.parse(ev.data)); } catch { /* noop */ }
  });
  es.addEventListener('log', (ev) => {
    try {
      const entry = JSON.parse(ev.data);
      if (entry.seq && entry.seq <= trainLastSeq) return;
      if (entry.seq) trainLastSeq = entry.seq;
      appendTrainLog(entry);
    } catch { /* noop */ }
  });
  es.addEventListener('error', (ev) => {
    try {
      const d = JSON.parse(ev.data || '{}');
      if (d.error) appendTrainLog({ message: `[stream error] ${d.error}` });
    } catch { /* noop */ }
  });
  es.addEventListener('end', () => {
    stopTrainStream();
    loadCheckpoints().catch(() => {});
  });
  es.onerror = () => {
    // EventSource は自動再接続するので明示ログのみ
    appendTrainLog({ message: '[stream disconnected, retrying…]' });
  };
}

async function handleTrainStart() {
  if (!activeId) { toast('プロジェクトを選択してください', 'error'); return; }
  const startBtn = $('lora-train-start');
  startBtn.disabled = true;
  trainLogLines = [];
  const logEl = $('lora-train-log');
  if (logEl) logEl.textContent = '';
  try {
    const entry = await api(`/api/lora/projects/${activeId}/train`, {
      method: 'POST', body: {},
    });
    activeTrainTaskId = entry.task_id;
    renderTrainSnapshot(entry);
    toast('学習開始', 'success');
    startTrainStream(entry.task_id);
  } catch (err) {
    console.error('train start failed', err);
    toast(`学習開始失敗: ${err.message || err}`, 'error');
    startBtn.disabled = false;
  }
}

// ============================================================
// Checkpoints / Promotion (Phase H)
// ============================================================
async function loadCheckpoints() {
  if (!activeId) return;
  const el = $('lora-ckpt-list');
  if (!el) return;
  el.textContent = '読み込み中…';
  try {
    const res = await api(`/api/lora/projects/${activeId}/checkpoints`);
    const items = res?.items || [];
    if (!items.length) {
      el.innerHTML = '<div class="pc-empty">未生成（学習完了後に samples が表示されます）</div>';
      return;
    }
    el.innerHTML = items.map((it) => {
      const mb = (it.size / (1024 * 1024)).toFixed(1);
      const ts = new Date(it.mtime * 1000).toLocaleString();
      return `
        <div class="pc-session-row" data-file="${esc(it.filename)}">
          <div class="pc-session-text">
            <div><code>${esc(it.filename)}</code></div>
            <div class="pc-session-meta">${mb} MiB · ${ts}</div>
          </div>
          <button class="btn btn-small" data-act="promote">⭐ 昇格</button>
        </div>`;
    }).join('');
    el.querySelectorAll('[data-file]').forEach((row) => {
      row.querySelector('[data-act="promote"]')?.addEventListener('click', () => {
        handlePromote(row.dataset.file);
      });
    });
  } catch (err) {
    el.textContent = `エラー: ${err.message || err}`;
  }
}

async function handlePromote(filename) {
  if (!activeId) return;
  const suggested = filename;
  const target = prompt(
    `${filename} を models/loras/<project>/ に昇格します。\n保存ファイル名（空欄で元名を維持）:`,
    suggested,
  );
  if (target === null) return;
  try {
    const res = await api(`/api/lora/projects/${activeId}/promote`, {
      method: 'POST',
      body: {
        checkpoint_filename: filename,
        target_filename: target || null,
      },
    });
    toast(`昇格: ${res.promoted_to}`, 'success');
    await loadList();
    const refreshed = projects.find(p => p.id === activeId);
    if (refreshed) {
      activeId = refreshed.id;
      $('lora-status').value = refreshed.status || 'draft';
      const paths = [];
      if (refreshed.dataset_path) paths.push(`<div>dataset: <code>${esc(refreshed.dataset_path)}</code></div>`);
      if (refreshed.output_path) paths.push(`<div>output: <code>${esc(refreshed.output_path)}</code></div>`);
      $('lora-paths').innerHTML = paths.join('');
    }
  } catch (err) {
    toast(`昇格失敗: ${err.message || err}`, 'error');
  }
}

async function handleTrainCancel() {
  if (!activeTrainTaskId) return;
  if (!confirm('学習をキャンセルしますか？')) return;
  const btn = $('lora-train-cancel');
  btn.disabled = true;
  try {
    await api(
      `/api/lora/projects/${activeId}/train/${encodeURIComponent(activeTrainTaskId)}/cancel`,
      { method: 'POST', body: {} },
    );
    toast('キャンセル要求送信', 'info');
  } catch (err) {
    toast(`キャンセル失敗: ${err.message || err}`, 'error');
    btn.disabled = false;
  }
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
  $('lora-tag-btn')?.addEventListener('click', handleTagStart);
  $('lora-prepare-btn')?.addEventListener('click', handlePrepare);
  $('lora-sync-btn')?.addEventListener('click', handleSyncStart);
  $('lora-train-start')?.addEventListener('click', handleTrainStart);
  $('lora-train-cancel')?.addEventListener('click', handleTrainCancel);
  $('lora-ckpt-reload')?.addEventListener('click', loadCheckpoints);
  $('lora-ds-reviewed-only')?.addEventListener('change', async (ev) => {
    reviewedOnly = !!ev.target.checked;
    await loadDataset();
  });
  $('lora-edit-close')?.addEventListener('click', closeEditModal);
  $('lora-edit-cancel')?.addEventListener('click', closeEditModal);
  $('lora-edit-save')?.addEventListener('click', handleEditSave);
  $('lora-edit-modal')?.addEventListener('click', (ev) => {
    if (ev.target?.id === 'lora-edit-modal') closeEditModal();
  });
  bindDropZone();
  await loadList();
}

export function unmount() {
  stopTagPolling();
  stopSyncPolling();
  stopTrainStream();
  activeTagTaskId = null;
  activeSyncTaskId = null;
  activeTrainTaskId = null;
}
