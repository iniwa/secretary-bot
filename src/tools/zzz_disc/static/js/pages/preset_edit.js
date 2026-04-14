/** 1キャラの6部位プリセット編集 */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';

let state = { character: null, sets: [], presets: {} };

const SLOT_LABEL = { 1: '1（攻撃）', 2: '2（生命）', 3: '3（防御）', 4: '4（主）', 5: '5（副）', 6: '6（特）' };
const MAIN_STAT_OPTIONS = [
  'attack_flat', 'hp_flat', 'def_flat',
  'attack_pct', 'hp_pct', 'def_pct',
  'crit_rate', 'crit_dmg',
  'anomaly_proficiency', 'anomaly_mastery',
  'pen_ratio', 'energy_regen',
  'physical_dmg', 'fire_dmg', 'ice_dmg', 'electric_dmg', 'ether_dmg',
];
const SUB_STAT_OPTIONS = MAIN_STAT_OPTIONS.filter(s => !s.endsWith('_dmg') && s !== 'energy_regen' && s !== 'anomaly_mastery');

export function render(params) {
  return `
    <div class="page-header">
      <a href="#/presets" class="btn btn-sm btn-ghost">← 一覧</a>
      <h2 id="preset-title">プリセット編集 — ${escapeHtml(params.slug)}</h2>
      <button class="btn btn-primary" id="save-all">全て保存</button>
    </div>
    <div id="preset-body"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount(params) {
  const slug = params.slug;
  try {
    const masters = await api('/masters');
    state.sets = masters?.sets || [];
    const chars = masters?.characters || [];
    state.character = chars.find(c => c.slug === slug);
    if (!state.character) throw new Error(`キャラ "${slug}" が見つかりません`);
    document.getElementById('preset-title').textContent = `プリセット編集 — ${state.character.name_ja}`;

    const preset = await api(`/presets/${state.character.id}`);
    const entries = Array.isArray(preset) ? preset : (preset?.presets || []);
    state.presets = {};
    for (const e of entries) {
      if (e?.slot) state.presets[e.slot] = e;
    }
    renderBody();
  } catch (err) {
    document.getElementById('preset-body').innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
  document.getElementById('save-all').addEventListener('click', saveAll);

  // 部位ごとの保存ボタン（delegated）
  const body = document.getElementById('preset-body');
  body.addEventListener('click', onSlotSaveClick);
}

function onSlotSaveClick(e) {
  const btn = e.target.closest('[data-save-slot]');
  if (btn) saveSlot(Number(btn.dataset.saveSlot));
}

export function unmount() {
  state = { character: null, sets: [], presets: {} };
}

function renderBody() {
  const el = document.getElementById('preset-body');
  el.innerHTML = [1,2,3,4,5,6].map(slot => slotCardHtml(slot, state.presets[slot] || {})).join('');
}

function parseJSON(s) {
  if (!s) return null;
  if (typeof s === 'object') return s;
  try { return JSON.parse(s); } catch { return null; }
}

function slotCardHtml(slot, p) {
  const preferredSetIds = parseJSON(p.preferred_set_ids_json) || [];
  const preferredMains = parseJSON(p.preferred_main_stats_json) || [];
  const subPriority = parseJSON(p.sub_stat_priority_json) || [];
  const subMap = new Map(subPriority.map(s => [s.name, s.weight]));

  return `
    <div class="card" data-slot="${slot}">
      <h3>部位 ${SLOT_LABEL[slot]}</h3>
      <div class="form-grid mt-1">
        <label>優先セット（複数可）</label>
        <select multiple size="4" name="preferred_set_ids">
          ${state.sets.map(s => `<option value="${s.id}" ${preferredSetIds.includes(s.id) ? 'selected' : ''}>${escapeHtml(s.name_ja)}</option>`).join('')}
        </select>

        <label>優先メインステ（複数可）</label>
        <select multiple size="4" name="preferred_main_stats">
          ${MAIN_STAT_OPTIONS.map(m => `<option value="${m}" ${preferredMains.includes(m) ? 'selected' : ''}>${m}</option>`).join('')}
        </select>
      </div>

      <div class="mt-2"><strong>サブステ優先度（ウェイト 1–5、0は不要）</strong></div>
      <div class="form-grid mt-1">
        ${SUB_STAT_OPTIONS.map(name => `
          <label>${name}</label>
          <input type="number" min="0" max="5" name="sub_${name}" value="${subMap.get(name) ?? 0}" style="width:80px;" />
        `).join('')}
      </div>

      <div class="mt-2 row">
        <div class="flex-1"></div>
        <button class="btn btn-sm" data-save-slot="${slot}">この部位のみ保存</button>
      </div>
    </div>
  `;
}

function readSlot(slot) {
  const card = document.querySelector(`[data-slot="${slot}"]`);
  if (!card) return null;
  const setSel = card.querySelector('[name="preferred_set_ids"]');
  const mainSel = card.querySelector('[name="preferred_main_stats"]');
  const preferred_set_ids = Array.from(setSel.selectedOptions).map(o => Number(o.value));
  const preferred_main_stats = Array.from(mainSel.selectedOptions).map(o => o.value);
  const sub_stat_priority = [];
  for (const name of SUB_STAT_OPTIONS) {
    const v = parseInt(card.querySelector(`[name="sub_${name}"]`).value, 10) || 0;
    if (v > 0) sub_stat_priority.push({ name, weight: v });
  }
  return {
    slot,
    preferred_set_ids,
    preferred_main_stats,
    sub_stat_priority,
  };
}

async function saveSlot(slot) {
  const payload = readSlot(slot);
  if (!payload) return;
  try {
    await api(`/presets/${state.character.id}/${slot}`, { method: 'PUT', body: payload });
    toast(`部位 ${slot} を保存しました`, 'success');
  } catch (err) {
    toast(`保存失敗（部位 ${slot}）: ${err.message}`, 'error');
  }
}

async function saveAll() {
  const results = await Promise.all([1,2,3,4,5,6].map(async (slot) => {
    try {
      await api(`/presets/${state.character.id}/${slot}`, { method: 'PUT', body: readSlot(slot) });
      return true;
    } catch (err) {
      console.error(err);
      return false;
    }
  }));
  const ok = results.filter(Boolean).length;
  const ng = 6 - ok;
  toast(`保存 ${ok}件 / 失敗 ${ng}件`, ng ? 'warning' : 'success');
}

