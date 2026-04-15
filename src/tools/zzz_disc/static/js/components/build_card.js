/** ビルドカード描画（HoYoLAB 戦績カード風） */
import { escapeHtml } from '../app.js';
import { statLabel, formatStatValue, elementLabel, setNameWithPopover } from '../labels.js';

/** STATS の表示順（先頭ほど上に並ぶ） */
const STATS_ORDER = [
  'hp', 'atk', 'def', 'impact',
  'crit_rate', 'crit_dmg',
  'anomaly_proficiency', 'anomaly_mastery',
  'pen_ratio', 'pen_value',
  'energy_regen',
  'element_dmg_bonus',
  'ether_dmg', 'fire_dmg', 'ice_dmg', 'electric_dmg', 'physical_dmg',
];

/**
 * @param {Object} opts
 * @param {Object} opts.character - {slug, name_ja, element, faction, icon_url}
 * @param {Object} opts.build - 完全な build オブジェクト
 * @param {Array<string>} opts.actions - ['edit', 'clone', 'delete'] など表示したいアクション
 */
export function renderBuildCard({ character, build, actions = [], setsByName = null }) {
  if (!build) return '';
  const stats = build.stats || {};
  const recommended = new Set(character?.recommended_substats || []);
  const setsMap = setsByName || new Map();
  const portraitStyle = character?.icon_url ? `background-image:url(${escapeHtml(character.icon_url)});` : '';
  const rank = build.rank || '';
  const synced = build.synced_at ? formatDate(build.synced_at) : null;

  return `
    <div class="build-card" data-build-id="${build.id}">
      <div class="build-card-header">
        <div class="build-portrait" style="${portraitStyle}"></div>
        <div class="build-header-body">
          <h2>${escapeHtml(character?.name_ja || '-')}</h2>
          <div class="text-muted text-sm">${escapeHtml(elementLabel(character?.element))} / ${escapeHtml(character?.faction || '-')}</div>
          <div class="meta">
            <strong>${escapeHtml(build.name || '無名ビルド')}</strong>
            ${rank ? `<span class="rank-badge rank-${escapeHtml(rank)}">${escapeHtml(rank)}</span>` : ''}
            ${build.tag ? `<span class="build-tag">${escapeHtml(build.tag)}</span>` : ''}
            ${build.is_current ? '<span class="build-current-badge">● 現在の装備</span>' : ''}
            ${synced ? `<span class="build-synced-at">synced: ${escapeHtml(synced)}</span>` : ''}
          </div>
          ${build.notes ? `<div class="text-sm text-secondary mt-1">${escapeHtml(build.notes)}</div>` : ''}
        </div>
        <div class="build-card-actions">
          ${renderActions(actions, build)}
        </div>
      </div>

      <div class="stats-grid">
        ${renderStats(stats)}
      </div>

      <div class="disc-grid">
        ${renderDiscs(build.slots || [], recommended, setsMap)}
      </div>
    </div>
  `;
}

function renderActions(actions, build) {
  const parts = [];
  if (actions.includes('clone')) {
    parts.push(`<button class="btn btn-sm btn-primary" data-act="clone">プリセットへ複製</button>`);
  }
  if (actions.includes('edit')) {
    parts.push(`<button class="btn btn-sm" data-act="edit">編集</button>`);
  }
  if (actions.includes('delete')) {
    parts.push(`<button class="btn btn-sm btn-danger" data-act="delete">削除</button>`);
  }
  return parts.join('');
}

function renderStats(stats) {
  // 並び順通りに出し、残りは後ろにアルファベット順（_で始まるメタは除外）
  const keys = Object.keys(stats).filter(k => !k.startsWith('_'));
  const known = STATS_ORDER.filter(k => keys.includes(k));
  const remaining = keys.filter(k => !STATS_ORDER.includes(k)).sort();
  const all = [...known, ...remaining];
  if (!all.length) return '<div class="text-muted text-sm">ステータス情報なし</div>';
  return all.map(key => {
    const v = stats[key];
    // v: {base, add, final} 形式 or 旧レガシー（数値/文字列）
    if (v && typeof v === 'object' && 'final' in v) {
      const finalTxt = v.final || '-';
      const addTxt = v.add && v.add !== '' && v.add !== '0' ? v.add : '';
      const baseTxt = v.base && v.base !== '' ? v.base : '';
      const breakdown = (baseTxt || addTxt)
        ? `<span class="stat-breakdown">${escapeHtml(baseTxt || '-')}${addTxt ? ` <span class="stat-add">+${escapeHtml(addTxt)}</span>` : ''}</span>`
        : '';
      return `
        <div class="stat-row">
          <span class="label">${escapeHtml(statLabel(key))}</span>
          <span class="value">
            <span class="stat-final">${escapeHtml(finalTxt)}</span>
            ${breakdown}
          </span>
        </div>`;
    }
    return `
      <div class="stat-row">
        <span class="label">${escapeHtml(statLabel(key))}</span>
        <span class="value">${escapeHtml(formatStatValue(key, v))}</span>
      </div>`;
  }).join('');
}

function renderDiscs(slots, recommended = new Set(), setsMap = new Map()) {
  // 1..6 の空セルを埋める
  const map = new Map();
  for (const s of slots) {
    if (s?.slot) map.set(s.slot, s);
  }
  const cells = [];
  for (let slot = 1; slot <= 6; slot++) {
    const entry = map.get(slot);
    if (!entry || !entry.disc) {
      cells.push(`
        <div class="disc-tile empty" data-slot="${slot}">
          <span class="disc-slot-badge">${slot}</span>
          <span class="disc-tile-empty-text">未装備</span>
        </div>
      `);
      continue;
    }
    cells.push(renderDiscTile(entry, recommended, setsMap));
  }
  return cells.join('');
}

function renderDiscTile(entry, recommended = new Set(), setsMap = new Map()) {
  const d = entry.disc || {};
  const shared = Array.isArray(entry.shared_with) ? entry.shared_with.filter(x => x) : [];
  const sharedCount = shared.length;
  const setName = d.set_name_ja || d.set_name || '-';
  const level = d.level != null ? `Lv.${d.level}` : '';
  const subs = Array.isArray(d.sub_stats) ? d.sub_stats : [];

  const iconHtml = d.icon_url
    ? `<img class="disc-tile-icon" src="${escapeHtml(d.icon_url)}" alt="" loading="lazy" />`
    : '';
  const rawName = (d.name || '').replace(/\s*\[\d+\]\s*$/, '').trim();
  // setName が未解決 (「-」) のときは d.name をフォールバック表示
  const displayName = (setName && setName !== '-') ? setName : (rawName || '-');
  const setMaster = setsMap.get(displayName) || null;
  const setNameHtml = setNameWithPopover(displayName, setMaster, {
    suffix: sharedCount ? `<span class="shared-warning">⚠${sharedCount}</span>` : '',
  });
  const tooltip = shared.length ? '⚠ ' + shared.map(s => (s.character_name_ja || '') + ': ' + (s.name || '')).join(' / ') : '';
  return `
    <div class="disc-tile ${sharedCount ? 'shared' : ''}" data-disc-id="${d.id}" data-slot="${d.slot}" title="${escapeHtml(tooltip)}">
      <span class="disc-slot-badge">${d.slot}</span>
      <div class="disc-tile-header">
        ${iconHtml}
        <span class="disc-tile-set">${setNameHtml}</span>
      </div>
      ${level ? `<div class="disc-tile-level">${escapeHtml(level)}</div>` : ''}
      <div class="disc-main">
        <span class="name">${escapeHtml(statLabel(d.main_stat_name))}</span>
        <span class="value">${escapeHtml(formatStatValue(d.main_stat_name, d.main_stat_value))}</span>
      </div>
      <div class="disc-subs">
        ${subs.map(s => renderSubRow(s, recommended)).join('')}
      </div>
    </div>
  `;
}

function renderSubRow(s, recommended = new Set()) {
  if (!s || !s.name) return '';
  const upgrades = Number(s.upgrades || 0);
  const dots = upgrades > 0 ? `<span class="sub-dots">${'<span class="dot"></span>'.repeat(upgrades)}</span>` : '';
  const isRec = recommended.has(s.name);
  return `
    <div class="disc-sub-row ${isRec ? 'recommended' : ''}">
      <span class="sub-name">${escapeHtml(statLabel(s.name))}</span>
      ${dots}
      <span class="sub-value">${escapeHtml(formatStatValue(s.name, s.value))}</span>
    </div>
  `;
}

function formatDate(iso) {
  try {
    const d = new Date(iso);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${y}-${m}-${day} ${hh}:${mm}`;
  } catch {
    return iso;
  }
}
