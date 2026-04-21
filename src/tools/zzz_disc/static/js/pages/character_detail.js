/** キャラ詳細 — 現在の装備 + プリセット装備カード */
import { api } from '../api.js';
import { escapeHtml, toast, confirmDialog, promptDialog, openModal } from '../app.js';
import { renderBuildCard } from '../components/build_card.js';
import { statLabel, formatStatValue, setsByName, setNameWithPopover } from '../labels.js';

let state = { character: null, current: null, presets: [], sets: [] };

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

    const [data, setsRes] = await Promise.all([
      api(`/characters/${ch.id}/builds`),
      state.sets.length ? Promise.resolve({ sets: state.sets }) : api('/sets'),
    ]);
    state.character = data.character || ch;
    state.current = data.current || null;
    state.presets = data.presets || [];
    state.sets = setsRes?.sets || state.sets;
    renderBody();
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

const SUB_STAT_CANDIDATES = [
  'HP', 'HP%', '攻撃力', '攻撃力%', '防御力', '防御力%',
  '会心率%', '会心ダメージ%', '異常マスタリー', '貫通値', '貫通率%',
];

// 変動メインステスロット（4/5/6）の候補。内部 disc.slot と一致。
// UI 上は「5 番 / 6 番 / 7 番ディスク」という慣用表記で表示する。
const MAIN_STAT_SLOTS = [
  { key: '4', label: '4号位', candidates:
    ['HP%', '攻撃力%', '防御力%', '会心率%', '会心ダメージ%', '異常掌握'] },
  { key: '5', label: '5号位', candidates:
    ['HP%', '攻撃力%', '防御力%', '貫通率%',
     '物理属性ダメージ%', '炎属性ダメージ%', '氷属性ダメージ%',
     '電気属性ダメージ%', 'エーテル属性ダメージ%'] },
  { key: '6', label: '6号位', candidates:
    ['HP%', '攻撃力%', '防御力%', '異常マスタリー', '異常掌握',
     '衝撃力%', 'エネルギー自動回復%'] },
];

function renderRecommendedEditor() {
  const recommended = new Set(state.character?.recommended_substats || []);
  const boxes = SUB_STAT_CANDIDATES.map(name => `
    <label class="rec-sub-chip ${recommended.has(name) ? 'on' : ''}">
      <input type="checkbox" data-val="${escapeHtml(name)}" ${recommended.has(name) ? 'checked' : ''} />
      <span>${escapeHtml(name)}</span>
    </label>
  `).join('');
  return `
    <div class="recommended-editor" data-rec-kind="substats">
      <h3 class="mb-1">★ 推奨サブステ <span class="text-muted text-sm rec-status"></span></h3>
      <div class="text-muted text-sm mb-1">チェックで即時保存。選択したサブステは各ディスクで強調表示されます</div>
      <div class="rec-sub-chips">${boxes}</div>
    </div>
  `;
}

function renderRecommendedDiscEditor(setsMap) {
  const recommended = new Set(state.character?.recommended_disc_sets || []);
  const map = setsMap || setsByName(state.sets);
  // セット名は同期で増えるので state.sets の name_ja を一覧化
  const names = (state.sets || [])
    .map(s => s.name_ja)
    .filter(Boolean)
    .sort((a, b) => new Intl.Collator('ja').compare(a, b));
  const boxes = names.map(name => `
    <label class="rec-sub-chip with-popover ${recommended.has(name) ? 'on' : ''}">
      <input type="checkbox" data-val="${escapeHtml(name)}" ${recommended.has(name) ? 'checked' : ''} />
      ${setNameWithPopover(name, map.get(name))}
    </label>
  `).join('');
  return `
    <div class="recommended-editor" data-rec-kind="disc_sets">
      <h3 class="mb-1">💿 推奨ディスク（セット） <span class="text-muted text-sm rec-status"></span></h3>
      <div class="text-muted text-sm mb-1">チェックで即時保存。スワップモーダルの初期フィルタに反映されます</div>
      <div class="rec-sub-chips">${boxes}</div>
    </div>
  `;
}

const REC_ENDPOINTS = {
  substats: { path: 'recommended-substats', body: 'stats', stateKey: 'recommended_substats' },
  disc_sets: { path: 'recommended-disc-sets', body: 'sets', stateKey: 'recommended_disc_sets' },
};

const _recSaveTimers = {};
const _recSaveSeq = {};

function wireRecommendedEditors() {
  document.querySelectorAll('.recommended-editor').forEach(wrap => {
    const kind = wrap.dataset.recKind;
    const cfg = REC_ENDPOINTS[kind];
    if (!cfg) return;
    const status = wrap.querySelector('.rec-status');

    const doSave = async () => {
      const picked = Array.from(wrap.querySelectorAll('input[type="checkbox"]:checked'))
        .map(cb => cb.dataset.val);
      _recSaveSeq[kind] = (_recSaveSeq[kind] || 0) + 1;
      const seq = _recSaveSeq[kind];
      if (status) status.textContent = '保存中…';
      try {
        const res = await api(`/characters/${state.character.id}/${cfg.path}`, {
          method: 'PUT', body: { [cfg.body]: picked },
        });
        if (seq !== _recSaveSeq[kind]) return;
        state.character = res.character || { ...state.character, [cfg.stateKey]: picked };
        if (status) status.textContent = '✓ 保存済み';
        setTimeout(() => { if (status && seq === _recSaveSeq[kind]) status.textContent = ''; }, 1500);
      } catch (err) {
        if (seq !== _recSaveSeq[kind]) return;
        if (status) status.textContent = '';
        toast(`保存失敗: ${err.message}`, 'error');
      }
    };

    wrap.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
        // 楽観的に state を更新し、ビルドカードを即時再描画
        const picked = Array.from(wrap.querySelectorAll('input[type="checkbox"]:checked'))
          .map(x => x.dataset.val);
        if (state.character) {
          state.character = { ...state.character, [cfg.stateKey]: picked };
        }
        renderBuildsSection();
        clearTimeout(_recSaveTimers[kind]);
        _recSaveTimers[kind] = setTimeout(doSave, 250);
      });
    });
  });
}

function renderBody() {
  const el = document.getElementById('detail-body');
  el.innerHTML = `
    <div id="rec-editors-area"></div>
    <div id="rec-main-stats-area"></div>
    <div id="rec-notes-area"></div>
    <div id="rec-team-notes-area"></div>
    <div id="skills-area"></div>
    <div id="builds-area"></div>
  `;
  renderRecEditorsSection();
  renderRecMainStatsSection();
  renderRecNotesSection();
  renderRecTeamNotesSection();
  renderSkillsSection();
  renderBuildsSection();
}

function renderRecNotesSection() {
  const el = document.getElementById('rec-notes-area');
  if (!el) return;
  const notes = state.character?.recommended_notes || '';
  el.innerHTML = `
    <div class="skills-block">
      <div class="skills-head">
        <h3 class="mb-1">📝 オススメステータス / ディスク（メモ）</h3>
        <button class="btn btn-sm" id="rec-notes-edit-btn">編集</button>
      </div>
      ${notes
        ? `<div class="skill-summary">${escapeHtml(notes)}</div>`
        : '<div class="text-muted text-sm">ネットから拾ってきた推奨ステ・ディスクをフリーテキストで残せます。（未設定）</div>'}
    </div>
  `;
  document.getElementById('rec-notes-edit-btn').addEventListener('click', openRecNotesEditor);
}

function openRecNotesEditor() {
  const ch = state.character;
  const wrap = document.createElement('div');
  wrap.innerHTML = `
    <div class="text-muted text-sm mb-1">
      ネット記事や攻略サイトから集めた「上げるべきステータス」「推奨ディスク」等を自由に記入。<br>
      既存の推奨サブステ／ディスクのチェックには影響しません（表示専用）。
    </div>
    <textarea id="rec-notes-text" rows="6" style="width:100%;"
      placeholder="例: 異常マスタリーを最優先。ディスクは○○4セット推奨。&#10;ATK 3,500 以上で××バフ最大化など。">${escapeHtml(ch?.recommended_notes || '')}</textarea>
  `;
  const { footerEl, close } = openModal({ title: 'オススメステータス（メモ）編集', body: wrap });
  footerEl.innerHTML = `
    <button class="btn" data-act="cancel">キャンセル</button>
    <button class="btn btn-primary" data-act="ok">保存</button>
  `;
  footerEl.querySelector('[data-act="cancel"]').addEventListener('click', close);
  footerEl.querySelector('[data-act="ok"]').addEventListener('click', async () => {
    const notes = wrap.querySelector('#rec-notes-text').value;
    try {
      const res = await api(`/characters/${ch.id}/recommended-notes`, {
        method: 'PUT', body: { notes: notes || null },
      });
      state.character = res.character || state.character;
      close();
      toast('保存しました', 'success');
      renderRecNotesSection();
    } catch (err) {
      toast(err.message || String(err), 'error');
    }
  });
}

function renderRecTeamNotesSection() {
  const el = document.getElementById('rec-team-notes-area');
  if (!el) return;
  const notes = state.character?.recommended_team_notes || '';
  el.innerHTML = `
    <div class="skills-block">
      <div class="skills-head">
        <h3 class="mb-1">🧩 おすすめ編成（メモ）</h3>
        <button class="btn btn-sm" id="rec-team-notes-edit-btn">編集</button>
      </div>
      ${notes
        ? `<div class="skill-summary">${escapeHtml(notes)}</div>`
        : '<div class="text-muted text-sm">おすすめ編成やシナジーをフリーテキストで残せます。（未設定）</div>'}
    </div>
  `;
  document.getElementById('rec-team-notes-edit-btn').addEventListener('click', openRecTeamNotesEditor);
}

function openRecTeamNotesEditor() {
  const ch = state.character;
  const wrap = document.createElement('div');
  wrap.innerHTML = `
    <div class="text-muted text-sm mb-1">
      おすすめ編成・シナジー・カウンター例などを自由に記入。<br>
      「コーデックスから取り込み」を押すと <code>docs/zzz_character_codex.md</code> の「編成例」セクションをそのまま貼り付けます。
    </div>
    <div class="flex-between mb-1">
      <button class="btn btn-sm" id="rec-team-notes-import-btn" type="button">📖 コーデックスから取り込み</button>
      <span class="text-muted text-sm rec-team-notes-import-status"></span>
    </div>
    <textarea id="rec-team-notes-text" rows="8" style="width:100%;"
      placeholder="例: 妄想エンジェル編成（千夏 / アリア / 南宮羽）&#10;代替: 強攻編成（千夏 / 葉瞬光 / ダイアリン）など">${escapeHtml(ch?.recommended_team_notes || '')}</textarea>
  `;
  const { footerEl, close } = openModal({ title: 'おすすめ編成（メモ）編集', body: wrap });
  footerEl.innerHTML = `
    <button class="btn" data-act="cancel">キャンセル</button>
    <button class="btn btn-primary" data-act="ok">保存</button>
  `;

  const textarea = wrap.querySelector('#rec-team-notes-text');
  const importBtn = wrap.querySelector('#rec-team-notes-import-btn');
  const importStatus = wrap.querySelector('.rec-team-notes-import-status');

  importBtn.addEventListener('click', async () => {
    const existing = (textarea.value || '').trim();
    if (existing) {
      const ok = await confirmDialog(
        '既存のメモをコーデックスの「編成例」で上書きします。よろしいですか？'
      );
      if (!ok) return;
    }
    importBtn.disabled = true;
    if (importStatus) importStatus.textContent = '取得中…';
    try {
      const res = await api(`/characters/${ch.id}/codex/teams`);
      if (!res?.found || !res?.text) {
        if (importStatus) importStatus.textContent = '';
        toast('コーデックスに「編成例」セクションが見つかりません', 'warning');
        return;
      }
      textarea.value = res.text;
      if (importStatus) importStatus.textContent = '✓ 取り込み完了（保存で確定）';
    } catch (err) {
      if (importStatus) importStatus.textContent = '';
      toast(`取り込み失敗: ${err.message || err}`, 'error');
    } finally {
      importBtn.disabled = false;
    }
  });

  footerEl.querySelector('[data-act="cancel"]').addEventListener('click', close);
  footerEl.querySelector('[data-act="ok"]').addEventListener('click', async () => {
    const notes = textarea.value;
    try {
      const res = await api(`/characters/${ch.id}/recommended-team-notes`, {
        method: 'PUT', body: { notes: notes || null },
      });
      state.character = res.character || state.character;
      close();
      toast('保存しました', 'success');
      renderRecTeamNotesSection();
    } catch (err) {
      toast(err.message || String(err), 'error');
    }
  });
}

const SKILL_KINDS = ['通常攻撃', '回避', '支援', '特殊攻撃', '連携攻撃', 'コアスキル', 'その他'];

function renderSkillsSection() {
  const el = document.getElementById('skills-area');
  if (!el) return;
  const skills = state.character?.skills || [];
  const summary = state.character?.skill_summary || '';
  const listHtml = skills.length
    ? skills.map(s => `
        <details class="skill-item">
          <summary>
            ${s.kind ? `<span class="skill-kind">[${escapeHtml(s.kind)}]</span>` : ''}
            <strong>${escapeHtml(s.name || '-')}</strong>
          </summary>
          <div class="skill-desc">${escapeHtml(s.description || '')}</div>
        </details>`).join('')
    : '<div class="text-muted text-sm">スキル情報は未登録です。</div>';
  el.innerHTML = `
    <div class="skills-block">
      <div class="skills-head">
        <h3 class="mb-1">📘 スキル / 要約</h3>
        <button class="btn btn-sm" id="skills-edit-btn">編集</button>
      </div>
      ${summary ? `<div class="skill-summary">${escapeHtml(summary)}</div>` : ''}
      <div class="skills-list">${listHtml}</div>
    </div>
  `;
  document.getElementById('skills-edit-btn').addEventListener('click', openSkillsEditor);
}

function openSkillsEditor() {
  const ch = state.character;
  const skills = (ch?.skills || []).map(s => ({ ...s }));
  const wrap = document.createElement('div');
  wrap.innerHTML = `
    <label class="text-secondary text-sm">要約（自由記入・キャラ運用メモ）</label>
    <textarea id="sk-summary" rows="3" style="width:100%;margin:4px 0 12px;">${escapeHtml(ch?.skill_summary || '')}</textarea>
    <div class="flex-between mb-1">
      <strong>スキル一覧</strong>
      <button class="btn btn-sm" id="sk-add">＋ スキル追加</button>
    </div>
    <div id="sk-rows" style="display:flex;flex-direction:column;gap:6px;"></div>
  `;
  const { footerEl, close } = openModal({ title: 'スキル編集', body: wrap });
  footerEl.innerHTML = `
    <button class="btn" data-act="cancel">キャンセル</button>
    <button class="btn btn-primary" data-act="ok">保存</button>
  `;
  const rowsEl = wrap.querySelector('#sk-rows');

  function rowHtml(idx, s) {
    const kindOpts = ['', ...SKILL_KINDS].map(k =>
      `<option value="${escapeHtml(k)}" ${s.kind === k ? 'selected' : ''}>${escapeHtml(k || '（種類）')}</option>`
    ).join('');
    return `
      <div class="sk-row" data-idx="${idx}" style="border:1px solid var(--border,#333);border-radius:4px;padding:6px;">
        <div style="display:flex;gap:4px;margin-bottom:4px;">
          <select class="sk-kind" style="width:120px;">${kindOpts}</select>
          <input class="sk-name" type="text" placeholder="スキル名" value="${escapeHtml(s.name || '')}" style="flex:1;" />
          <button class="btn btn-sm btn-danger sk-del">×</button>
        </div>
        <textarea class="sk-desc" rows="2" placeholder="説明" style="width:100%;">${escapeHtml(s.description || '')}</textarea>
      </div>
    `;
  }
  function draw() {
    rowsEl.innerHTML = skills.map((s, i) => rowHtml(i, s)).join('');
    rowsEl.querySelectorAll('.sk-row').forEach(row => {
      const idx = Number(row.dataset.idx);
      row.querySelector('.sk-del').addEventListener('click', () => {
        skills.splice(idx, 1);
        draw();
      });
      row.querySelector('.sk-name').addEventListener('input', (e) => {
        skills[idx].name = e.target.value;
      });
      row.querySelector('.sk-kind').addEventListener('change', (e) => {
        skills[idx].kind = e.target.value || null;
      });
      row.querySelector('.sk-desc').addEventListener('input', (e) => {
        skills[idx].description = e.target.value;
      });
    });
  }
  wrap.querySelector('#sk-add').addEventListener('click', () => {
    skills.push({ name: '', description: '', kind: null });
    draw();
  });
  draw();

  footerEl.querySelector('[data-act="cancel"]').addEventListener('click', close);
  footerEl.querySelector('[data-act="ok"]').addEventListener('click', async () => {
    const summary = wrap.querySelector('#sk-summary').value;
    const clean = skills
      .map(s => ({ name: (s.name || '').trim(), description: s.description || '', kind: s.kind || null }))
      .filter(s => s.name);
    try {
      const res = await api(`/characters/${ch.id}/skills`, {
        method: 'PUT', body: { skills: clean, summary: summary || null },
      });
      state.character = res.character || state.character;
      close();
      toast('保存しました', 'success');
      renderSkillsSection();
    } catch (err) {
      toast(err.message || String(err), 'error');
    }
  });
}

function renderRecMainStatsSection() {
  const el = document.getElementById('rec-main-stats-area');
  if (!el) return;
  const current = state.character?.recommended_main_stats || {};
  const blocks = MAIN_STAT_SLOTS.map(({ key, label, candidates }) => {
    const picked = new Set(Array.isArray(current[key]) ? current[key] : []);
    const chips = candidates.map(name => `
      <label class="rec-sub-chip ${picked.has(name) ? 'on' : ''}">
        <input type="checkbox" data-slot="${key}" data-val="${escapeHtml(name)}" ${picked.has(name) ? 'checked' : ''} />
        <span>${escapeHtml(name)}</span>
      </label>
    `).join('');
    return `
      <div class="main-stat-slot-block" data-slot="${key}">
        <div class="text-sm text-secondary mb-1"><strong>${escapeHtml(label)}</strong></div>
        <div class="rec-sub-chips">${chips}</div>
      </div>
    `;
  }).join('');
  el.innerHTML = `
    <div class="recommended-editor" data-rec-kind="main_stats">
      <h3 class="mb-1">🎯 推奨メインステ（4/5/6号位） <span class="text-muted text-sm rec-status"></span></h3>
      <div class="text-muted text-sm mb-1">チェックで即時保存。一覧フィルタとスワップモーダルの初期フィルタに反映されます</div>
      ${blocks}
    </div>
  `;
  wireRecMainStats();
}

const _mainStatsSaveTimer = { t: null, seq: 0 };

function wireRecMainStats() {
  const wrap = document.querySelector('[data-rec-kind="main_stats"]');
  if (!wrap) return;
  const status = wrap.querySelector('.rec-status');

  const collect = () => {
    const out = {};
    MAIN_STAT_SLOTS.forEach(({ key }) => { out[key] = []; });
    wrap.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {
      const slot = cb.dataset.slot;
      if (!out[slot]) out[slot] = [];
      out[slot].push(cb.dataset.val);
    });
    return out;
  };

  const doSave = async () => {
    const payload = collect();
    _mainStatsSaveTimer.seq += 1;
    const seq = _mainStatsSaveTimer.seq;
    if (status) status.textContent = '保存中…';
    try {
      const res = await api(`/characters/${state.character.id}/recommended-main-stats`, {
        method: 'PUT', body: { main_stats: payload },
      });
      if (seq !== _mainStatsSaveTimer.seq) return;
      state.character = res.character || { ...state.character, recommended_main_stats: payload };
      if (status) status.textContent = '✓ 保存済み';
      setTimeout(() => { if (status && seq === _mainStatsSaveTimer.seq) status.textContent = ''; }, 1500);
    } catch (err) {
      if (seq !== _mainStatsSaveTimer.seq) return;
      if (status) status.textContent = '';
      toast(`保存失敗: ${err.message}`, 'error');
    }
  };

  wrap.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
      state.character = { ...state.character, recommended_main_stats: collect() };
      clearTimeout(_mainStatsSaveTimer.t);
      _mainStatsSaveTimer.t = setTimeout(doSave, 250);
    });
  });
}

function renderRecEditorsSection() {
  const el = document.getElementById('rec-editors-area');
  if (!el) return;
  const setsMap = setsByName(state.sets);
  el.innerHTML = renderRecommendedEditor() + renderRecommendedDiscEditor(setsMap);
  wireRecommendedEditors();
}

function renderBuildsSection() {
  const el = document.getElementById('builds-area');
  if (!el) return;
  const setsMap = setsByName(state.sets);
  const chunks = [];
  chunks.push('<h3 class="mb-1">● 現在の装備</h3>');
  if (state.current) {
    chunks.push(`<div class="build-wrap" data-kind="current">${renderBuildCard({ character: state.character, build: state.current, actions: ['pin-all', 'clone'], setsByName: setsMap })}</div>`);
  } else {
    chunks.push(`
      <div class="placeholder" style="padding:24px;">
        <div class="big-icon">📡</div>
        <div>現在の装備はまだ同期されていません</div>
        <div class="text-muted text-sm mt-1">HoYoLAB 設定で cookie を登録後、「同期」ボタンで取得できます</div>
      </div>
    `);
  }
  chunks.push(`<h3 class="mb-1 mt-2">📦 プリセット装備 (${state.presets.length})</h3>`);
  if (!state.presets.length) {
    chunks.push('<div class="text-muted text-sm mb-2">プリセットはまだありません。「現在の装備」から「プリセットへ複製」で保存できます。</div>');
  } else {
    for (const b of state.presets) {
      chunks.push(`<div class="build-wrap" data-kind="preset">${renderBuildCard({ character: state.character, build: b, actions: ['pin-all', 'edit', 'delete'], setsByName: setsMap })}</div>`);
    }
  }
  el.innerHTML = chunks.join('');
  wireUp();
}

function _updatePinInState(discId, pinned) {
  const visit = (build) => {
    if (!build?.slots) return;
    for (const s of build.slots) {
      if (s?.disc?.id === discId) s.disc.is_pinned = pinned;
    }
  };
  visit(state.current);
  for (const p of state.presets || []) visit(p);
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
        else if (act === 'pin-all') pinAllBuild(buildId);
      });
    });
    // disc-tile クリックでスワップモーダル（ピンボタンクリック時は除外）
    const allTiles = card.querySelectorAll('.disc-tile');
    allTiles.forEach(tile => {
      tile.addEventListener('click', (e) => {
        if (e.target.closest('[data-act="toggle-pin"]')) return;
        e.stopPropagation();
        const slot = Number(tile.dataset.slot);
        if (!slot) return;
        const currentDiscId = tile.dataset.discId ? Number(tile.dataset.discId) : null;
        openSwapModal({ buildId, slot, currentDiscId });
      });
      tile.style.cursor = 'pointer';
    });
    // disc-tile のピントグル
    card.querySelectorAll('.disc-pin-btn[data-act="toggle-pin"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = Number(btn.dataset.discId);
        if (!id) return;
        const next = !btn.classList.contains('on');
        btn.disabled = true;
        try {
          const res = await api(`/discs/${id}/pin`, { method: 'PUT', body: { pinned: next } });
          const on = !!res?.disc?.is_pinned;
          btn.classList.toggle('on', on);
          btn.closest('.disc-tile')?.classList.toggle('pinned', on);
          btn.title = on ? 'ピン解除' : 'ピン留め';
          // state 側の disc にも反映
          _updatePinInState(id, on);
          toast(on ? '📌 ピン留めしました' : 'ピン解除しました', 'success');
        } catch (err) {
          toast(`ピン操作失敗: ${err.message}`, 'error');
        } finally {
          btn.disabled = false;
        }
      });
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
  const recommendedSets = state.character?.recommended_disc_sets || [];
  const setsMap = setsByName(state.sets);
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

  // 候補の中から実際に存在する set / main_stat / sub_stat 名を抽出（プルダウン用）
  const collator = new Intl.Collator('ja');
  const setOptions = [...new Set(scored.map(s => s.disc.set_name_ja || s.disc.name || '').filter(Boolean))]
    .sort((a, b) => collator.compare(a, b));
  const mainStatOptions = [...new Set(scored.map(s => s.disc.main_stat_name).filter(Boolean))].sort();
  const subStatOptions = [...new Set(
    scored.flatMap(s => (s.disc.sub_stats || []).map(x => x.name)).filter(Boolean)
  )].sort();

  let sortKey = 'score';
  let subStatFor = recommended.find(r => subStatOptions.includes(r)) || subStatOptions[0] || '';
  // フィルタ: 初期値は推奨セット & 推奨サブステ & 推奨メインステ（存在するもののみ）
  const filterSets = new Set(recommendedSets.filter(s => setOptions.includes(s)));
  const filterSubs = new Set(recommended.filter(s => subStatOptions.includes(s)));
  const recommendedMainForSlot = (state.character?.recommended_main_stats?.[String(slot)] || []);
  const filterMain = new Set(recommendedMainForSlot.filter(s => mainStatOptions.includes(s)));

  function filteredRows() {
    return scored.filter(({ disc: d }) => {
      const setName = d.set_name_ja || d.name || '';
      if (filterSets.size && !filterSets.has(setName)) return false;
      if (filterMain.size && !filterMain.has(d.main_stat_name)) return false;
      if (filterSubs.size) {
        const has = (d.sub_stats || []).some(s => filterSubs.has(s.name));
        if (!has) return false;
      }
      return true;
    });
  }

  function sortRows() {
    const arr = filteredRows();
    if (sortKey === 'score') {
      arr.sort((a, b) => (b.score - a.score) || ((b.disc.level || 0) - (a.disc.level || 0)));
    } else if (sortKey === 'set') {
      arr.sort((a, b) => collator.compare(a.disc.set_name_ja || '', b.disc.set_name_ja || '')
        || (b.disc.level || 0) - (a.disc.level || 0));
    } else if (sortKey === 'main_stat') {
      arr.sort((a, b) => collator.compare(statLabel(a.disc.main_stat_name) || '', statLabel(b.disc.main_stat_name) || '')
        || (Number(b.disc.main_stat_value) || 0) - (Number(a.disc.main_stat_value) || 0));
    } else if (sortKey === 'sub_stat') {
      const valOf = (d) => {
        const s = (d.sub_stats || []).find(x => x.name === subStatFor);
        return s ? Number(s.value) || 0 : -1;
      };
      arr.sort((a, b) => valOf(b.disc) - valOf(a.disc));
    } else if (sortKey === 'level') {
      arr.sort((a, b) => (b.disc.level || 0) - (a.disc.level || 0));
    }
    return arr;
  }

  function renderRows() {
    const arr = sortRows();
    const listEl = bodyEl.querySelector('.swap-list');
    const countEl = bodyEl.querySelector('#swap-count');
    if (!listEl) return;
    if (countEl) countEl.textContent = `表示 ${arr.length} / 全 ${scored.length} 件`;
    listEl.innerHTML = arr.length === 0
      ? '<div class="text-muted">条件に一致するディスクがありません</div>'
      : arr.map(({ disc: d, score, mainMatch }) => {
          const inUse = usageByDisc.get(d.id) || [];
          const inUseHere = usedDiscIds.has(d.id);
          const isSelected = currentDiscId === d.id;
          const subs = Array.isArray(d.sub_stats) ? d.sub_stats : [];
          return `
            <div class="swap-row ${isSelected ? 'selected' : ''} ${d.is_pinned ? 'pinned' : ''}" data-disc-id="${d.id}">
              <div class="swap-row-head">
                <button class="swap-pin-btn ${d.is_pinned ? 'on' : ''}" data-act="toggle-pin" data-disc-id="${d.id}" title="${d.is_pinned ? 'ピン解除' : 'ピン留め'}">📌</button>
                <span class="swap-set">${setNameWithPopover(d.set_name_ja || d.name || '-', setsMap.get(d.set_name_ja || d.name || ''))}</span>
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
                  <span class="swap-sub ${recommended.includes(s.name) ? 'recommended' : ''} ${sortKey === 'sub_stat' && s.name === subStatFor ? 'sort-key' : ''}">
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
        }).join('');

    listEl.querySelectorAll('.swap-row').forEach(row => {
      row.addEventListener('click', async (e) => {
        if (e.target.closest('[data-act="toggle-pin"]')) return;
        const newId = Number(row.dataset.discId);
        if (newId === currentDiscId) { close(); return; }
        const newDisc = discs.find(x => x.id === newId) || null;
        await applySwap({ buildId, slot, discId: newId, isCurrent, close, newDisc });
      });
    });
    listEl.querySelectorAll('[data-act="toggle-pin"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = Number(btn.dataset.discId);
        const d = discs.find(x => x.id === id);
        if (!d) return;
        const next = !d.is_pinned;
        btn.disabled = true;
        try {
          const res = await api(`/discs/${id}/pin`, { method: 'PUT', body: { pinned: next } });
          d.is_pinned = !!res?.disc?.is_pinned;
          btn.classList.toggle('on', d.is_pinned);
          btn.closest('.swap-row')?.classList.toggle('pinned', d.is_pinned);
          btn.title = d.is_pinned ? 'ピン解除' : 'ピン留め';
          toast(d.is_pinned ? '📌 ピン留めしました' : 'ピン解除しました', 'success');
        } catch (err) {
          toast(`ピン操作失敗: ${err.message}`, 'error');
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  bodyEl.innerHTML = `
    ${isCurrent ? `
      <div class="alert alert-warning text-sm mb-1">
        ⚠ 「現在の装備」を変更すると HoYoLAB 同期で上書きされます。残したい構成は「プリセットへ複製」してから編集してください。
      </div>` : ''}
    <div class="text-muted text-sm mb-1">推奨サブステ: ${
      recommended.length ? recommended.map(escapeHtml).join(', ') : '<em>未設定</em>'
    } / <span id="swap-count">表示 ${scored.length} / 全 ${scored.length} 件</span></div>
    <div class="swap-filter-block">
      <div class="swap-filter-row">
        <label class="text-sm text-muted">セット:</label>
        <div class="filter-chips" id="filter-chips-set">
          ${setOptions.map(n => `
            <label class="rec-sub-chip with-popover ${filterSets.has(n) ? 'on' : ''}">
              <input type="checkbox" data-val="${escapeHtml(n)}" ${filterSets.has(n) ? 'checked' : ''} />
              ${setNameWithPopover(n, setsMap.get(n))}
            </label>
          `).join('')}
        </div>
      </div>
      <div class="swap-filter-row">
        <label class="text-sm text-muted">メインステ:</label>
        <div class="filter-chips" id="filter-chips-main">
          ${mainStatOptions.map(n => `
            <label class="rec-sub-chip ${filterMain.has(n) ? 'on' : ''}">
              <input type="checkbox" data-val="${escapeHtml(n)}" ${filterMain.has(n) ? 'checked' : ''} />
              <span>${escapeHtml(statLabel(n))}</span>
            </label>
          `).join('')}
        </div>
      </div>
      <div class="swap-filter-row">
        <label class="text-sm text-muted">サブステ含む:</label>
        <div class="filter-chips" id="filter-chips-sub">
          ${subStatOptions.map(n => `
            <label class="rec-sub-chip ${filterSubs.has(n) ? 'on' : ''}">
              <input type="checkbox" data-val="${escapeHtml(n)}" ${filterSubs.has(n) ? 'checked' : ''} />
              <span>${escapeHtml(n)}</span>
            </label>
          `).join('')}
        </div>
      </div>
      <div class="swap-filter-row">
        <button class="btn btn-sm" id="swap-filter-clear">フィルタ全解除</button>
      </div>
    </div>
    <div class="swap-sort-bar">
      <label class="text-sm text-muted">並び替え:</label>
      <select id="swap-sort-key" class="select-sm">
        <option value="score">推奨スコア</option>
        <option value="set">セット名</option>
        <option value="main_stat">メインステ</option>
        <option value="sub_stat">サブステ値</option>
        <option value="level">Lv</option>
      </select>
      <select id="swap-sort-substat" class="select-sm" style="display:none;">
        ${subStatOptions.map(n => `<option value="${escapeHtml(n)}" ${n === subStatFor ? 'selected' : ''}>${escapeHtml(n)}</option>`).join('')}
      </select>
    </div>
    <div class="swap-list"></div>
  `;

  const sortSel = bodyEl.querySelector('#swap-sort-key');
  const subSel = bodyEl.querySelector('#swap-sort-substat');

  function wireChipFilter(containerId, set) {
    const cont = bodyEl.querySelector('#' + containerId);
    if (!cont) return;
    cont.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        cb.closest('.rec-sub-chip').classList.toggle('on', cb.checked);
        if (cb.checked) set.add(cb.dataset.val);
        else set.delete(cb.dataset.val);
        renderRows();
      });
    });
  }
  wireChipFilter('filter-chips-set', filterSets);
  wireChipFilter('filter-chips-sub', filterSubs);
  wireChipFilter('filter-chips-main', filterMain);

  bodyEl.querySelector('#swap-filter-clear').addEventListener('click', () => {
    filterSets.clear();
    filterSubs.clear();
    filterMain.clear();
    bodyEl.querySelectorAll('.filter-chips input[type="checkbox"]').forEach(cb => {
      cb.checked = false;
      cb.closest('.rec-sub-chip').classList.remove('on');
    });
    renderRows();
  });
  sortSel.addEventListener('change', () => {
    sortKey = sortSel.value;
    subSel.style.display = sortKey === 'sub_stat' ? '' : 'none';
    renderRows();
  });
  subSel.addEventListener('change', () => {
    subStatFor = subSel.value;
    renderRows();
  });
  renderRows();
}

async function applySwap({ buildId, slot, discId, isCurrent, close, newDisc = null }) {
  try {
    await api(`/builds/${buildId}/slots/${slot}`, {
      method: 'PUT', body: { disc_id: discId },
    });
    toast(discId == null ? 'スロットを外しました' : 'ディスクを差し替えました', 'success');
    close();
    // ローカル更新: stats スナップショットと _residualAdd キャッシュを保持したまま
    // slots のみ差し替えてビルドカードを即時再描画（リロードすると residual が壊れるので避ける）
    const build = isCurrent
      ? state.current
      : state.presets.find(p => p.id === buildId);
    if (build) {
      const slots = build.slots || [];
      const idx = slots.findIndex(s => s.slot === slot);
      const entry = { slot, disc_id: discId, disc: newDisc || null };
      if (idx >= 0) slots[idx] = entry;
      else slots.push(entry);
      build.slots = slots;
      renderBuildsSection();
    } else {
      await load(state.character.slug);
    }
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

async function pinAllBuild(buildId) {
  try {
    const res = await api(`/builds/${buildId}/pin-all`, { method: 'POST' });
    const n = res?.pinned ?? 0;
    toast(n ? `📌 ${n} 枚ピン留めしました` : '全てピン済みでした', 'success');
    await load(state.character.slug);
  } catch (err) {
    toast(`ピン操作失敗: ${err.message}`, 'error');
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
  state = { character: null, current: null, presets: [], sets: [] };
}
