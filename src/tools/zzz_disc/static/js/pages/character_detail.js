/** キャラ詳細 — 現在の装備 + プリセット装備カード */
import { api } from '../api.js';
import { escapeHtml, toast, confirmDialog, promptDialog } from '../app.js';
import { renderBuildCard } from '../components/build_card.js';

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
      <h3 class="mb-1">★ 推奨サブステ</h3>
      <div class="text-muted text-sm mb-1">ここで選択したサブステは、各ディスクで目立つようハイライトされます</div>
      <div class="rec-sub-chips">${boxes}</div>
      <div class="mt-1"><button class="btn btn-sm btn-primary" id="save-recommended">保存</button></div>
    </div>
  `;
}

function wireRecommendedEditor() {
  const wrap = document.querySelector('.recommended-editor');
  if (!wrap) return;
  wrap.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
    });
  });
  const btn = document.getElementById('save-recommended');
  btn?.addEventListener('click', async () => {
    const picked = Array.from(wrap.querySelectorAll('input[type="checkbox"]:checked'))
      .map(cb => cb.dataset.sub);
    btn.disabled = true;
    try {
      const res = await api(`/characters/${state.character.id}/recommended-substats`, {
        method: 'PUT', body: { stats: picked },
      });
      state.character = res.character || { ...state.character, recommended_substats: picked };
      toast('推奨サブステを保存しました', 'success');
      renderBody();
    } catch (err) {
      toast(`保存失敗: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
    }
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
    // disc-tile クリックで詳細へ
    card.querySelectorAll('.disc-tile[data-disc-id]').forEach(tile => {
      tile.addEventListener('click', () => {
        const id = tile.dataset.discId;
        if (id) location.hash = `#/discs/${id}`;
      });
    });
  });
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
