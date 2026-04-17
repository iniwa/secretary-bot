/** 編成モード: スタンドアロン部隊 + 高難易度グループ。ディスク使い回し検知 */
import { api } from '../api.js';
import { escapeHtml, toast, confirmDialog, promptDialog, openModal } from '../app.js';
import { elementLabel } from '../labels.js';

let state = {
  groups: [],
  standalone: [],
  characters: [],
  buildsByChar: {},
};

export function render() {
  return `
    <div class="page-header">
      <h2>🛡️ 編成モード</h2>
      <button class="btn btn-sm" id="refresh-btn">↻ 更新</button>
      <button class="btn btn-sm btn-primary" id="new-team-btn">＋ 部隊（普段使い）</button>
      <button class="btn btn-sm btn-primary" id="new-group-btn">＋ 高難易度グループ</button>
    </div>
    <p class="text-muted text-sm mb-2">
      3 人 1 組の部隊を登録すると、ディスクの使い回しを自動検知します。
      高難易度グループでは最大 10 部隊まとめて競合チェックできます。
    </p>
    <div id="body"><div class="placeholder"><div class="spinner"></div></div></div>
  `;
}

export async function mount() {
  document.getElementById('refresh-btn').addEventListener('click', load);
  document.getElementById('new-team-btn').addEventListener('click', onNewStandaloneTeam);
  document.getElementById('new-group-btn').addEventListener('click', onNewGroup);
  await load();
}

async function load() {
  const el = document.getElementById('body');
  el.innerHTML = '<div class="placeholder"><div class="spinner"></div></div>';
  try {
    const [tg, chars] = await Promise.all([
      api('/team-groups'),
      api('/characters'),
    ]);
    state.groups = tg.groups || [];
    state.standalone = tg.standalone_teams || [];
    state.characters = (chars.characters || []).slice().sort((a, b) =>
      (a.display_order ?? 0) - (b.display_order ?? 0) ||
      (a.name_ja || '').localeCompare(b.name_ja || ''));
    renderAll();
  } catch (err) {
    el.innerHTML = `<div class="placeholder"><div class="big-icon">⚠️</div><div>${escapeHtml(err.message)}</div></div>`;
  }
}

function renderAll() {
  const el = document.getElementById('body');
  const parts = [];
  if (state.standalone.length) {
    parts.push(`<h3 class="mt-2">普段使い / 危局（単独部隊）</h3>`);
    parts.push(`<div class="teams-list">${state.standalone.map(t => teamBlockHtml(t, null)).join('')}</div>`);
  }
  if (state.groups.length) {
    parts.push(`<h3 class="mt-3">高難易度グループ</h3>`);
    parts.push(state.groups.map(groupBlockHtml).join(''));
  }
  if (!state.standalone.length && !state.groups.length) {
    parts.push(`<div class="placeholder"><div class="big-icon">🛡️</div><div>部隊がありません。右上の「＋」から作成してください。</div></div>`);
  }
  el.innerHTML = parts.join('');
  bindEvents();
}

function groupBlockHtml(g) {
  const conflictBadge = g.conflicts?.length
    ? `<span class="build-current-badge" style="background:#c44;">⚠ ${g.conflicts.length} 件</span>`
    : '';
  return `
    <div class="team-group" data-group-id="${g.id}">
      <div class="team-group-header">
        <h4>${escapeHtml(g.name)} ${conflictBadge}</h4>
        <span class="text-muted text-sm">${(g.teams || []).length}/10 部隊</span>
        <div class="spacer"></div>
        <button class="btn btn-sm" data-act="add-team" data-group-id="${g.id}"
          ${(g.teams || []).length >= 10 ? 'disabled' : ''}>＋部隊</button>
        <button class="btn btn-sm" data-act="rename-group" data-group-id="${g.id}">名前変更</button>
        <button class="btn btn-sm btn-danger" data-act="delete-group" data-group-id="${g.id}">削除</button>
      </div>
      ${g.description ? `<div class="text-muted text-sm">${escapeHtml(g.description)}</div>` : ''}
      ${g.conflicts?.length ? conflictSummaryHtml(g.conflicts, true) : ''}
      <div class="teams-list">
        ${(g.teams || []).map(t => teamBlockHtml(t, g.id)).join('')}
      </div>
    </div>
  `;
}

function teamBlockHtml(t, groupId) {
  const conflictBadge = t.conflicts?.length
    ? `<span class="build-current-badge" style="background:#c44;">⚠ ${t.conflicts.length} 件</span>`
    : '';
  return `
    <div class="team-block" data-team-id="${t.id}">
      <div class="team-header">
        <strong>${escapeHtml(t.name)}</strong>
        ${conflictBadge}
        <div class="spacer"></div>
        <button class="btn btn-sm" data-act="rename-team" data-team-id="${t.id}">名前変更</button>
        <button class="btn btn-sm btn-danger" data-act="delete-team" data-team-id="${t.id}">削除</button>
      </div>
      <div class="team-slots">
        ${[0, 1, 2].map(pos => teamSlotHtml(t, pos)).join('')}
      </div>
      ${t.conflicts?.length ? conflictSummaryHtml(t.conflicts, false) : ''}
    </div>
  `;
}

function teamSlotHtml(team, pos) {
  const slot = (team.slots || []).find(s => s.position === pos) || { position: pos };
  const filled = !!slot.character_id;
  const name = filled ? (slot.character_name_ja || '?') : '空きスロット';
  const buildInfo = filled
    ? `<div class="text-xs text-muted">${escapeHtml(slot.build_name || '-')}${slot.build_is_current ? ' <span class="build-current-badge">現在</span>' : ''}</div>`
    : '';
  return `
    <div class="team-slot ${filled ? '' : 'empty'}" data-team-id="${team.id}" data-pos="${pos}">
      <div class="team-slot-name">${escapeHtml(name)}</div>
      ${buildInfo}
      <div class="team-slot-actions">
        <button class="btn btn-sm" data-act="pick-member" data-team-id="${team.id}" data-pos="${pos}">
          ${filled ? '変更' : '選択'}
        </button>
        ${filled ? `<button class="btn btn-sm btn-danger" data-act="clear-member" data-team-id="${team.id}" data-pos="${pos}">×</button>` : ''}
      </div>
    </div>
  `;
}

function conflictSummaryHtml(conflicts, showTeamId) {
  return `
    <div class="team-conflicts">
      <div class="text-sm" style="font-weight:600;color:#c44;">⚠ ディスク使い回し</div>
      <ul class="text-sm">
        ${conflicts.map(c => {
          const d = c.disc || {};
          const setName = d.set_name_ja || d.set_name || '-';
          const who = (c.used_by || []).map(u => {
            const t = showTeamId && u.team_id ? `[T${u.team_id}] ` : '';
            return escapeHtml(`${t}${u.character_name_ja || '-'} / ${u.build_name || '-'}`);
          }).join(' ⇔ ');
          return `<li>Slot ${d.slot ?? '?'} ${escapeHtml(setName)} — ${who}</li>`;
        }).join('')}
      </ul>
    </div>
  `;
}

function bindEvents() {
  const body = document.getElementById('body');
  body.querySelectorAll('[data-act]').forEach(btn => {
    btn.addEventListener('click', onActionClick);
  });
}

async function onActionClick(e) {
  const btn = e.currentTarget;
  const act = btn.dataset.act;
  const teamId = btn.dataset.teamId ? Number(btn.dataset.teamId) : null;
  const groupId = btn.dataset.groupId ? Number(btn.dataset.groupId) : null;
  const pos = btn.dataset.pos != null ? Number(btn.dataset.pos) : null;
  try {
    if (act === 'add-team') await onAddTeamToGroup(groupId);
    else if (act === 'rename-team') await onRenameTeam(teamId);
    else if (act === 'delete-team') await onDeleteTeam(teamId);
    else if (act === 'rename-group') await onRenameGroup(groupId);
    else if (act === 'delete-group') await onDeleteGroup(groupId);
    else if (act === 'pick-member') await onPickMember(teamId, pos);
    else if (act === 'clear-member') await onClearMember(teamId, pos);
  } catch (err) {
    toast(err.message || String(err), 'error');
  }
}

async function onNewStandaloneTeam() {
  const name = await promptDialog({ title: '部隊名', label: '例: 普段使い / 危局-1', value: '新規部隊' });
  if (!name) return;
  await api('/teams', { method: 'POST', body: { name } });
  toast('部隊を作成しました', 'success');
  await load();
}

async function onNewGroup() {
  const name = await promptDialog({ title: '高難易度グループ名', label: '例: 式輿防衛戦', value: '新規グループ' });
  if (!name) return;
  await api('/team-groups', { method: 'POST', body: { name } });
  toast('グループを作成しました', 'success');
  await load();
}

async function onAddTeamToGroup(groupId) {
  const name = await promptDialog({ title: '部隊名', label: '例: 1部隊', value: `部隊${Date.now() % 100}` });
  if (!name) return;
  await api('/teams', { method: 'POST', body: { name, group_id: groupId } });
  toast('部隊を追加しました', 'success');
  await load();
}

async function onRenameTeam(teamId) {
  const current = findTeam(teamId);
  const name = await promptDialog({ title: '部隊名変更', value: current?.name || '' });
  if (!name) return;
  await api(`/teams/${teamId}`, { method: 'PUT', body: { name } });
  await load();
}

async function onDeleteTeam(teamId) {
  if (!await confirmDialog('この部隊を削除しますか？')) return;
  await api(`/teams/${teamId}`, { method: 'DELETE' });
  toast('削除しました', 'success');
  await load();
}

async function onRenameGroup(groupId) {
  const g = state.groups.find(x => x.id === groupId);
  const name = await promptDialog({ title: 'グループ名変更', value: g?.name || '' });
  if (!name) return;
  await api(`/team-groups/${groupId}`, { method: 'PUT', body: { name } });
  await load();
}

async function onDeleteGroup(groupId) {
  if (!await confirmDialog('このグループと所属する部隊を全て削除しますか？')) return;
  await api(`/team-groups/${groupId}`, { method: 'DELETE' });
  toast('削除しました', 'success');
  await load();
}

async function onClearMember(teamId, pos) {
  await api(`/teams/${teamId}/slots/${pos}`, {
    method: 'PUT', body: { character_id: null, build_id: null },
  });
  await load();
}

async function onPickMember(teamId, pos) {
  const team = findTeam(teamId);
  const current = team?.slots?.find(s => s.position === pos) || {};
  const wrap = document.createElement('div');

  const elementValues = [...new Set(state.characters.map(c => c.element).filter(v => v !== null && v !== undefined && v !== ''))];
  const factionValues = [...new Set(state.characters.map(c => c.faction).filter(Boolean))].sort();

  wrap.innerHTML = `
    <div class="row gap-1 mb-1" style="flex-wrap:wrap;">
      <input type="search" id="mem-search" placeholder="名前で検索" style="flex:1;min-width:140px;" />
      <select id="mem-element" style="min-width:110px;">
        <option value="">全属性</option>
        ${elementValues.map(e => `<option value="${escapeHtml(String(e))}">${escapeHtml(elementLabel(e))}</option>`).join('')}
      </select>
      <select id="mem-faction" style="min-width:110px;">
        <option value="">全陣営</option>
        ${factionValues.map(f => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join('')}
      </select>
      <select id="mem-sort" style="min-width:110px;">
        <option value="display_order">既定順</option>
        <option value="name_ja">名前</option>
        <option value="element">属性</option>
        <option value="faction">陣営</option>
        <option value="has_current">現在ビルド有→無</option>
      </select>
    </div>
    <label class="text-secondary text-sm">キャラ <span id="mem-count" class="text-muted text-xs"></span></label>
    <select id="mem-char" size="10" style="width:100%;margin:4px 0 12px;">
      <option value="">（未選択）</option>
    </select>
    <label class="text-secondary text-sm">ビルド</label>
    <select id="mem-build" style="width:100%;margin-top:4px;">
      <option value="">現在の装備（自動）</option>
    </select>
    <div id="mem-build-hint" class="text-xs text-muted mt-1"></div>
  `;
  const { footerEl, close } = openModal({ title: 'メンバー選択', body: wrap });
  footerEl.innerHTML = `
    <button class="btn" data-act="cancel">キャンセル</button>
    <button class="btn btn-primary" data-act="ok">OK</button>
  `;
  const searchInput = wrap.querySelector('#mem-search');
  const elemSel = wrap.querySelector('#mem-element');
  const factionSel = wrap.querySelector('#mem-faction');
  const sortSel = wrap.querySelector('#mem-sort');
  const charSel = wrap.querySelector('#mem-char');
  const buildSel = wrap.querySelector('#mem-build');
  const countSpan = wrap.querySelector('#mem-count');
  const hint = wrap.querySelector('#mem-build-hint');

  const collator = new Intl.Collator('ja');

  function rebuildChars() {
    const q = (searchInput.value || '').trim().toLowerCase();
    const fe = elemSel.value;
    const ff = factionSel.value;
    const sk = sortSel.value;
    let list = state.characters.filter(c => {
      if (fe && String(c.element) !== fe) return false;
      if (ff && (c.faction || '') !== ff) return false;
      if (q && !(c.name_ja || '').toLowerCase().includes(q)) return false;
      return true;
    });
    if (sk === 'name_ja') {
      list.sort((a, b) => collator.compare(a.name_ja || '', b.name_ja || ''));
    } else if (sk === 'element') {
      list.sort((a, b) => collator.compare(elementLabel(a.element), elementLabel(b.element))
        || (a.display_order ?? 0) - (b.display_order ?? 0));
    } else if (sk === 'faction') {
      list.sort((a, b) => collator.compare(a.faction || '', b.faction || '')
        || (a.display_order ?? 0) - (b.display_order ?? 0));
    } else if (sk === 'has_current') {
      list.sort((a, b) => {
        const av = (a.has_current_build || a.current_build_id) ? 1 : 0;
        const bv = (b.has_current_build || b.current_build_id) ? 1 : 0;
        return bv - av || (a.display_order ?? 0) - (b.display_order ?? 0);
      });
    } else {
      list.sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0));
    }
    const prevId = Number(charSel.value) || current.character_id || null;
    const options = [`<option value="">（未選択）</option>`];
    for (const c of list) {
      const label = `${c.name_ja}（${elementLabel(c.element)} / ${c.faction || '-'}）`;
      const selected = c.id === prevId ? ' selected' : '';
      options.push(`<option value="${c.id}"${selected}>${escapeHtml(label)}</option>`);
    }
    charSel.innerHTML = options.join('');
    countSpan.textContent = `${list.length} 件`;
    if ((Number(charSel.value) || null) !== prevId) {
      refreshBuilds();
    }
  }

  async function refreshBuilds() {
    const cid = Number(charSel.value) || null;
    buildSel.innerHTML = '<option value="">現在の装備（自動）</option>';
    hint.textContent = '';
    if (!cid) return;
    try {
      const builds = await loadBuildsForChar(cid);
      for (const b of builds) {
        const opt = document.createElement('option');
        opt.value = b.id;
        opt.textContent = `${b.is_current ? '[現在] ' : ''}${b.name || '無名'}`;
        if (b.id === current.build_id) opt.selected = true;
        buildSel.appendChild(opt);
      }
      if (!builds.length) hint.textContent = 'ビルドがありません。キャラ詳細で先にビルドを登録してください。';
    } catch (e) {
      hint.textContent = `ビルド取得失敗: ${e.message}`;
    }
  }
  searchInput.addEventListener('input', rebuildChars);
  elemSel.addEventListener('change', rebuildChars);
  factionSel.addEventListener('change', rebuildChars);
  sortSel.addEventListener('change', rebuildChars);
  charSel.addEventListener('change', refreshBuilds);
  rebuildChars();
  await refreshBuilds();

  await new Promise(resolve => {
    footerEl.querySelector('[data-act="cancel"]').addEventListener('click', () => { close(); resolve(); });
    footerEl.querySelector('[data-act="ok"]').addEventListener('click', async () => {
      const character_id = Number(charSel.value) || null;
      const build_id = Number(buildSel.value) || null;
      close();
      try {
        await api(`/teams/${teamId}/slots/${pos}`, {
          method: 'PUT', body: { character_id, build_id },
        });
        await load();
      } catch (err) {
        toast(err.message || String(err), 'error');
      }
      resolve();
    });
  });
}

async function loadBuildsForChar(cid) {
  if (state.buildsByChar[cid]) return state.buildsByChar[cid];
  const data = await api(`/characters/${cid}/builds`);
  const list = data.builds || [];
  state.buildsByChar[cid] = list;
  return list;
}

function findTeam(teamId) {
  for (const t of state.standalone) if (t.id === teamId) return t;
  for (const g of state.groups) for (const t of (g.teams || [])) if (t.id === teamId) return t;
  return null;
}
