/** ステータス名・属性名の日本語ラベル辞書 */

export const STAT_LABELS = {
  hp: 'HP',
  atk: '攻撃力',
  def: '防御力',
  impact: '衝撃力',
  crit_rate: '会心率',
  crit_dmg: '会心ダメージ',
  anomaly_mastery: '異常マスタリー',
  anomaly_proficiency: '異常掌握',
  pen_ratio: '貫通率',
  pen_value: '貫通値',
  energy_regen: 'エネルギー自動回復',
  element_dmg_bonus: '属性ダメージボーナス',
  physical_dmg: '物理ダメージボーナス',
  fire_dmg: '炎属性ダメージボーナス',
  ice_dmg: '氷属性ダメージボーナス',
  electric_dmg: '電気属性ダメージボーナス',
  ether_dmg: 'エーテル属性ダメージボーナス',
  hp_pct: 'HP%',
  atk_pct: '攻撃力%',
  def_pct: '防御力%',
  hp_flat: 'HP（実数）',
  atk_flat: '攻撃力（実数）',
  def_flat: '防御力（実数）',
};

export function statLabel(key) {
  if (!key) return '';
  return STAT_LABELS[key] || key;
}

/** 表示用フォーマット（%系は%付き、その他は整数） */
export function formatStatValue(key, value) {
  if (value == null || value === '') return '-';
  const num = typeof value === 'number' ? value : parseFloat(value);
  if (!Number.isFinite(num)) return String(value);
  // 日本語名末尾の % もしくは _pct/_rate 等で割合扱い
  const isPercent = /%$/.test(String(key))
    || /_pct$|_rate$|_dmg$|_ratio$|_bonus$|_regen$/.test(key);
  if (isPercent) return `${num.toFixed(1)}%`;
  return String(Math.round(num));
}

export const ELEMENT_LABELS = {
  physical: '物理',
  fire: '炎',
  ice: '氷',
  electric: '電気',
  ether: 'エーテル',
  // ZZZ 数値 ID（HoYoLAB 互換）
  200: '物理', 201: '炎', 202: '氷', 203: '電気', 205: 'エーテル',
  '200': '物理', '201': '炎', '202': '氷', '203': '電気', '205': 'エーテル',
};

export function elementLabel(key) {
  return ELEMENT_LABELS[key] || key || '-';
}

export const SLOT_LABELS = {
  1: '部位1',
  2: '部位2',
  3: '部位3',
  4: '部位4',
  5: '部位5',
  6: '部位6',
};

export const RANK_LABELS = {
  S: 'S',
  A: 'A',
  B: 'B',
  C: 'C',
};

/** HoYoLAB の <color=#xxx>...</color> タグを span に変換（XSS 安全） */
export function colorizeHoyoText(s) {
  if (!s) return '';
  const escaped = String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
  return escaped
    .replace(/&lt;color=#([0-9A-Fa-f]{3,8})&gt;/g, (_, hex) => `<span style="color:#${hex}">`)
    .replace(/&lt;\/color&gt;/g, '</span>');
}

/** sets[] → Map<name_ja, {two_pc_effect, four_pc_effect}> */
export function setsByName(sets) {
  const m = new Map();
  for (const s of (sets || [])) {
    if (s?.name_ja) m.set(s.name_ja, s);
  }
  return m;
}

/**
 * セット名 + ホバーで効果ポップオーバーを表示する HTML を返す。
 * @param {string} name - セット名（表示テキスト）
 * @param {object|null} setMaster - {two_pc_effect, four_pc_effect}
 * @param {object} opts - { extraClass?, suffix? }
 */
export function setNameWithPopover(name, setMaster, opts = {}) {
  const escName = String(name || '-')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const suffix = opts.suffix || '';
  const extra = opts.extraClass || '';
  if (!setMaster || (!setMaster.two_pc_effect && !setMaster.four_pc_effect)) {
    return `<span class="set-name ${extra}">${escName}${suffix}</span>`;
  }
  const two = setMaster.two_pc_effect ? colorizeHoyoText(setMaster.two_pc_effect) : '';
  const four = setMaster.four_pc_effect ? colorizeHoyoText(setMaster.four_pc_effect) : '';
  return `
    <span class="set-name has-popover ${extra}">
      ${escName}${suffix}
      <span class="set-effect-popover" role="tooltip">
        <div class="popover-title">${escName}</div>
        ${two ? `<div class="popover-row"><span class="popover-tag">2pc</span> ${two}</div>` : ''}
        ${four ? `<div class="popover-row"><span class="popover-tag">4pc</span> ${four}</div>` : ''}
      </span>
    </span>
  `;
}

export const HOYOLAB_REGIONS = [
  { value: 'prod_gf_jp', label: '日本（TW/HK/MO/JP）' },
  { value: 'prod_gf_us', label: '北米' },
  { value: 'prod_gf_eu', label: '欧州' },
  { value: 'prod_gf_sg', label: 'アジア' },
];
