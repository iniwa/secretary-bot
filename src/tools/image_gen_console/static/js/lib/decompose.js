/** クライアントサイドのプロンプト逆算。
 *  最終 positive/negative 文字列とセクション一覧から、
 *  「完全一致したセクションID集合」と「ユーザー入力欄に流す残余タグ」を復元する。
 *
 *  方針:
 *    - セクションの positive/negative タグが最終出力に「全部」含まれていれば候補
 *    - positive 内の出現順でセクションを並べ、未消費タグを供給するものから採用
 *    - 採用セクションが消費したタグを除いた残余を userPositive / userNegative に
 *
 *  サーバ側 section_composer の挙動 (重複先勝ち / weight 記法吸収) と整合。
 */

const WEIGHTED_RE = /^\((?<tag>.+?)\s*(?::\s*(?<w>[-+]?\d*\.?\d+))?\)$/;

function normKey(raw) {
  if (!raw) return '';
  let s = String(raw).trim().toLowerCase().replace(/\s+/g, ' ');
  const m = s.match(WEIGHTED_RE);
  if (m) s = (m.groups?.tag || '').trim();
  return s;
}

function splitTagsRaw(text) {
  if (!text) return [];
  return String(text).split(/,(?![^(]*\))/).map(t => t.trim()).filter(Boolean);
}

function tagKeysOf(text) {
  return splitTagsRaw(text).map(normKey).filter(Boolean);
}

/**
 * @param {object} args
 * @param {string} args.positive 最終 positive 文字列
 * @param {string} args.negative 最終 negative 文字列
 * @param {Array<{id:number, positive?:string, negative?:string}>} args.sections 全セクション
 * @returns {{section_ids:number[], userPositive:string, userNegative:string}}
 */
export function decomposePromptClient({ positive = '', negative = '', sections = [] }) {
  const posOutKeys = new Set(tagKeysOf(positive));
  const negOutKeys = new Set(tagKeysOf(negative));

  const candidates = [];
  for (const s of sections) {
    const posKeys = tagKeysOf(s.positive || '');
    const negKeys = tagKeysOf(s.negative || '');
    if (posKeys.length === 0 && negKeys.length === 0) continue;
    const posOk = posKeys.every(k => posOutKeys.has(k));
    const negOk = negKeys.every(k => negOutKeys.has(k));
    if (!(posOk && negOk)) continue;
    candidates.push({ id: s.id, posKeys, negKeys, weight: posKeys.length + negKeys.length });
  }

  // positive の出現順で候補を並べる（同位置はタグ数の多い方を優先）
  const posOrder = tagKeysOf(positive);
  const firstIdx = new Map();
  posOrder.forEach((k, i) => { if (!firstIdx.has(k)) firstIdx.set(k, i); });
  candidates.sort((a, b) => {
    const ai = a.posKeys.length ? (firstIdx.get(a.posKeys[0]) ?? Number.MAX_SAFE_INTEGER) : Number.MAX_SAFE_INTEGER;
    const bi = b.posKeys.length ? (firstIdx.get(b.posKeys[0]) ?? Number.MAX_SAFE_INTEGER) : Number.MAX_SAFE_INTEGER;
    if (ai !== bi) return ai - bi;
    return b.weight - a.weight;
  });

  const consumedPos = new Set();
  const consumedNeg = new Set();
  const accepted = [];
  for (const c of candidates) {
    const contributesPos = c.posKeys.some(k => !consumedPos.has(k));
    const contributesNeg = c.negKeys.some(k => !consumedNeg.has(k));
    if (!contributesPos && !contributesNeg) continue;
    accepted.push(c.id);
    c.posKeys.forEach(k => consumedPos.add(k));
    c.negKeys.forEach(k => consumedNeg.add(k));
  }

  const userPos = splitTagsRaw(positive).filter(t => !consumedPos.has(normKey(t))).join(', ');
  const userNeg = splitTagsRaw(negative).filter(t => !consumedNeg.has(normKey(t))).join(', ');

  return { section_ids: accepted, userPositive: userPos, userNegative: userNeg };
}
