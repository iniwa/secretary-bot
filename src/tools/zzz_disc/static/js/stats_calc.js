/** ZZZ ステータス逆算・再計算ユーティリティ。
 *
 * HoYoLAB 同期時点の {base, add, final} とディスク・音動機の情報から、
 * ディスク/音動機入れ替え時のステータスを再計算する。
 *
 * 前提:
 *   final = base + add
 *   add   = disc_contribution + w_engine_contribution + residual
 *   residual = コアスキル / 陣営ボーナス / 常時バフ等、ここでは不可視な加算
 *
 * 同期直後は上式が厳密に成り立つので、そこから residual を逆算してキャッシュし、
 * ディスク差し替え時には residual を固定して再計算する。
 */

const PARSE_NUM_RE = /[-\d.]+/;

/** "30%" → 30, "3500" → 3500, "" → 0 */
export function parseStatNumber(v) {
  if (v == null || v === '') return 0;
  const s = String(v);
  const m = s.match(PARSE_NUM_RE);
  if (!m) return 0;
  const n = parseFloat(m[0]);
  return Number.isFinite(n) ? n : 0;
}

/** stat が % 表示か（base または final から判定） */
function isPercentBaseStat(statEntry) {
  if (!statEntry || typeof statEntry !== 'object') return false;
  if (typeof statEntry.base === 'string' && statEntry.base.includes('%')) return true;
  if (typeof statEntry.final === 'string' && statEntry.final.includes('%')) return true;
  return false;
}

/** base が空の stat（final のみ）かを判定 */
function isFinalOnly(v) {
  return !(v && typeof v === 'object' && typeof v.base === 'string' && v.base !== '');
}

/** stat entry からベース数値を取り出す（base が空なら final を base とみなす） */
function baseNumberOf(v) {
  if (!v || typeof v !== 'object') return 0;
  if (typeof v.base === 'string' && v.base !== '') return parseStatNumber(v.base);
  if (v.final != null) return parseStatNumber(v.final);
  return 0;
}

/** 元の add（base が空のときは 0 相当とみなす） */
function originalAddOf(v) {
  if (!v || typeof v !== 'object') return 0;
  if (typeof v.base === 'string' && v.base !== '') return parseStatNumber(v.add);
  return 0;
}

/** 値を元の表示形式（% or 整数）に整形 */
function formatLikeOriginal(value, original) {
  const isPct = typeof original === 'string' && original.includes('%');
  if (isPct) return `${value.toFixed(1)}%`;
  if (Math.abs(value) < 10) return value.toFixed(2);
  return String(Math.round(value));
}

/**
 * 1つのディスク寄与 (name, value) を stat accumulator に加算する。
 * @param {string} name - "HP", "HP%", "会心率%", "攻撃力", 等
 * @param {number} value - 数値
 * @param {Object} baseStats - build.stats（base 参照のため）
 * @param {Object} acc - {stat_key: number} に加算する
 */
function addOneContribution(name, value, baseStats, acc) {
  if (!name || !Number.isFinite(value) || value === 0) return;
  const isPct = name.endsWith('%');
  const cleanName = isPct ? name.slice(0, -1) : name;
  const baseEntry = baseStats?.[cleanName];
  const baseIsPercent = isPercentBaseStat(baseEntry);

  let contrib;
  if (isPct && !baseIsPercent) {
    // 例: HP% 15 → HP base * 0.15
    const baseNum = parseStatNumber(baseEntry?.base);
    contrib = baseNum * value / 100;
  } else {
    // 常時 % stat (会心率%等) への直加算 / flat → flat の直加算
    contrib = value;
  }
  acc[cleanName] = (acc[cleanName] || 0) + contrib;
}

/** 全ディスク合計の寄与 {stat: value} を返す */
export function computeDiscAdd(slots, baseStats) {
  const acc = {};
  for (const s of slots || []) {
    const d = s?.disc;
    if (!d) continue;
    addOneContribution(d.main_stat_name, Number(d.main_stat_value) || 0, baseStats, acc);
    for (const sub of (d.sub_stats || [])) {
      addOneContribution(sub.name, Number(sub.value) || 0, baseStats, acc);
    }
  }
  return acc;
}

/** 音動機の寄与 {stat: value} を返す。main_properties(基礎攻撃力) + properties(副ステ) */
export function computeWEngineAdd(wEngine, baseStats) {
  const acc = {};
  if (!wEngine) return acc;
  for (const p of (wEngine.main_properties || [])) {
    addOneContribution(p.name, Number(p.value) || 0, baseStats, acc);
  }
  for (const p of (wEngine.properties || [])) {
    addOneContribution(p.name, Number(p.value) || 0, baseStats, acc);
  }
  return acc;
}

/**
 * build に residual (不可視加算) をキャッシュとして計算・保存する。
 * 同期直後のスロット・音動機・stats.add が整合している前提で、
 *   residual = stats.add - disc_add - weng_add
 * を一度だけ計算し、以降の再計算で使い回す。
 */
function ensureResidual(build) {
  if (build._residualAdd) return build._residualAdd;
  const stats = build.stats || {};
  const discAdd = computeDiscAdd(build.slots || [], stats);
  const wengAdd = computeWEngineAdd(build.w_engine, stats);
  const residual = {};
  for (const [name, v] of Object.entries(stats)) {
    if (name.startsWith('_')) continue;
    if (!v || typeof v !== 'object' || !('final' in v)) continue;
    const curAdd = originalAddOf(v);
    residual[name] = curAdd - (discAdd[name] || 0) - (wengAdd[name] || 0);
  }
  // 内訳もキャッシュしておく（UI 表示・デバッグ用）
  build._residualAdd = residual;
  build._baselineDiscAdd = discAdd;
  build._baselineWengAdd = wengAdd;
  return residual;
}

/**
 * 現在の slots / w_engine から stats の {base, add, final} を再計算して返す。
 * build.stats は同期時スナップショットとして保持し、このコピーを壊さない。
 */
export function derivedStats(build) {
  const stats = build?.stats || {};
  if (!build) return stats;
  const residual = ensureResidual(build);
  const discAdd = computeDiscAdd(build.slots || [], stats);
  const wengAdd = computeWEngineAdd(build.w_engine, stats);
  const out = {};
  for (const [name, v] of Object.entries(stats)) {
    if (name.startsWith('_')) { out[name] = v; continue; }
    if (!v || typeof v !== 'object' || !('final' in v)) { out[name] = v; continue; }
    const baseNum = baseNumberOf(v);
    const origAdd = originalAddOf(v);
    const newAdd = (residual[name] || 0) + (discAdd[name] || 0) + (wengAdd[name] || 0);
    const newFinal = baseNum + newAdd;
    const finalOnly = isFinalOnly(v);
    // 元々 final のみだった stat は、ディスク差し替え差分だけを add として表示
    const displayAdd = finalOnly ? (newAdd - origAdd) : newAdd;
    const formatRef = v.add || v.final;
    const addStr = finalOnly && Math.abs(displayAdd) < 0.05
      ? ''
      : formatLikeOriginal(displayAdd, formatRef);
    out[name] = {
      ...v,
      add: addStr,
      base: finalOnly ? '' : v.base,
      final: formatLikeOriginal(newFinal, v.final),
      _disc_add: discAdd[name] || 0,
      _weng_add: wengAdd[name] || 0,
    };
  }
  return out;
}

/**
 * new_slots と new_weng に差し替えた場合の stats を返す（プレビュー用、非破壊）。
 * build は変更しない。
 */
export function previewStats(build, { newSlots, newWEngine } = {}) {
  const preview = {
    ...build,
    slots: newSlots !== undefined ? newSlots : build.slots,
    w_engine: newWEngine !== undefined ? newWEngine : build.w_engine,
    _residualAdd: build._residualAdd,  // キャッシュ継承
    _baselineDiscAdd: build._baselineDiscAdd,
    _baselineWengAdd: build._baselineWengAdd,
  };
  return derivedStats(preview);
}

/**
 * 差分用: 2つの stats dict を比較して {stat: delta} を返す。
 */
export function statsDelta(before, after) {
  const delta = {};
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  for (const k of keys) {
    if (k.startsWith('_')) continue;
    const b = before?.[k];
    const a = after?.[k];
    if (!b || !a || typeof b !== 'object' || typeof a !== 'object') continue;
    const bn = parseStatNumber(b.final);
    const an = parseStatNumber(a.final);
    if (Math.abs(an - bn) < 0.01) continue;
    delta[k] = {
      diff: an - bn,
      is_percent: typeof a.final === 'string' && a.final.includes('%'),
    };
  }
  return delta;
}
