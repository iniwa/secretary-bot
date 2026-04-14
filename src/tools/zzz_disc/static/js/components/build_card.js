/** ビルドカード描画（HoYoLAB 戦績カード風） */
import { escapeHtml } from '../app.js';
import { statLabel, formatStatValue, elementLabel } from '../labels.js';

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
export function renderBuildCard({ character, build, actions = [] }) {
  if (!build) return '';
  const stats = build.stats || {};
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
        ${renderDiscs(build.slots || [])}
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
  // 並び順通りに出し、残りは後ろにアルファベット順
  const known = STATS_ORDER.filter(k => stats[k] != null);
  const remaining = Object.keys(stats).filter(k => !STATS_ORDER.includes(k)).sort();
  const all = [...known, ...remaining];
  if (!all.length) return '<div class="text-muted text-sm">ステータス情報なし</div>';
  return all.map(key => `
    <div class="stat-row">
      <span class="label">${escapeHtml(statLabel(key))}</span>
      <span class="value">${escapeHtml(formatStatValue(key, stats[key]))}</span>
    </div>
  `).join('');
}

function renderDiscs(slots) {
  // 1..6 の空セルを埋める
  const map = new Map();
  for (const s of slots) {
    if (s?.slot) map.set(s.slot, s);
  }
  const cells = [];
  for (let slot = 1; slot <= 6; slot++) {
    const entry = map.get(slot);
    if (!entry || !entry.disc) {
      cells.push(`<div class="disc-tile empty" data-slot="${slot}">部位 ${slot} 未装備</div>`);
      continue;
    }
    cells.push(renderDiscTile(entry));
  }
  return cells.join('');
}

function renderDiscTile(entry) {
  const d = entry.disc || {};
  const shared = Array.isArray(entry.shared_with) ? entry.shared_with.filter(x => x) : [];
  const sharedCount = shared.length;
  const setName = d.set_name_ja || d.set_name || '-';
  const level = d.level != null ? `Lv.${d.level}` : '';
  const subs = Array.isArray(d.sub_stats) ? d.sub_stats : [];

  return `
    <div class="disc-tile ${sharedCount ? 'shared' : ''}" data-disc-id="${d.id}" data-slot="${d.slot}" title="${shared.length ? '⚠ ' + shared.map(s => (s.character_name_ja || '') + ': ' + (s.name || '')).join(' / ') : ''}">
      <div class="disc-tile-header">
        <span class="disc-tile-set">${escapeHtml(setName)}${sharedCount ? `<span class="shared-warning">⚠${sharedCount}</span>` : ''}</span>
        <span class="disc-tile-slot">[${d.slot}]</span>
      </div>
      ${level ? `<div class="disc-tile-level">${escapeHtml(level)}</div>` : ''}
      <div class="disc-main">
        <span class="name">${escapeHtml(statLabel(d.main_stat_name))}</span>
        <span class="value">${escapeHtml(formatStatValue(d.main_stat_name, d.main_stat_value))}</span>
      </div>
      <div class="disc-subs">
        ${subs.map(renderSubRow).join('')}
      </div>
    </div>
  `;
}

function renderSubRow(s) {
  if (!s || !s.name) return '';
  const upgrades = Number(s.upgrades || 0);
  const dots = upgrades > 0 ? `<span class="sub-dots">${'<span class="dot"></span>'.repeat(upgrades)}</span>` : '';
  return `
    <div class="disc-sub-row">
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
