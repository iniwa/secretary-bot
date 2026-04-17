/** クライアントサイドのプロンプト合成プレビュー。
 *  サーバ側 src/units/image_gen/section_composer.py と同じロジックを保つ。
 */

const WEIGHTED_RE = /^\((?<tag>.+?)\s*(?::\s*(?<w>[-+]?\d*\.?\d+))?\)$/;

function normalizeKey(raw) {
  if (!raw) return '';
  let s = String(raw).trim().toLowerCase().replace(/\s+/g, ' ');
  const m = s.match(WEIGHTED_RE);
  if (m) s = (m.groups?.tag || '').trim();
  return s;
}

function splitTags(text) {
  if (!text) return [];
  return String(text).split(/,(?![^(]*\))/).map(t => t.trim()).filter(Boolean);
}

/** rows: [{category_key, name, positive, negative, ...}]
 *  returns: { positive, negative, warnings[], dropped[], tags[] }
 */
export function composePromptClient(rows, {
  userPositive = null,
  userNegative = null,
  userPosition = 'tail',
} = {}) {
  const warnings = [];
  const dropped = [];

  const posBuckets = []; // {key, tags[]}
  const negBuckets = [];
  for (const r of rows || []) {
    const catKey = r.category_key || '';
    posBuckets.push({ catKey, sectionName: r.name || '', tags: splitTags(r.positive || '') });
    negBuckets.push({ catKey, sectionName: r.name || '', tags: splitTags(r.negative || '') });
  }

  // user prompts split
  const userPosTags = splitTags(userPositive || '');
  const userNegTags = splitTags(userNegative || '');

  function assemble(buckets, userTags) {
    const mode = userPosition || 'tail';
    let order;
    if (mode === 'head') {
      order = [{ tags: userTags, source: '__user__' }, ...buckets.map(b => ({ tags: b.tags, source: `${b.catKey}/${b.sectionName}` }))];
    } else if (mode === 'tail') {
      order = [...buckets.map(b => ({ tags: b.tags, source: `${b.catKey}/${b.sectionName}` })), { tags: userTags, source: '__user__' }];
    } else if (mode.startsWith('section:')) {
      const target = mode.slice('section:'.length);
      order = [];
      let inserted = false;
      for (const b of buckets) {
        if (!inserted && b.catKey === target) {
          order.push({ tags: userTags, source: '__user__' });
          inserted = true;
        }
        order.push({ tags: b.tags, source: `${b.catKey}/${b.sectionName}` });
      }
      if (!inserted) {
        // 未知カテゴリ → tail fallback
        warnings.push(`user_position "${mode}" のカテゴリが見つからず末尾に付与`);
        order.push({ tags: userTags, source: '__user__' });
      }
    } else {
      order = [...buckets.map(b => ({ tags: b.tags, source: `${b.catKey}/${b.sectionName}` })), { tags: userTags, source: '__user__' }];
    }
    const seen = new Map(); // normKey -> {tag, source, weight}
    const outTags = [];
    for (const part of order) {
      for (const t of part.tags) {
        const key = normalizeKey(t);
        if (!key) continue;
        if (seen.has(key)) {
          const prev = seen.get(key);
          if (prev.tag !== t) {
            warnings.push(`重複: "${t}" (先勝ち: "${prev.tag}" from ${prev.source})`);
          }
          dropped.push(key);
          continue;
        }
        seen.set(key, { tag: t, source: part.source });
        outTags.push(t);
      }
    }
    return outTags;
  }

  const posTags = assemble(posBuckets, userPosTags);
  const negTags = assemble(negBuckets, userNegTags);

  return {
    positive: posTags.join(', '),
    negative: negTags.join(', '),
    warnings,
    dropped,
    tags: posTags,
  };
}
