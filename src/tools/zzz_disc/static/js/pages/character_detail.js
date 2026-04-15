/** キャラ詳細 — 現在の装備 + プリセット装備カード */
import { api } from '../api.js';
import { escapeHtml, toast, confirmDialog, promptDialog, openModal } from '../app.js';
import { renderBuildCard } from '../components/build_card.js';
import { statLabel, formatStatValue } from '../labels.js';

let state = { character: null, current: null, presets: [] };

export function render(params) {
  return `
    <div class="page-header">
      <a href="#/characters" class="btn btn-sm btn-ghost">← 一覧</a>
      <h2 id="char-title">キャラ詳細 — ${escapeHtml(params.slug)}</h2>
      <button class="btn btn-sm" id="sync-btn">↻ HoYoLAB 同期</button>
    </div>
    <div id="detail-body"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount(params) {
  const slug = params.slug;
  document.getElementById('sync-btn').addEventListener('click', () => syncCharacter(slug));
  await load(slug);
}

async function load(slug) {
  const el = document.getElementById('detail-body');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    // キャラ ID を解決するため、一覧から slug → id を引く
    const listRes = await api('/characters');
    const list = Array.isArray(listRes) ? listRes : (listRes?.characters || []);
    const ch = list.find(c => c.slug === slug);
    if (!ch) throw new Error(`キャラ "${slug}" が見つかりません`);
    document.getElementById('char-title').textContent = ch.name_ja;

    const data = await api(`/characters/${ch.id}/builds`);
    state.character = data.character || ch;
    state.current = data.current || null;
    state.presets = data.presets || [];
    renderBody();
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

const SUB_STAT_CANDIDATES = [
  'HP', 'HP%', '攻撃力', '攻撃力%', '防御力', '防御力%',
  '会心率%', '会心ダメージ%', '異常マスタリー', '貫通値', '貫通率%',
];

function renderRecommendedEditor() {
  const recommended = new Set(state.character?.recommended_substats || []);
  const boxes = SUB_STAT_CANDIDATES.map(name => `
    <label class="rec-sub-chip ${recommended.has(name) ? 'on' : ''}">
      <input type="checkbox" data-sub="${escapeHtml(name)}" ${recommended.has(name) ? 'checked' : ''} />
      <span>${escapeHtml(name)}</span>
    </label>
  `).join('');
  return `
    <div class="recommended-editor">
      <h3 class="mb-1">★ 推奨サブステ <span class="text-muted text-sm" id="rec-sub-status"></span></h3>
      <div class="text-muted text-sm mb-1">チェックを入れると即時保存されます。選択したサブステは各ディスクで強調表示されます</div>
      <div class="rec-sub-chips">${boxes}</div>
    </div>
  `;
}

let _recSaveTimer = null;
let _recSaveSeq = 0;

function wireRecommendedEditor() {
  const wrap = document.querySelector('.recommended-editor');
  if (!wrap) return;
  const status = wrap.querySelector('#rec-sub-status');

  const doSave = async () => {
    const picked = Array.from(wrap.querySelectorAll('input[type="checkbox"]:checked'))
      .map(cb => cb.dataset.sub);
    const seq = ++_recSaveSeq;
    if (status) status.textContent = '保存中…';
    try {
      const res = await api(`/characters/${state.character.id}/recommended-substats`, {
        method: 'PUT', body: { stats: picked },
      });
      if (seq !== _recSaveSeq) return;
      state.character = res.character || { ...state.character, recommended_substats: picked };
      if (status) status.textContent = '✓ 保存済み';
      setTimeout(() => { if (status && seq === _recSaveSeq) status.textContent = ''; }, 1500);
    } catch (err) {
      if (seq !== _recSaveSeq) return;
      if (status) status.textContent = '';
      toast(`保存失敗: ${err.message}`, 'error');
    }
  };

  wrap.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
      clearTimeout(_recSaveTimer);
      _recSaveTimer = setTimeout(doSave, 250);
    });
  });
}

function renderBody() {
  const el = document.getElementById('detail-body');
  const chunks = [];

  chunks.push(renderRecommendedEditor());

  // 現在の装備
  chunks.push('<h3 class="mb-1">● 現在の装備</h3>');
  if (state.current) {
    chunks.push(`<div class="build-wrap" data-kind="current">${renderBuildCard({ character: state.character, build: state.current, actions: ['clone'] })}</div>`);
  } else {
    chunks.push(`
      <div class="placeholder" style="padding:24px;">
        <div class="big-icon">📡</div>
        <div>現在の装備はまだ同期されていません</div>
        <div class="text-muted text-sm mt-1">HoYoLAB 設定で cookie を登録後、「同期」ボタンで取得できます</div>
      </div>
    `);
  }

  // プリセット装備
  chunks.push(`<h3 class="mb-1 mt-2">📦 プリセット装備 (${state.presets.length})</h3>`);
  if (!state.presets.length) {
    chunks.push('<div class="text-muted text-sm mb-2">プリセットはまだありません。「現在の装備」から「プリセットへ複製」で保存できます。</div>');
  } else {
    for (const b of state.presets) {
      chunks.push(`<div class="build-wrap" data-kind="preset">${renderBuildCard({ character: state.character, build: b, actions: ['edit', 'delete'] })}</div>`);
    }
  }

  el.innerHTML = chunks.join('');
  wireRecommendedEditor();
  wireUp();
}

function wireUp() {
  document.querySelectorAll('.build-card').forEach(card => {
    const buildId = Number(card.dataset.buildId);
    card.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const act = btn.dataset.act;
        if (act === 'clone') cloneBuild(buildId);
        else if (act === 'edit') editBuild(buildId);
        else if (act === 'delete') deleteBuild(buildId);
      });
    });
    // disc-tile クリックでスワップモーダル
    const allTiles = card.querySelectorAll('.disc-tile');
    allTiles.forEach(tile => {
      tile.addEventListener('click', (e) => {
        e.stopPropagation();
        const slot = Number(tile.dataset.slot);
        if (!slot) return;
        const currentDiscId = tile.dataset.discId ? Number(tile.dataset.discId) : null;
        openSwapModal({ buildId, slot, currentDiscId });
      });
      tile.style.cursor = 'pointer';
    });
  });
}

async function openSwapModal({ buildId, slot, currentDiscId }) {
  const isCurrent = state.current && state.current.id === buildId;
  const build = isCurrent
    ? state.current
    : state.presets.find(p => p.id === buildId);
  if (!build) {
    toast('ビルドが見つかりません', 'error');
    return;
  }

  const recommended = state.character?.recommended_substats || [];
  const usedDiscIds = new Set(
    (build.slots || []).map(s => s?.disc?.id).filter(Boolean)
  );

  const { bodyEl, footerEl, close } = openModal({
    title: `スロット ${slot} のディスクを差し替え`,
    body: `<div class="placeholder"><div class="spinner"></div></div>`,
  });
  footerEl.innerHTML = `
    ${currentDiscId ? '<button class="btn" data-act="unequip">外す</button>' : ''}
    <button class="btn" data-act="cancel">キャンセル</button>
  `;
  footerEl.querySelector('[data-act="cancel"]').addEventListener('click', close);
  footerEl.querySelector('[data-act="unequip"]')?.addEventListener('click', async () => {
    if (!await confirmDialog('このスロットを未装備にします。よろしいですか？')) return;
    await applySwap({ buildId, slot, discId: null, isCurrent, close });
  });

  let discs = [];
  let usage = [];
  try {
    const [dRes, uRes] = await Promise.all([
      api('/discs', { params: { slot } }),
      api('/disc-usage'),
    ]);
    discs = dRes?.discs || [];
    usage = uRes?.usage || [];
  } catch (err) {
    bodyEl.innerHTML = `<div class="text-muted">候補の取得に失敗: ${escapeHtml(err.message)}</div>`;
    return;
  }

  const usageByDisc = new Map();
  for (const u of usage) {
    if (!usageByDisc.has(u.disc_id)) usageByDisc.set(u.disc_id, []);
    usageByDisc.get(u.disc_id).push(u);
  }

  const scored = discs.map(d => {
    const subs = Array.isArray(d.sub_stats) ? d.sub_stats : [];
    const matchedSubs = subs.filter(s => recommended.includes(s.name));
    const subScore = matchedSubs.reduce(
      (acc, s) => acc + 2 + Number(s.upgrades || 0) * 0.5,
      0,
    );
    const mainBonus = recommended.includes(d.main_stat_name) ? 3 : 0;
    return {
      disc: d,
      score: subScore + mainBonus + Number(d.level || 0) * 0.1,
      matchedSubs,
      mainMatch: !!mainBonus,
    };
  });
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return (b.disc.level || 0) - (a.disc.level || 0);
  });

  bodyEl.innerHTML = `
    ${isCurrent ? `
      <div class="alert alert-warning text-sm mb-1">
        ⚠ 「現在の装備」を変更すると HoYoLAB 同期で上書きされます。残したい構成は「プリセットへ複製」してから編集してください。
      </div>` : ''}
    <div class="text-muted text-sm mb-1">候補 ${scored.length} 件 / 推奨サブステ: ${
      recommended.length ? recommended.map(escapeHtml).join(', ') : '<em>未設定</em>'
    }</div>
    <div class="swap-list">
      ${scored.length === 0
        ? '<div class="text-muted">スロット ' + slot + ' のディスクがありません</div>'
        : scored.map(({ disc: d, score, matchedSubs, mainMatch }) => {
            const inUse = usageByDisc.get(d.id) || [];
            const inUseHere = usedDiscIds.has(d.id);
            const isSelected = currentDiscId === d.id;
            const subs = Array.isArray(d.sub_stats) ? d.sub_stats : [];
            return `
              <div class="swap-row ${isSelected ? 'selected' : ''}" data-disc-id="${d.id}">
                <div class="swap-row-head">
                  <span class="swap-set">${escapeHtml(d.set_name_ja || d.name || '-')}</span>
                  <span class="swap-level">${d.level != null ? `Lv.${d.level}` : ''}</span>
                  <span class="swap-score">★ ${score.toFixed(1)}</span>
                  ${isSelected ? '<span class="badge badge-current">現在のスロット</span>' : ''}
                  ${inUseHere && !isSelected ? '<span class="badge badge-warn">同ビルドの他スロット</span>' : ''}
                </div>
                <div class="swap-main ${mainMatch ? 'recommended' : ''}">
                  ${escapeHtml(statLabel(d.main_stat_name))}
                  <strong>${escapeHtml(formatStatValue(d.main_stat_name, d.main_stat_value))}</strong>
                </div>
                <div class="swap-subs">
                  ${subs.map(s => `
                    <span class="swap-sub ${recommended.includes(s.name) ? 'recommended' : ''}">
                      ${escapeHtml(s.name)} ${escapeHtml(formatStatValue(s.name, s.value))}${
                        Number(s.upgrades || 0) > 0 ? ` <small>+${s.upgrades}</small>` : ''
                      }
                    </span>
                  `).join('')}
                </div>
                ${inUse.length ? `
                  <div class="swap-usage text-xs text-muted">
                    使用中: ${inUse.map(u =>
                      `${escapeHtml(u.character_name_ja || '-')}${u.is_current ? ' ★' : ` / ${escapeHtml(u.build_name || '')}`}`
                    ).join(' , ')}
                  </div>` : ''}
              </div>
            `;
          }).join('')
      }
    </div>
  `;

  bodyEl.querySelectorAll('.swap-row').forEach(row => {
    row.addEventListener('click', async () => {
      const newId = Number(row.dataset.discId);
      if (newId === currentDiscId) { close(); return; }
      await applySwap({ buildId, slot, discId: newId, isCurrent, close });
    });
  });
}

async function applySwap({ buildId, slot, discId, isCurrent, close }) {
  try {
    await api(`/builds/${buildId}/slots/${slot}`, {
      method: 'PUT', body: { disc_id: discId },
    });
    toast(discId == null ? 'スロットを外しました' : 'ディスクを差し替えました', 'success');
    close();
    await load(state.character.slug);
  } catch (err) {
    toast(`差し替え失敗: ${err.message}`, 'error');
  }
}

async function cloneBuild(buildId) {
  const name = await promptDialog({
    title: 'プリセット名',
    label: 'このビルドをプリセットとして複製します',
    value: `${state.character?.name_ja || ''} プリセット ${state.presets.length + 1}`,
  });
  if (name == null) return;
  try {
    await api('/builds', { method: 'POST', body: { source_build_id: buildId, name } });
    toast('プリセットに複製しました', 'success');
    await load(state.character.slug);
  } catch (err) {
    toast(`複製失敗: ${err.message}`, 'error');
  }
}

async function editBuild(buildId) {
  const build = state.presets.find(p => p.id === buildId);
  if (!build) return;
  const name = await promptDialog({
    title: 'ビルド名を編集',
    label: 'ビルド名',
    value: build.name || '',
  });
  if (name == null) return;
  const tag = await promptDialog({
    title: 'タグを編集',
    label: 'タグ（任意）',
    value: build.tag || '',
  });
  if (tag == null) return;
  const rank = await promptDialog({
    title: 'ランクを編集',
    label: 'ランク（S/A/B/C、空で解除）',
    value: build.rank || '',
  });
  if (rank == null) return;
  try {
    await api(`/builds/${buildId}`, { method: 'PUT', body: { name, tag: tag || null, rank: rank || null } });
    toast('保存しました', 'success');
    await load(state.character.slug);
  } catch (err) {
    toast(`保存失敗: ${err.message}`, 'error');
  }
}

async function deleteBuild(buildId) {
  const ok = await confirmDialog(`プリセット #${buildId} を削除します。よろしいですか？`);
  if (!ok) return;
  try {
    await api(`/builds/${buildId}`, { method: 'DELETE' });
    toast('削除しました', 'info');
    await load(state.character.slug);
  } catch (err) {
    toast(`削除失敗: ${err.message}`, 'error');
  }
}

async function syncCharacter(slug) {
  if (!state.character?.id) {
    // 未ロード時は id 解決してから同期
    const listRes = await api('/characters');
    const list = Array.isArray(listRes) ? listRes : (listRes?.characters || []);
    const ch = list.find(c => c.slug === slug);
    if (ch) state.character = ch;
  }
  if (!state.character?.id) {
    toast('キャラ ID を解決できませんでした', 'error');
    return;
  }
  const btn = document.getElementById('sync-btn');
  btn.disabled = true;
  btn.textContent = '同期中...';
  try {
    await api(`/hoyolab/sync/${state.character.id}`, { method: 'POST' });
    toast('同期しました', 'success');
    await load(slug);
  } catch (err) {
    toast(`同期失敗: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '↻ HoYoLAB 同期';
  }
}

export function unmount() {
  state = { character: null, current: null, presets: [] };
}
