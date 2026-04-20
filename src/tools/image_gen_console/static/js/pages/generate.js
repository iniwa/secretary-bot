/** Generate page — 生成フォーム + セクション選択 + 合成プレビュー + プリセット管理。
 *  Jobs / Gallery は別ページ（#/jobs, #/gallery）。
 */
import { api } from '../api.js';
import { toast } from '../lib/toast.js';
import { GenerationAPI } from '../lib/generation_api.js';
import { composePromptClient } from '../lib/compose.js';
import { esc, makeSortable, stashGet, stashClear, bindModalBackdropClose } from '../lib/common.js';
import { expand as expandWildcards, hasWildcardToken, loadWildcardFiles } from '../lib/wildcard.js';

// ============================================================
// State
// ============================================================
let workflows = [];
let categories = [];
let sections = [];
let chosen = [];          // Array<number> section_id の配列（順序保持）
let comfyAgents = [];
let comfyStatusTimer = null;
const comfyBusy = new Set();

// Presets modal state
let presetModalState = { source: '', workflowJson: null, sourceLabel: '' };

// LoRA state（workflow ごとに [{node_id, enabled, strength, lora_name}, ...]）
let loraNodes = [];
const LORA_LS_PREFIX = 'imggen:lora:';   // + workflow_name → JSON

// セクションプリセット（DB 由来）
let sectionPresets = [];

// セクションカテゴリのアコーディオン展開状態（key → bool）
const SECTION_EXPANDED_LS = 'imggen:sections:expanded';
let sectionExpanded = {};

// 既知の sampler / scheduler（datalist 候補）
const KNOWN_SAMPLERS = [
  'euler', 'euler_ancestral', 'heun', 'heunpp2', 'dpm_2', 'dpm_2_ancestral',
  'lms', 'dpm_fast', 'dpm_adaptive',
  'dpmpp_2s_ancestral', 'dpmpp_sde', 'dpmpp_sde_gpu',
  'dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_2m_sde_gpu',
  'dpmpp_3m_sde', 'dpmpp_3m_sde_gpu',
  'ddpm', 'ddim', 'uni_pc', 'uni_pc_bh2', 'lcm',
];
const KNOWN_SCHEDULERS = [
  'normal', 'karras', 'exponential', 'sgm_uniform', 'simple',
  'ddim_uniform', 'beta',
];

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

// ============================================================
// Render (root)
// ============================================================
export function render() {
  return `
<div class="imggen-grid" style="display:grid;grid-template-columns:1fr;gap:1rem;">
  <!-- Generate -->
  <section class="card imggen-section">
    <div class="imggen-header">
      <h3>Generate</h3>
      <div style="display:flex;gap:0.4rem;">
        <a href="#/jobs" class="btn btn-sm">Jobs →</a>
        <a href="#/gallery" class="btn btn-sm">Gallery →</a>
      </div>
    </div>
    <div id="ig-comfy-panel" class="imggen-comfy-panel"></div>

    <div class="imggen-form">
      <label for="ig-workflow">Workflow</label>
      <select id="ig-workflow" class="form-input"><option value="">Loading...</option></select>

      <div id="ig-lora-block" class="imggen-lora-block" hidden>
        <h4 style="margin:0.5rem 0 0.3rem;font-size:0.8rem;">LoRA</h4>
        <div id="ig-lora-list"></div>
      </div>

      <div class="imggen-sections-block">
        <h4>
          <span>セクション（プロンプト断片）</span>
          <span>
            <button id="ig-sec-expand-all" class="imggen-toggle" title="全カテゴリを展開">⤓ 全展開</button>
            <button id="ig-sec-collapse-all" class="imggen-toggle" title="全カテゴリを収納">⤒ 全収納</button>
            <button id="ig-sec-reload" class="imggen-toggle" title="再読込">↻</button>
            <button id="ig-sec-new" class="imggen-toggle">+ 新規</button>
          </span>
        </h4>

        <div class="imggen-secpreset-row">
          <select id="ig-secpreset-select" class="form-input" style="flex:1;">
            <option value="">— セクションプリセット選択 —</option>
          </select>
          <button id="ig-secpreset-load" class="btn btn-sm" title="このプリセットを読み込み">読込</button>
          <button id="ig-secpreset-overwrite" class="btn btn-sm" title="現在の選択で上書き保存">上書</button>
          <button id="ig-secpreset-save" class="btn btn-sm btn-primary" title="新規プリセットとして保存">+ 保存</button>
          <button id="ig-secpreset-delete" class="btn btn-sm btn-danger" title="プリセット削除">削除</button>
        </div>

        <div id="ig-sec-cats">
          <div class="imggen-empty" style="padding:0.4rem;">Loading...</div>
        </div>
        <div>
          <div style="font-size:0.7rem;color:var(--text-muted);margin:0.3rem 0 0.15rem;">選択中（ドラッグで順序変更）</div>
          <div id="ig-sec-chosen" class="imggen-selected-chips"></div>
        </div>
      </div>

      <label for="ig-positive">Positive prompt（ユーザー追記）</label>
      <textarea id="ig-positive" placeholder="例: 1girl, beautiful lighting ..."></textarea>

      <label for="ig-negative">Negative prompt（ユーザー追記）</label>
      <textarea id="ig-negative" placeholder="例: blurry ..."></textarea>

      <div class="imggen-user-pos">
        <span>挿入位置</span>
        <select id="ig-userpos" class="form-input" style="width:auto;">
          <option value="tail">末尾</option>
          <option value="head">先頭</option>
        </select>
        <button id="ig-prompt-crafter" class="btn btn-sm" title="プロンプト履歴から選択">📝 履歴</button>
      </div>

      <div class="imggen-preview-row">
        <button id="ig-preview-show" class="btn btn-sm" type="button">🔍 プレビュー表示</button>
        <span class="text-muted text-xs">合成済みプロンプトをモーダルで確認</span>
      </div>

      <div class="imggen-params">
        <div>
          <label for="ig-width">Width</label>
          <input id="ig-width" class="form-input" type="number" min="64" step="8" placeholder="1024">
        </div>
        <div>
          <label for="ig-height">Height</label>
          <input id="ig-height" class="form-input" type="number" min="64" step="8" placeholder="1024">
        </div>
        <div>
          <label for="ig-steps">Steps</label>
          <input id="ig-steps" class="form-input" type="number" min="1" placeholder="30">
        </div>
        <div>
          <label for="ig-cfg">CFG</label>
          <input id="ig-cfg" class="form-input" type="number" step="0.1" placeholder="5.5">
        </div>
        <div>
          <label for="ig-seed">Seed (-1 random)</label>
          <input id="ig-seed" class="form-input" type="number" placeholder="-1">
        </div>
        <div>
          <label for="ig-sampler">Sampler</label>
          <input id="ig-sampler" class="form-input" type="text" list="ig-sampler-list" placeholder="euler_ancestral" autocomplete="off">
          <datalist id="ig-sampler-list">
            ${KNOWN_SAMPLERS.map(s => `<option value="${esc(s)}"></option>`).join('')}
          </datalist>
        </div>
        <div>
          <label for="ig-scheduler">Scheduler</label>
          <input id="ig-scheduler" class="form-input" type="text" list="ig-scheduler-list" placeholder="karras" autocomplete="off">
          <datalist id="ig-scheduler-list">
            ${KNOWN_SCHEDULERS.map(s => `<option value="${esc(s)}"></option>`).join('')}
          </datalist>
        </div>
      </div>

      <div class="imggen-wildcard-row" style="display:flex;gap:0.4rem;align-items:center;margin-top:0.3rem;font-size:0.75rem;flex-wrap:wrap;">
        <span title="Wildcard 展開方式（{a|b} / {1-3} / __name__）">🎲 Wildcard</span>
        <select id="ig-wildcard-mode" class="form-input" style="width:auto;" title="random=毎回ランダム / tied=画像 seed と連動 / fixed=固定展開を全バッチ使い回し">
          <option value="random">毎回ランダム</option>
          <option value="tied">Seed 連動</option>
          <option value="fixed">固定展開</option>
        </select>
        <input id="ig-wildcard-seed" class="form-input" type="number" placeholder="wc seed (空欄=乱数)" style="width:160px;" title="Wildcard seed。明示時のみ決定的。random モードでは +i ずつ加算">
        <a href="#/wildcards" class="btn btn-sm" style="margin-left:auto;" title="Wildcard ファイル編集">📝 辞書</a>
      </div>

      <div class="imggen-submit-row">
        <label for="ig-batch-count" class="imggen-submit-batch-label">枚数</label>
        <input id="ig-batch-count" class="form-input" type="number" min="1" max="50" value="1" title="同じプロンプトで連続投入する枚数（seed 指定時は +1 ずつ加算）">
        <button id="ig-submit" class="btn btn-primary imggen-submit">投入</button>
      </div>
      <div id="ig-status" class="imggen-status-line"></div>
    </div>
  </section>

  <!-- Presets -->
  <section class="card imggen-section">
    <div class="imggen-header">
      <h3>Workflow Presets（プリセット管理）</h3>
      <button id="ig-preset-new" class="btn btn-sm btn-primary">新規登録</button>
    </div>
    <div id="ig-presets-body"><div class="imggen-empty">Loading...</div></div>
  </section>
</div>
`;
}

// ============================================================
// ComfyUI panel
// ============================================================
async function loadComfyPanel() {
  try {
    const data = await api('/api/image/agents');
    comfyAgents = data?.agents || [];
  } catch (err) {
    console.error('agents load failed', err);
    comfyAgents = [];
  }
  renderComfyPanel({});
  refreshComfyStatus();
}

function renderComfyPanel(statusMap) {
  const el = $('ig-comfy-panel');
  if (!el) return;
  if (!comfyAgents.length) { el.innerHTML = ''; return; }
  el.innerHTML = comfyAgents.map(a => {
    const st = statusMap[a.id] || { loading: true };
    let dotClass = '', statusLabel = '読み込み中...', pidPart = '';
    if (!st.loading) {
      if (st.unreachable) { dotClass = 'error'; statusLabel = 'Agent 応答なし'; }
      else if (st.available) { dotClass = 'running'; statusLabel = '稼働中'; if (st.pid) pidPart = ` (PID ${st.pid})`; }
      else if (st.running) { dotClass = 'starting'; statusLabel = '起動中 / 応答待ち'; }
      else { statusLabel = '停止'; }
    }
    const busy = comfyBusy.has(a.id);
    const isUp = !st.loading && (st.running || st.available);
    const actionBtn = isUp
      ? `<button data-comfy-action="stop" data-agent="${esc(a.id)}" ${busy ? 'disabled' : ''}>停止</button>`
      : `<button data-comfy-action="start" data-agent="${esc(a.id)}" ${busy ? 'disabled' : ''}>起動</button>`;
    return `
      <div class="imggen-comfy-row">
        <span class="dot ${dotClass}"></span>
        <span class="name">${esc(a.name || a.id)}</span>
        <span class="meta">${esc(statusLabel)}${esc(pidPart)}</span>
        <span class="spacer"></span>
        ${actionBtn}
        <a href="${esc(a.comfyui_url)}" target="_blank" rel="noopener" title="${esc(a.comfyui_url)}">開く</a>
      </div>`;
  }).join('');
  el.querySelectorAll('button[data-comfy-action]').forEach(btn => {
    btn.addEventListener('click', () => handleComfyAction(btn.dataset.agent, btn.dataset.comfyAction));
  });
}

async function refreshComfyStatus() {
  if (!comfyAgents.length) return;
  const results = await Promise.all(comfyAgents.map(async a => {
    try { return [a.id, await api(`/api/image/agents/${encodeURIComponent(a.id)}/comfyui/status`)]; }
    catch { return [a.id, { unreachable: true }]; }
  }));
  const map = {};
  results.forEach(([id, s]) => { map[id] = s; });
  renderComfyPanel(map);
}

async function handleComfyAction(agentId, action) {
  if (!agentId || !action || comfyBusy.has(agentId)) return;
  comfyBusy.add(agentId);
  refreshComfyStatus();
  try {
    const res = await api(`/api/image/agents/${encodeURIComponent(agentId)}/comfyui/${action}`, { method: 'POST' });
    if (action === 'stop') {
      if (res?.adopted_kill) {
        toast(`ComfyUI 停止 (外部起動を port 経由で kill, PID ${res.pid})`, 'success');
      } else if (res?.stopped) {
        toast(`ComfyUI 停止完了`, 'success');
      } else {
        toast(res?.note || '既に停止しています', 'info');
      }
    } else {
      toast(`ComfyUI 起動リクエスト送信`, 'info');
    }
  } catch (err) {
    const body = err?.body || err?.data || {};
    const klass = body.error_class || err?.error_class;
    if (action === 'stop' && klass === 'PermissionError') {
      const pid = body.pid ? ` (PID ${body.pid})` : '';
      toast(`権限不足で停止できません${pid}。Sub PC の管理者 PowerShell で Stop-Process を実行してください`, 'error');
    } else {
      toast(`ComfyUI ${action} 失敗: ${body.error || err?.message || err}`, 'error');
    }
  } finally {
    comfyBusy.delete(agentId);
    refreshComfyStatus();
  }
}

// ============================================================
// Workflows
// ============================================================
const LAST_WORKFLOW_KEY = 'imggen:last_workflow';

async function loadWorkflows() {
  const sel = $('ig-workflow');
  if (!sel) return;
  try {
    const data = await GenerationAPI.listWorkflows();
    workflows = data?.workflows || [];
    if (!workflows.length) { sel.innerHTML = '<option value="">(no workflows)</option>'; return; }
    sel.innerHTML = workflows.map(w => {
      const label = `${w.name}${w.description ? ' — ' + w.description : ''}${w.main_pc_only ? ' [main]' : ''}`;
      return `<option value="${esc(w.name)}">${esc(label)}</option>`;
    }).join('');
    // 前回選択した workflow を復元（存在する場合のみ）
    let saved = '';
    try { saved = localStorage.getItem(LAST_WORKFLOW_KEY) || ''; } catch { /* ignore */ }
    if (saved && workflows.some(w => w.name === saved)) {
      sel.value = saved;
    }
  } catch (err) {
    console.error('workflows load failed', err);
    sel.innerHTML = '<option value="">(load failed)</option>';
  }
}

function persistWorkflowSelection() {
  const v = $('ig-workflow')?.value || '';
  try {
    if (v) localStorage.setItem(LAST_WORKFLOW_KEY, v);
    else localStorage.removeItem(LAST_WORKFLOW_KEY);
  } catch { /* ignore */ }
}

function handleWorkflowChange() {
  persistWorkflowSelection();
  loadLoras();
}

// ============================================================
// LoRA selector
// ============================================================
function loraStorageKey(workflow) {
  return `${LORA_LS_PREFIX}${workflow}`;
}

function loadLoraOverridesLS(workflow) {
  if (!workflow) return {};
  try {
    const raw = localStorage.getItem(loraStorageKey(workflow));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return (parsed && typeof parsed === 'object') ? parsed : {};
  } catch { return {}; }
}

function persistLoraOverridesLS() {
  const wf = $('ig-workflow')?.value || '';
  if (!wf) return;
  const map = {};
  for (const node of loraNodes) {
    map[node.node_id] = {
      enabled: !!node.enabled,
      strength: (typeof node.strength === 'number') ? node.strength : null,
    };
  }
  try { localStorage.setItem(loraStorageKey(wf), JSON.stringify(map)); }
  catch { /* ignore */ }
}

async function loadLoras() {
  const wf = $('ig-workflow')?.value || '';
  const block = $('ig-lora-block');
  const list = $('ig-lora-list');
  if (!block || !list) return;
  if (!wf) { block.hidden = true; loraNodes = []; return; }
  list.innerHTML = '<div class="imggen-empty" style="padding:0.4rem;">Loading LoRA...</div>';
  try {
    const data = await GenerationAPI.workflowLoras(wf);
    const items = data?.loras || [];
    if (!items.length) {
      block.hidden = true;
      loraNodes = [];
      return;
    }
    const saved = loadLoraOverridesLS(wf);
    loraNodes = items.map(it => {
      const ovr = saved[it.node_id] || {};
      const defStrength = (typeof it.strength_model === 'number') ? it.strength_model : 1.0;
      return {
        node_id: it.node_id,
        lora_name: it.lora_name || '(unknown)',
        title: it.title || null,
        default_strength: defStrength,
        enabled: (typeof ovr.enabled === 'boolean') ? ovr.enabled : true,
        strength: (typeof ovr.strength === 'number') ? ovr.strength : defStrength,
      };
    });
    block.hidden = false;
    renderLoraList();
  } catch (err) {
    console.error('loras load failed', err);
    list.innerHTML = `<div class="imggen-empty" style="padding:0.4rem;">取得失敗: ${esc(err?.message || err)}</div>`;
    block.hidden = false;
  }
}

function renderLoraList() {
  const list = $('ig-lora-list');
  if (!list) return;
  if (!loraNodes.length) { list.innerHTML = ''; return; }
  list.innerHTML = loraNodes.map((n, i) => {
    const label = n.title ? `${n.title} (${n.lora_name})` : n.lora_name;
    const strengthVal = Number.isFinite(n.strength) ? n.strength.toFixed(2) : n.default_strength.toFixed(2);
    return `
      <div class="imggen-lora-row" data-idx="${i}">
        <label class="imggen-lora-toggle" title="LoRA を適用するか">
          <input type="checkbox" data-lora-toggle ${n.enabled ? 'checked' : ''}>
        </label>
        <span class="imggen-lora-name" title="${esc(n.lora_name)} (node ${esc(n.node_id)})">${esc(label)}</span>
        <input class="imggen-lora-strength" type="range" min="-2" max="2" step="0.05"
               value="${strengthVal}" data-lora-strength ${n.enabled ? '' : 'disabled'}>
        <input class="imggen-lora-strength-num" type="number" step="0.05"
               value="${strengthVal}" data-lora-strength-num ${n.enabled ? '' : 'disabled'}>
      </div>`;
  }).join('');
  list.querySelectorAll('.imggen-lora-row').forEach(row => {
    const idx = Number(row.dataset.idx);
    const toggle = row.querySelector('[data-lora-toggle]');
    const range = row.querySelector('[data-lora-strength]');
    const num = row.querySelector('[data-lora-strength-num]');
    toggle?.addEventListener('change', () => {
      loraNodes[idx].enabled = !!toggle.checked;
      if (range) range.disabled = !toggle.checked;
      if (num) num.disabled = !toggle.checked;
      persistLoraOverridesLS();
    });
    const onStrength = (v) => {
      const f = parseFloat(v);
      if (!Number.isFinite(f)) return;
      loraNodes[idx].strength = f;
      if (range && range.value !== String(f)) range.value = String(f);
      if (num && num.value !== String(f)) num.value = String(f);
      persistLoraOverridesLS();
    };
    range?.addEventListener('input', () => onStrength(range.value));
    num?.addEventListener('change', () => onStrength(num.value));
  });
}

function collectLoraOverrides() {
  if (!loraNodes.length) return [];
  return loraNodes.map(n => ({
    node_id: n.node_id,
    enabled: !!n.enabled,
    strength: Number.isFinite(n.strength) ? n.strength : null,
  }));
}

// ============================================================
// Sections
// ============================================================
async function loadSections() {
  try {
    const [cats, secs] = await Promise.all([
      GenerationAPI.listCategories(),
      GenerationAPI.listSections(),
    ]);
    categories = (cats?.categories || []).sort((a, b) => (a.display_order || 0) - (b.display_order || 0));
    sections = secs?.sections || [];
  } catch (err) {
    console.error('sections load failed', err);
    categories = []; sections = [];
  }
  renderSections();
  renderUserPosOptions();
}

function renderUserPosOptions() {
  const sel = $('ig-userpos');
  if (!sel) return;
  const cur = sel.value || 'tail';
  const catOpts = categories.map(c =>
    `<option value="section:${esc(c.key)}">カテゴリ: ${esc(c.label)}</option>`
  ).join('');
  sel.innerHTML = `
    <option value="tail">末尾</option>
    <option value="head">先頭</option>
    ${catOpts}
  `;
  sel.value = cur;
}

function loadSectionExpandedLS() {
  try {
    const raw = localStorage.getItem(SECTION_EXPANDED_LS);
    sectionExpanded = raw ? (JSON.parse(raw) || {}) : {};
  } catch { sectionExpanded = {}; }
}

function persistSectionExpandedLS() {
  try { localStorage.setItem(SECTION_EXPANDED_LS, JSON.stringify(sectionExpanded)); }
  catch { /* ignore */ }
}

function isCategoryExpanded(key) {
  if (key in sectionExpanded) return !!sectionExpanded[key];
  // 初期値: quality / character / style は展開、その他は折畳
  return ['quality', 'character', 'style'].includes(key);
}

function setAllCategoriesExpanded(expanded) {
  for (const c of categories) sectionExpanded[c.key] = !!expanded;
  persistSectionExpandedLS();
  renderSections();
}

function renderSections() {
  const el = $('ig-sec-cats');
  if (!el) return;
  if (!categories.length) {
    el.innerHTML = '<div class="imggen-empty" style="padding:0.4rem;">カテゴリがありません</div>';
    return;
  }
  el.innerHTML = categories.map(c => {
    const expanded = isCategoryExpanded(c.key);
    const list = sections.filter(s => s.category_key === c.key)
      .sort((a, b) => (b.starred - a.starred) || a.name.localeCompare(b.name));
    const chips = list.map(s => {
      const selected = chosen.includes(s.id);
      const star = s.starred ? '★ ' : '';
      const tooltip = [s.positive, s.negative].filter(Boolean).join(' / ');
      return `<span class="imggen-section-chip ${selected ? 'selected' : ''}" data-sid="${s.id}" title="${esc(tooltip)}">
        <span class="chip-label">${star}${esc(s.name)}</span>
        <span class="chip-edit" data-edit-sid="${s.id}" title="編集">✎</span>
      </span>`;
    }).join('') || '<span class="text-muted text-xs">（未登録）</span>';
    return `
      <div class="imggen-section-cat ${expanded ? 'expanded' : 'collapsed'}" data-cat="${esc(c.key)}">
        <div class="cat-label">
          <button class="cat-toggle" data-toggle-cat="${esc(c.key)}" title="${expanded ? '折畳' : '展開'}">${expanded ? '▼' : '▶'}</button>
          <strong>${esc(c.label)}</strong>
          <span class="cat-count">(${list.length})</span>
          <button class="add-btn" data-add-cat="${esc(c.key)}">+ 追加</button>
        </div>
        <div class="imggen-section-picker" ${expanded ? '' : 'hidden'}>${chips}</div>
      </div>`;
  }).join('');

  el.querySelectorAll('[data-toggle-cat]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const key = btn.dataset.toggleCat;
      sectionExpanded[key] = !isCategoryExpanded(key);
      persistSectionExpandedLS();
      renderSections();
    });
  });
  el.querySelectorAll('.imggen-section-chip').forEach(chip => {
    chip.addEventListener('click', (e) => {
      // 編集アイコンクリック時は選択トグルしない
      if (e.target.closest('[data-edit-sid]')) return;
      const sid = Number(chip.dataset.sid);
      const idx = chosen.indexOf(sid);
      if (idx >= 0) chosen.splice(idx, 1);
      else chosen.push(sid);
      renderChosen();
      chip.classList.toggle('selected');
    });
  });
  el.querySelectorAll('[data-edit-sid]').forEach(icon => {
    icon.addEventListener('click', (e) => {
      e.stopPropagation();
      const sid = Number(icon.dataset.editSid);
      openSectionModal({ section_id: sid });
    });
  });
  el.querySelectorAll('[data-add-cat]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openSectionModal({ category_key: btn.dataset.addCat });
    });
  });
  renderChosen();
}

function renderChosen() {
  const el = $('ig-sec-chosen');
  if (!el) return;
  if (!chosen.length) {
    el.innerHTML = '<span class="text-muted text-xs">（セクション未選択）</span>';
    return;
  }
  el.innerHTML = chosen.map(sid => {
    const s = sections.find(x => x.id === sid);
    if (!s) return '';
    return `<span class="imggen-chosen-chip" data-key="${sid}" data-sid="${sid}">
      ${esc(s.name)}
      <span class="x" data-remove="${sid}">×</span>
    </span>`;
  }).join('');
  el.querySelectorAll('[data-remove]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sid = Number(btn.dataset.remove);
      chosen = chosen.filter(x => x !== sid);
      renderSections();
    });
  });
  makeSortable(el, (order) => {
    chosen = order.map(k => Number(k)).filter(n => !isNaN(n));
  });
}

// ============================================================
// Section presets（DB 永続化）
// ============================================================
async function loadSectionPresets() {
  try {
    const data = await GenerationAPI.listSectionPresets();
    sectionPresets = data?.presets || [];
  } catch (err) {
    console.error('section presets load failed', err);
    sectionPresets = [];
  }
  renderSectionPresetSelect();
}

function renderSectionPresetSelect() {
  const sel = $('ig-secpreset-select');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = `<option value="">— セクションプリセット選択 —</option>` +
    sectionPresets.map(p => {
      const sids = (p.payload?.section_ids || []).length;
      return `<option value="${p.id}">${esc(p.name)} (${sids} sections)</option>`;
    }).join('');
  if (cur && sectionPresets.some(p => String(p.id) === cur)) sel.value = cur;
}

function applySectionPreset(payload) {
  if (!payload) return;
  const sids = (payload.section_ids || []).filter(sid => sections.some(s => s.id === sid));
  chosen = sids;
  $('ig-positive').value = payload.user_positive || '';
  $('ig-negative').value = payload.user_negative || '';
  if (payload.user_position) {
    const sel = $('ig-userpos');
    if (sel) {
      const exists = Array.from(sel.options).some(o => o.value === payload.user_position);
      if (exists) sel.value = payload.user_position;
    }
  }
  renderSections();
}

function currentSectionPresetPayload() {
  return {
    section_ids: chosen.slice(),
    user_positive: $('ig-positive')?.value || '',
    user_negative: $('ig-negative')?.value || '',
    user_position: $('ig-userpos')?.value || 'tail',
  };
}

async function handleSectionPresetLoad() {
  const sel = $('ig-secpreset-select');
  const id = Number(sel?.value);
  if (!id) { toast('プリセットを選択', 'error'); return; }
  const p = sectionPresets.find(x => x.id === id);
  if (!p) { toast('プリセットが見つからない', 'error'); return; }
  applySectionPreset(p.payload);
  toast(`「${p.name}」を読込`, 'success');
}

async function handleSectionPresetSave() {
  const name = prompt('新規セクションプリセット名（1〜64 文字）:', '');
  if (!name) return;
  const trimmed = name.trim();
  if (!trimmed || trimmed.length > 64) { toast('名前は 1〜64 文字', 'error'); return; }
  try {
    const res = await GenerationAPI.createSectionPreset({
      name: trimmed,
      payload: currentSectionPresetPayload(),
    });
    toast('保存しました', 'success');
    await loadSectionPresets();
    const sel = $('ig-secpreset-select');
    if (sel && res?.id) sel.value = String(res.id);
  } catch (err) {
    toast(`保存失敗: ${err?.message || err}`, 'error');
  }
}

async function handleSectionPresetOverwrite() {
  const sel = $('ig-secpreset-select');
  const id = Number(sel?.value);
  if (!id) { toast('上書き対象を選択', 'error'); return; }
  const p = sectionPresets.find(x => x.id === id);
  if (!p) return;
  if (!confirm(`「${p.name}」を現在の選択で上書きしますか？`)) return;
  try {
    await GenerationAPI.updateSectionPreset(id, { payload: currentSectionPresetPayload() });
    toast('上書きしました', 'success');
    await loadSectionPresets();
    sel.value = String(id);
  } catch (err) {
    toast(`上書き失敗: ${err?.message || err}`, 'error');
  }
}

async function handleSectionPresetDelete() {
  const sel = $('ig-secpreset-select');
  const id = Number(sel?.value);
  if (!id) { toast('削除対象を選択', 'error'); return; }
  const p = sectionPresets.find(x => x.id === id);
  if (!p) return;
  if (!confirm(`プリセット「${p.name}」を削除しますか？`)) return;
  try {
    await GenerationAPI.deleteSectionPreset(id);
    toast('削除しました', 'info');
    await loadSectionPresets();
  } catch (err) {
    toast(`削除失敗: ${err?.message || err}`, 'error');
  }
}

// ============================================================
// Compose preview (client-side; モーダルで表示)
// ============================================================
function closePreviewModal() {
  const root = $('ig-preview-modal-root');
  if (root) root.innerHTML = '';
}

function runPreview() {
  const root = $('ig-preview-modal-root');
  if (!root) return;
  const rows = chosen.map(sid => sections.find(s => s.id === sid)).filter(Boolean);
  const userPos = $('ig-positive')?.value || '';
  const userNeg = $('ig-negative')?.value || '';
  const pos = $('ig-userpos')?.value || 'tail';
  const res = composePromptClient(rows, {
    userPositive: userPos, userNegative: userNeg, userPosition: pos,
  });
  const warnHtml = res.warnings.length
    ? `<div class="imggen-preview-warn">⚠ ${res.warnings.map(esc).join(' / ')}</div>` : '';
  const sectionLines = rows.length
    ? rows.map(s => `<li>${esc(s.name)} <span class="text-muted">(${esc(s.category_key)})</span></li>`).join('')
    : '<li class="text-muted">（未選択）</li>';
  root.innerHTML = `
    <div class="imggen-modal-backdrop" id="ig-preview-bg">
      <div class="imggen-modal imggen-modal-wide">
        <div class="imggen-modal-header">
          <span>🔍 合成プレビュー</span>
          <button id="ig-preview-close" class="btn btn-sm">×</button>
        </div>
        <div class="imggen-modal-body">
          ${warnHtml}
          <div class="imggen-preview-section">
            <div class="imggen-preview-label">POSITIVE</div>
            <div class="imggen-preview-text">${esc(res.positive) || '<span class="text-muted">(empty)</span>'}</div>
            <button class="btn btn-sm imggen-preview-copy" data-copy-target="pos">📋 コピー</button>
          </div>
          <div class="imggen-preview-section">
            <div class="imggen-preview-label">NEGATIVE</div>
            <div class="imggen-preview-text">${esc(res.negative) || '<span class="text-muted">(empty)</span>'}</div>
            <button class="btn btn-sm imggen-preview-copy" data-copy-target="neg">📋 コピー</button>
          </div>
          <div class="imggen-preview-section">
            <div class="imggen-preview-label">使用セクション (${rows.length})</div>
            <ul class="imggen-preview-list">${sectionLines}</ul>
          </div>
          <div class="imggen-preview-section">
            <div class="imggen-preview-label">挿入位置</div>
            <div>${esc(pos)}</div>
          </div>
        </div>
        <div class="imggen-modal-footer">
          <button id="ig-preview-cancel" class="btn btn-sm">閉じる</button>
        </div>
      </div>
    </div>`;
  $('ig-preview-close')?.addEventListener('click', closePreviewModal);
  $('ig-preview-cancel')?.addEventListener('click', closePreviewModal);
  bindModalBackdropClose($('ig-preview-bg'), closePreviewModal);
  root.querySelectorAll('[data-copy-target]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const text = btn.dataset.copyTarget === 'pos' ? res.positive : res.negative;
      try { await navigator.clipboard.writeText(text || ''); toast('コピーしました', 'info'); }
      catch { toast('コピー失敗', 'error'); }
    });
  });
}

// ============================================================
// Section create/edit modal
// ============================================================
function closeSectionModal() {
  const root = $('ig-sec-modal-root');
  if (root) root.innerHTML = '';
}

function openSectionModal({ category_key = '', section_id = null } = {}) {
  const root = $('ig-sec-modal-root');
  if (!root) return;
  const editing = section_id ? sections.find(s => s.id === section_id) : null;
  const catOpts = categories.map(c =>
    `<option value="${esc(c.key)}" ${c.key === (editing?.category_key || category_key) ? 'selected' : ''}>${esc(c.label)}</option>`
  ).join('');
  root.innerHTML = `
    <div class="imggen-modal-backdrop" id="ig-sec-bg">
      <div class="imggen-modal">
        <div class="imggen-modal-header">
          <span>セクション${editing ? '編集' : '新規登録'}</span>
          <button id="ig-sec-close" class="btn btn-sm">×</button>
        </div>
        <div class="imggen-modal-body">
          <div class="imggen-meta-grid">
            <div>
              <label class="text-xs">category</label>
              <select id="ig-sec-cat" class="form-input">${catOpts}</select>
            </div>
            <div>
              <label class="text-xs">name</label>
              <input id="ig-sec-name" class="form-input" type="text" value="${esc(editing?.name || '')}">
            </div>
          </div>
          <label class="text-xs">positive（,区切り）</label>
          <textarea id="ig-sec-pos" style="min-height:60px;">${esc(editing?.positive || '')}</textarea>
          <label class="text-xs">negative（,区切り）</label>
          <textarea id="ig-sec-neg" style="min-height:60px;">${esc(editing?.negative || '')}</textarea>
          <label class="text-xs">description</label>
          <input id="ig-sec-desc" class="form-input" type="text" value="${esc(editing?.description || '')}">
          <label class="text-xs">
            <input id="ig-sec-star" type="checkbox" ${editing?.starred ? 'checked' : ''}> お気に入り
          </label>
        </div>
        <div class="imggen-modal-footer">
          ${editing && !editing.is_builtin
            ? `<button id="ig-sec-del" class="btn btn-sm btn-danger" style="margin-right:auto;">削除</button>` : ''}
          <button id="ig-sec-cancel" class="btn btn-sm">キャンセル</button>
          <button id="ig-sec-save" class="btn btn-sm btn-primary">${editing ? '更新' : '登録'}</button>
        </div>
      </div>
    </div>`;
  const close = closeSectionModal;
  $('ig-sec-close')?.addEventListener('click', close);
  $('ig-sec-cancel')?.addEventListener('click', close);
  bindModalBackdropClose($('ig-sec-bg'), close);
  $('ig-sec-save')?.addEventListener('click', () => handleSectionSave(editing));
  $('ig-sec-del')?.addEventListener('click', () => handleSectionDelete(editing));
}

async function handleSectionSave(editing) {
  const body = {
    category_key: $('ig-sec-cat')?.value,
    name: ($('ig-sec-name')?.value || '').trim(),
    positive: $('ig-sec-pos')?.value || '',
    negative: $('ig-sec-neg')?.value || '',
    description: ($('ig-sec-desc')?.value || '').trim(),
    starred: !!$('ig-sec-star')?.checked,
  };
  if (!body.name) { toast('name は必須', 'error'); return; }
  try {
    if (editing) {
      await GenerationAPI.updateSection(editing.id, body);
      toast('更新しました', 'success');
    } else {
      await GenerationAPI.createSection(body);
      toast('登録しました', 'success');
    }
    closeSectionModal();
    await loadSections();
  } catch (err) {
    toast(`保存失敗: ${err?.message || err}`, 'error');
  }
}

async function handleSectionDelete(editing) {
  if (!editing || !confirm(`セクション "${editing.name}" を削除しますか？`)) return;
  try {
    await GenerationAPI.deleteSection(editing.id);
    chosen = chosen.filter(x => x !== editing.id);
    toast('削除しました', 'info');
    closeSectionModal();
    await loadSections();
  } catch (err) {
    toast(`削除失敗: ${err?.message || err}`, 'error');
  }
}

// ============================================================
// Prompt crafter intake (stash から取り込み)
// ============================================================
async function checkStashPrefill() {
  const h = location.hash || '';
  const qi = h.indexOf('?');
  if (qi < 0) return;
  const params = new URLSearchParams(h.slice(qi + 1));
  const kind = params.get('prefill');
  if (!kind) return;
  const stash = stashGet();
  if (!stash) return;
  try {
    const sel = $('ig-workflow');
    let workflowChanged = false;
    if (sel && stash.workflow_name) {
      const found = Array.from(sel.options).some(o => o.value === stash.workflow_name);
      if (found && sel.value !== stash.workflow_name) {
        sel.value = stash.workflow_name;
        persistWorkflowSelection();
        workflowChanged = true;
      }
    }
    if (stash.positive != null) $('ig-positive').value = stash.positive || '';
    if (stash.negative != null) $('ig-negative').value = stash.negative || '';
    if (Array.isArray(stash.section_ids) && stash.section_ids.length) {
      const valid = stash.section_ids.filter(sid => sections.some(s => s.id === sid));
      if (valid.length) {
        chosen = valid;
        renderSections();
      }
    }
    if (stash.user_position && $('ig-userpos')) {
      const opt = Array.from($('ig-userpos').options).find(o => o.value === stash.user_position);
      if (opt) $('ig-userpos').value = stash.user_position;
    }
    const p = stash.params || {};
    const map = {
      WIDTH: 'ig-width', HEIGHT: 'ig-height', STEPS: 'ig-steps', CFG: 'ig-cfg',
      SEED: 'ig-seed', SAMPLER: 'ig-sampler', SCHEDULER: 'ig-scheduler',
    };
    for (const [k, id] of Object.entries(map)) {
      if (p[k] !== undefined && $(id)) $(id).value = p[k];
    }
    // ギャラリー再利用時に LoRA 状態も引き継ぐ
    const ovrList = Array.isArray(p.__LORA_OVERRIDES__) ? p.__LORA_OVERRIDES__ : null;
    if (workflowChanged) await loadLoras();
    if (ovrList && loraNodes.length) {
      for (const ovr of ovrList) {
        const node = loraNodes.find(n => n.node_id === String(ovr.node_id));
        if (!node) continue;
        if (typeof ovr.enabled === 'boolean') node.enabled = ovr.enabled;
        if (typeof ovr.strength === 'number') node.strength = ovr.strength;
      }
      renderLoraList();
      persistLoraOverridesLS();
    }
    toast(`取り込み完了（${kind}）`, 'info');
    stashClear();
  } catch (err) {
    console.error('prefill failed', err);
  }
}

function handlePromptCrafterClick() {
  location.hash = '#/prompts';
  toast('Prompts ページで履歴を選んでください', 'info');
}

// ============================================================
// Submit
// ============================================================
function readNum(id) {
  const v = $(id)?.value?.trim();
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function readStr(id) {
  const v = $(id)?.value?.trim();
  return v || null;
}

/** positive/negative を同一 seed で展開して trace をまとめる。
 *  サーバ側 wildcard_expander.expand と対称な JS 実装（lib/wildcard.js）を呼ぶ。
 *  __WILDCARD_TRACE__ は params に同梱してジョブ DB に残し、再現性を担保する。
 */
function doExpand(positive, negative, files, rngSeed) {
  const expP = expandWildcards(positive, { files, rngSeed });
  const expN = expandWildcards(negative, { files, rngSeed });
  return {
    positive: expP.text,
    negative: expN.text,
    trace: {
      template_positive: positive,
      template_negative: negative,
      rng_seed: rngSeed,
      choices: [...expP.choices, ...expN.choices],
      warnings: [...expP.warnings, ...expN.warnings],
    },
  };
}

async function handleSubmit() {
  const btn = $('ig-submit');
  const statusEl = $('ig-status');
  const workflow_name = $('ig-workflow')?.value;
  if (!workflow_name) { toast('Workflow is required', 'error'); return; }
  const positive = $('ig-positive')?.value?.trim() || '';
  const negative = $('ig-negative')?.value?.trim() || '';
  const baseParams = {};
  const w = readNum('ig-width');  if (w !== null) baseParams.WIDTH = w;
  const h = readNum('ig-height'); if (h !== null) baseParams.HEIGHT = h;
  const st = readNum('ig-steps'); if (st !== null) baseParams.STEPS = st;
  const c  = readNum('ig-cfg');   if (c !== null) baseParams.CFG = c;
  const seedSpecified = readNum('ig-seed');
  if (seedSpecified !== null) baseParams.SEED = seedSpecified;
  const sp = readStr('ig-sampler');   if (sp) baseParams.SAMPLER = sp;
  const sc = readStr('ig-scheduler'); if (sc) baseParams.SCHEDULER = sc;

  // バッチ枚数。seed が指定されていれば +1 ずつ加算、未指定/-1 なら毎回ランダム任せ
  let count = parseInt($('ig-batch-count')?.value, 10);
  if (!Number.isFinite(count) || count < 1) count = 1;
  if (count > 50) count = 50;
  const incrementSeed = seedSpecified !== null && seedSpecified >= 0;

  // --- Wildcard 展開準備 ---
  //   mode: random = 毎バッチ独立展開
  //         tied   = 画像 seed に追従（未指定なら random フォールバック）
  //         fixed  = ループ前に 1 回だけ展開して使い回す
  const wcMode = $('ig-wildcard-mode')?.value || 'random';
  const wcSeedBase = readNum('ig-wildcard-seed');   // null or int
  const needsExpansion = hasWildcardToken(positive) || hasWildcardToken(negative);
  let wcFiles = {};
  let fixedExpansion = null;
  if (needsExpansion) {
    try {
      wcFiles = await loadWildcardFiles();
    } catch (err) {
      console.error('wildcard bulk load failed', err);
      toast('Wildcard 辞書の取得に失敗。展開せず投入します', 'error');
    }
    if (wcMode === 'fixed') {
      const fixedSeed = wcSeedBase !== null
        ? wcSeedBase
        : Math.floor(Math.random() * 0x7FFFFFFF);
      fixedExpansion = doExpand(positive, negative, wcFiles, fixedSeed);
    }
  }

  btn.disabled = true;
  const jobIds = [];
  const failures = [];
  try {
    for (let i = 0; i < count; i++) {
      const params = { ...baseParams };
      const imageSeed = incrementSeed ? seedSpecified + i : null;
      if (imageSeed !== null) params.SEED = imageSeed;

      // --- Wildcard 展開（必要なら）---
      let iterPositive = positive;
      let iterNegative = negative;
      if (needsExpansion) {
        let expansion;
        if (wcMode === 'fixed') {
          expansion = fixedExpansion;
        } else {
          let wcSeed;
          if (wcMode === 'tied' && imageSeed !== null) {
            wcSeed = imageSeed;
          } else if (wcSeedBase !== null) {
            wcSeed = wcSeedBase + i;
          } else {
            wcSeed = null;   // 完全ランダム
          }
          expansion = doExpand(positive, negative, wcFiles, wcSeed);
        }
        iterPositive = expansion.positive;
        iterNegative = expansion.negative;
        params.__WILDCARD_TRACE__ = expansion.trace;
      }

      statusEl.textContent = count > 1
        ? `投入中... ${i + 1}/${count}`
        : '投入中...';
      try {
        const body = {
          workflow_name,
          positive: iterPositive, negative: iterNegative, params,
          section_ids: chosen,
          user_position: $('ig-userpos')?.value || 'tail',
        };
        const overrides = collectLoraOverrides();
        if (overrides.length) body.lora_overrides = overrides;
        const res = await GenerationAPI.submit(body);
        if (res?.job_id) jobIds.push(res.job_id);
      } catch (err) {
        console.error(`generate ${i + 1}/${count} failed`, err);
        failures.push({ i: i + 1, err });
      }
    }
    if (jobIds.length) {
      const head = jobIds[0];
      statusEl.innerHTML = count > 1
        ? `Enqueued ${jobIds.length}/${count}: <code>${esc(head)}</code> ほか — <a href="#/jobs">Jobs を見る →</a>`
        : `Enqueued: <code>${esc(head)}</code> — <a href="#/jobs">Jobs を見る →</a>`;
    } else {
      statusEl.textContent = 'Error: 全ジョブ投入失敗';
    }
    if (failures.length) {
      toast(`投入失敗 ${failures.length}/${count}`, 'error');
    } else {
      toast(count > 1 ? `${count} 件投入しました` : 'Job enqueued', 'success');
    }
  } finally {
    btn.disabled = false;
  }
}

// ============================================================
// Presets（Workflow JSON 管理）
// ============================================================
const _PLACEHOLDER_OPTIONS = [
  'POSITIVE', 'NEGATIVE', 'SEED', 'STEPS', 'CFG', 'WIDTH', 'HEIGHT',
  'CKPT', 'VAE', 'SAMPLER', 'SCHEDULER', 'FILENAME_PREFIX', 'DENOISE',
  'LORA_1', 'LORA_2', 'LORA_3', 'STRENGTH_1', 'STRENGTH_2', 'STRENGTH_3',
];

async function loadPresets() {
  const el = $('ig-presets-body');
  if (!el) return;
  try {
    const data = await GenerationAPI.listWorkflows();
    const list = data?.workflows || [];
    if (!list.length) {
      el.innerHTML = '<div class="imggen-empty">まだプリセットがありません</div>';
      return;
    }
    el.innerHTML = `<div class="imggen-presets-list">${list.map(w => {
      const cat = w.category ? `<span class="tag">${esc(w.category)}</span>` : '';
      const mpc = w.main_pc_only ? '<span class="tag">main-pc</span>' : '';
      const nodes = (w.required_nodes || []).length;
      const loras = (w.required_loras || []).length;
      return `
        <div class="imggen-preset-row">
          <div>
            <div><strong>${esc(w.name)}</strong> ${cat}${mpc}</div>
            <div class="meta">${esc(w.description || '(no description)')}</div>
            <div class="meta">nodes: ${nodes} / loras: ${loras} / timeout: ${w.default_timeout_sec ?? '—'}s</div>
          </div>
          <button class="btn btn-sm" data-preset-view="${w.id}">編集</button>
          <button class="btn btn-sm btn-danger" data-preset-del="${w.id}" data-preset-name="${esc(w.name)}">削除</button>
        </div>`;
    }).join('')}</div>`;
    el.onclick = async (e) => {
      const del = e.target.closest('button[data-preset-del]');
      const view = e.target.closest('button[data-preset-view]');
      if (del) {
        const id = Number(del.dataset.presetDel);
        const name = del.dataset.presetName;
        if (!confirm(`プリセット "${name}" を削除しますか？`)) return;
        try {
          await api(`/api/image/workflows/${id}`, { method: 'DELETE' });
          toast('削除しました', 'info');
          await Promise.all([loadPresets(), loadWorkflows()]);
        } catch (err) { toast(`削除失敗: ${err?.message || err}`, 'error'); }
      }
      if (view) {
        const id = Number(view.dataset.presetView);
        try {
          const data = await api(`/api/image/workflows/${id}`);
          openPresetModal({ edit: data });
        } catch (err) { toast(`読み込み失敗: ${err?.message || err}`, 'error'); }
      }
    };
  } catch (err) {
    console.error('presets load failed', err);
    el.innerHTML = '<div class="imggen-empty">プリセット取得失敗</div>';
  }
}

function closePresetModal() {
  const root = $('ig-preset-modal-root');
  if (root) root.innerHTML = '';
  presetModalState = { source: '', workflowJson: null, sourceLabel: '' };
}

function openPresetModal({ edit = null } = {}) {
  presetModalState.workflowJson = edit ? (edit.workflow_json || null) : null;
  presetModalState.sourceLabel = edit ? `edit: ${edit.name}` : '';
  presetModalState.source = edit ? 'edit' : '';
  renderPresetModal(edit);
}

function renderPresetModal(edit = null) {
  const root = $('ig-preset-modal-root');
  if (!root) return;
  const tabsHtml = comfyAgents.map(a =>
    `<button data-ph-source="history:${esc(a.id)}">履歴: ${esc(a.name || a.id)}</button>`
  ).join('') + `<button data-ph-source="file">ファイル</button>`;
  root.innerHTML = `
    <div class="imggen-modal-backdrop" id="ig-preset-modal-bg">
      <div class="imggen-modal" role="dialog">
        <div class="imggen-modal-header">
          <span>プリセット${edit ? '編集' : '登録'}</span>
          <button id="ig-preset-modal-close" class="btn btn-sm">×</button>
        </div>
        <div class="imggen-modal-body">
          ${edit ? '' : `
            <div>
              <label class="text-xs">ソース選択</label>
              <div class="imggen-source-tabs">${tabsHtml}</div>
              <input id="ig-preset-file" type="file" accept=".json,application/json" style="display:none;">
              <div id="ig-preset-history" style="margin-top:0.4rem;"></div>
            </div>`}
          <div>
            <label class="text-xs">Placeholder 編集</label>
            <div id="ig-preset-ph">${renderPlaceholderEditor()}</div>
          </div>
          <div>
            <label class="text-xs">Workflow JSON</label>
            <textarea id="ig-preset-json" class="imggen-json-preview">${esc(
              presetModalState.workflowJson ? JSON.stringify(presetModalState.workflowJson, null, 2) : ''
            )}</textarea>
          </div>
          <div>
            <label class="text-xs">メタ情報</label>
            ${renderMetaForm(edit)}
          </div>
        </div>
        <div class="imggen-modal-footer">
          <button id="ig-preset-cancel" class="btn btn-sm">キャンセル</button>
          <button id="ig-preset-save" class="btn btn-sm btn-primary">${edit ? '更新' : '登録'}</button>
        </div>
      </div>
    </div>`;
  $('ig-preset-modal-close')?.addEventListener('click', closePresetModal);
  $('ig-preset-cancel')?.addEventListener('click', closePresetModal);
  bindModalBackdropClose($('ig-preset-modal-bg'), closePresetModal);
  $('ig-preset-save')?.addEventListener('click', () => handlePresetSave(edit));
  $('ig-preset-json')?.addEventListener('input', (e) => {
    try {
      const parsed = JSON.parse(e.target.value);
      if (parsed && typeof parsed === 'object') {
        presetModalState.workflowJson = parsed;
        $('ig-preset-ph').innerHTML = renderPlaceholderEditor();
        bindPlaceholderActions();
      }
    } catch { /* 無効JSON中は無視 */ }
  });
  root.querySelectorAll('[data-ph-source]').forEach(btn => {
    btn.addEventListener('click', () => handleSourceSelect(btn.dataset.phSource));
  });
  bindPlaceholderActions();
}

function renderMetaForm(edit) {
  const m = edit || {};
  return `
    <div class="imggen-meta-grid">
      <div>
        <label class="text-xs">name</label>
        <input id="ig-meta-name" class="form-input" type="text"
          value="${esc(m.name || '')}" ${edit ? 'readonly' : ''}
          placeholder="英数/_/-、1〜64文字">
      </div>
      <div>
        <label class="text-xs">category</label>
        <input id="ig-meta-category" class="form-input" type="text" value="${esc(m.category || 't2i')}">
      </div>
      <div>
        <label class="text-xs">default_timeout_sec</label>
        <input id="ig-meta-timeout" class="form-input" type="number" min="10" value="${Number(m.default_timeout_sec) || 300}">
      </div>
      <div>
        <label class="text-xs">main_pc_only</label>
        <select id="ig-meta-mpc" class="form-input">
          <option value="false" ${!m.main_pc_only ? 'selected' : ''}>false</option>
          <option value="true"  ${ m.main_pc_only ? 'selected' : ''}>true</option>
        </select>
      </div>
    </div>
    <label class="text-xs" style="margin-top:0.3rem; display:block;">description</label>
    <input id="ig-meta-desc" class="form-input" type="text" value="${esc(m.description || '')}">
  `;
}

function renderPlaceholderEditor() {
  const wf = presetModalState.workflowJson;
  if (!wf || typeof wf !== 'object') {
    return '<div class="imggen-empty" style="padding:0.6rem;">ワークフロー未読込</div>';
  }
  const literals = extractStringLiterals(wf);
  if (!literals.length) return '<div class="imggen-empty" style="padding:0.6rem;">編集可能な文字列フィールドなし</div>';
  const optHtml = _PLACEHOLDER_OPTIONS.map(k => `<option value="${k}">{{${k}}}</option>`).join('');
  return `
    <table class="imggen-ph-table">
      <thead><tr><th>node</th><th>class_type</th><th>key</th><th>value</th><th>アクション</th></tr></thead>
      <tbody>
        ${literals.map(x => {
          const isPh = /^\{\{[A-Z0-9_]+\}\}$/.test(x.value);
          const valHtml = isPh
            ? `<span class="is-ph">${esc(x.value)}</span>`
            : esc(x.value.length > 80 ? x.value.slice(0, 80) + '…' : x.value);
          return `
            <tr data-nid="${esc(x.nodeId)}" data-key="${esc(x.key)}">
              <td>${esc(x.nodeId)}</td>
              <td>${esc(x.classType)}</td>
              <td>${esc(x.key)}</td>
              <td class="val">${valHtml}</td>
              <td class="imggen-ph-actions">
                <select data-ph-key><option value="">--</option>${optHtml}</select>
                <button class="btn btn-sm" data-ph-apply>↔</button>
                ${isPh ? `<button class="btn btn-sm" data-ph-clear>解除</button>` : ''}
              </td>
            </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

function bindPlaceholderActions() {
  document.querySelectorAll('#ig-preset-ph button[data-ph-apply]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      if (!tr) return;
      const sel = tr.querySelector('select[data-ph-key]');
      const key = sel?.value;
      if (!key) { toast('プレースホルダを選択', 'error'); return; }
      const nid = tr.dataset.nid;
      const k = tr.dataset.key;
      if (presetModalState.workflowJson?.[nid]?.inputs) {
        presetModalState.workflowJson[nid].inputs[k] = `{{${key}}}`;
        refreshModalAfterEdit();
      }
    });
  });
  document.querySelectorAll('#ig-preset-ph button[data-ph-clear]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      if (!tr) return;
      const nid = tr.dataset.nid;
      const k = tr.dataset.key;
      const def = prompt('新しい値（空でキャンセル）:', '');
      if (def == null) return;
      if (presetModalState.workflowJson?.[nid]?.inputs) {
        presetModalState.workflowJson[nid].inputs[k] = def;
        refreshModalAfterEdit();
      }
    });
  });
}

function refreshModalAfterEdit() {
  const jsonEl = $('ig-preset-json');
  if (jsonEl) jsonEl.value = JSON.stringify(presetModalState.workflowJson, null, 2);
  const phEl = $('ig-preset-ph');
  if (phEl) phEl.innerHTML = renderPlaceholderEditor();
  bindPlaceholderActions();
}

function extractStringLiterals(wf) {
  const out = [];
  for (const [nid, node] of Object.entries(wf)) {
    if (!node || typeof node !== 'object' || nid === '_meta') continue;
    const inputs = node.inputs || {};
    for (const [k, v] of Object.entries(inputs)) {
      if (typeof v !== 'string') continue;
      out.push({ nodeId: nid, classType: node.class_type || '', key: k, value: v });
    }
  }
  return out;
}

async function handleSourceSelect(src) {
  presetModalState.source = src;
  document.querySelectorAll('[data-ph-source]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.phSource === src);
  });
  const histEl = $('ig-preset-history');
  if (src === 'file') {
    if (histEl) histEl.innerHTML = '';
    const f = $('ig-preset-file');
    f?.click();
    f.onchange = async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const parsed = JSON.parse(await file.text());
        if (!parsed || typeof parsed !== 'object') throw new Error('invalid JSON');
        presetModalState.workflowJson = parsed;
        presetModalState.sourceLabel = `file: ${file.name}`;
        refreshModalAfterEdit();
        toast(`読み込み: ${file.name}`, 'info');
      } catch (err) { toast(`JSON 解析失敗: ${err?.message || err}`, 'error'); }
    };
    return;
  }
  if (src.startsWith('history:')) {
    const agentId = src.slice('history:'.length);
    if (histEl) histEl.innerHTML = '<div class="imggen-empty" style="padding:0.5rem;">履歴取得中...</div>';
    try {
      const data = await api(`/api/image/agents/${encodeURIComponent(agentId)}/comfyui/history?limit=20`);
      const items = data?.items || [];
      if (!data?.available) {
        histEl.innerHTML = '<div class="imggen-empty" style="padding:0.5rem;">ComfyUI 停止中</div>';
        return;
      }
      if (!items.length) { histEl.innerHTML = '<div class="imggen-empty" style="padding:0.5rem;">履歴なし</div>'; return; }
      histEl.innerHTML = `<div class="imggen-history-list">${items.map((it, i) => {
        const files = (it.output_files || []).join(', ');
        return `<div class="imggen-history-item" data-hidx="${i}">
          <span class="pid">${esc(String(it.prompt_id).slice(0, 8))}</span>
          <span>${esc(it.completed ? '✓' : (it.status_str || '?'))}</span>
          <span style="flex:1;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(files)}</span>
        </div>`;
      }).join('')}</div>`;
      histEl.querySelectorAll('.imggen-history-item').forEach(it => {
        it.addEventListener('click', () => {
          const idx = Number(it.dataset.hidx);
          const picked = items[idx]?.workflow;
          if (!picked) { toast('API 形式なし', 'error'); return; }
          presetModalState.workflowJson = picked;
          presetModalState.sourceLabel = `history: ${agentId}`;
          histEl.querySelectorAll('.imggen-history-item').forEach(x => x.classList.remove('selected'));
          it.classList.add('selected');
          refreshModalAfterEdit();
        });
      });
    } catch (err) {
      histEl.innerHTML = `<div class="imggen-empty" style="padding:0.5rem;">取得失敗: ${esc(err?.message || err)}</div>`;
    }
  }
}

async function handlePresetSave(edit) {
  const name = edit ? edit.name : ($('ig-meta-name')?.value || '').trim();
  if (!/^[a-zA-Z0-9_\-]{1,64}$/.test(name)) { toast('name は英数/_/- の 1〜64 文字', 'error'); return; }
  let wfJson = presetModalState.workflowJson;
  const raw = $('ig-preset-json')?.value || '';
  if (raw.trim()) {
    try { wfJson = JSON.parse(raw); }
    catch (err) { toast(`JSON 解析失敗: ${err.message}`, 'error'); return; }
  }
  if (!wfJson || typeof wfJson !== 'object') { toast('Workflow JSON が空', 'error'); return; }
  const body = {
    name, workflow_json: wfJson,
    description: ($('ig-meta-desc')?.value || '').trim(),
    category: ($('ig-meta-category')?.value || 't2i').trim(),
    default_timeout_sec: Number($('ig-meta-timeout')?.value) || 300,
    main_pc_only: ($('ig-meta-mpc')?.value === 'true'),
  };
  const btn = $('ig-preset-save');
  if (btn) btn.disabled = true;
  try {
    await api('/api/image/workflows', { method: 'POST', body });
    toast(edit ? '更新しました' : '登録しました', 'success');
    closePresetModal();
    await Promise.all([loadPresets(), loadWorkflows()]);
  } catch (err) {
    toast(`保存失敗: ${err?.message || err}`, 'error');
    if (btn) btn.disabled = false;
  }
}

// ============================================================
// Mount / Show / Hide
// ============================================================
export async function mount() {
  loadSectionExpandedLS();

  $('ig-submit')?.addEventListener('click', handleSubmit);
  $('ig-preset-new')?.addEventListener('click', () => openPresetModal({}));
  $('ig-sec-new')?.addEventListener('click', () => openSectionModal({}));
  $('ig-sec-reload')?.addEventListener('click', loadSections);
  $('ig-sec-expand-all')?.addEventListener('click', () => setAllCategoriesExpanded(true));
  $('ig-sec-collapse-all')?.addEventListener('click', () => setAllCategoriesExpanded(false));
  $('ig-prompt-crafter')?.addEventListener('click', handlePromptCrafterClick);
  $('ig-preview-show')?.addEventListener('click', runPreview);
  $('ig-workflow')?.addEventListener('change', handleWorkflowChange);

  $('ig-secpreset-load')?.addEventListener('click', handleSectionPresetLoad);
  $('ig-secpreset-save')?.addEventListener('click', handleSectionPresetSave);
  $('ig-secpreset-overwrite')?.addEventListener('click', handleSectionPresetOverwrite);
  $('ig-secpreset-delete')?.addEventListener('click', handleSectionPresetDelete);

  await Promise.all([
    loadWorkflows(),
    loadSections(),
    loadComfyPanel(),
    loadPresets(),
    loadSectionPresets(),
  ]);
  // workflows ロード後に初期 LoRA も読み込む
  await loadLoras();
}

export async function onShow() {
  // ?prefill=... 付きで遷移してきた場合のみ stash 取り込み
  await checkStashPrefill();
  // ComfyUI ステータスは表示中のみポーリング
  if (!comfyStatusTimer) {
    refreshComfyStatus();
    comfyStatusTimer = setInterval(refreshComfyStatus, 15000);
  }
}

export function onHide() {
  if (comfyStatusTimer) { clearInterval(comfyStatusTimer); comfyStatusTimer = null; }
}
