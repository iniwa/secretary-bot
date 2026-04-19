/** Wildcard / Dynamic Prompts のクライアント側展開。
 *
 *  サーバ側 src/units/image_gen/wildcard_expander.py と同じロジックを保つ。
 *  真のソースは常にサーバ。ここはバッチループ内展開とプレビュー用。
 *
 *  ## 記法
 *  - {a|b|c}       均等ランダム
 *  - {2::a|1::b}   重み付き（重みは非負の数値）
 *  - {1-5}         整数ランダム (inclusive、両端入替え可)
 *  - __name__      wildcard_files(name) から 1 行ランダム
 *                   (`#` 行・空行はコメント)
 *  - \{ \| \} \: \\ \_ 等、任意 1 文字のエスケープ
 *
 *  ## 方針
 *  - 入れ子は非対応（置換結果は再スキャンしない、`{` の最初の `}` で閉じる）
 *  - 未定義ファイルはリテラルを残し warnings に記録
 *  - rng_seed を渡すと決定的展開
 */

import { api } from '../api.js';

// ---------- RNG ----------

/** seed から決定的な PRNG を返す（seed=null なら Math.random 相当）。
 *  Mulberry32 ベース: 十分に一様かつ軽量。
 */
function makeRng(seed) {
  if (seed === null || seed === undefined) {
    return {
      random: () => Math.random(),
      randint: (lo, hi) => lo + Math.floor(Math.random() * (hi - lo + 1)),
      randrange: (n) => Math.floor(Math.random() * n),
      choice: (arr) => arr[Math.floor(Math.random() * arr.length)],
    };
  }
  let t = (seed >>> 0) || 1;  // 0 は避ける
  const next = () => {
    t = (t + 0x6D2B79F5) >>> 0;
    let x = t;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
  return {
    random: next,
    randint: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)),
    randrange: (n) => Math.floor(next() * n),
    choice: (arr) => arr[Math.floor(next() * arr.length)],
  };
}

// ---------- パーサ補助 ----------

const FILE_RE = /^__([A-Za-z0-9_.\-]+)__/;
const WEIGHTED_RE = /^\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*::\s*(.*)$/;
const RANGE_RE = /^\s*(-?\d+)\s*-\s*(-?\d+)\s*$/;

function unescape(s) {
  let out = '';
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '\\' && i + 1 < s.length) {
      out += s[i + 1];
      i++;
    } else {
      out += c;
    }
  }
  return out;
}

function splitTopLevelPipe(inner) {
  const parts = [];
  let cur = '';
  for (let i = 0; i < inner.length; i++) {
    const c = inner[i];
    if (c === '\\' && i + 1 < inner.length) {
      cur += c + inner[i + 1];
      i++;
      continue;
    }
    if (c === '|') {
      parts.push(cur);
      cur = '';
      continue;
    }
    cur += c;
  }
  parts.push(cur);
  return parts;
}

function findMatchingBrace(text, openIdx) {
  for (let i = openIdx + 1; i < text.length; i++) {
    const c = text[i];
    if (c === '\\' && i + 1 < text.length) { i++; continue; }
    if (c === '}') return i;
  }
  return -1;
}

function pickWeighted(rng, rawAlts) {
  const weights = [];
  const bodies = [];
  for (const a of rawAlts) {
    const stripped = a.trim();
    const m = WEIGHTED_RE.exec(stripped);
    if (m) {
      let w = parseFloat(m[1]);
      if (!isFinite(w)) w = 1.0;
      weights.push(Math.max(0, w));
      bodies.push(unescape(m[2]));
    } else {
      weights.push(1.0);
      bodies.push(unescape(stripped));
    }
  }
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0) return bodies[rng.randrange(bodies.length)];
  let r = rng.random() * total;
  let acc = 0;
  for (let i = 0; i < weights.length; i++) {
    acc += weights[i];
    if (r < acc) return bodies[i];
  }
  return bodies[bodies.length - 1];
}

function expandBrace(content, rng) {
  const token = '{' + content + '}';
  const mr = RANGE_RE.exec(content);
  if (mr) {
    const a = parseInt(mr[1], 10);
    const b = parseInt(mr[2], 10);
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    const picked = String(rng.randint(lo, hi));
    return { picked, choice: { token, kind: 'range', picked, source: null } };
  }
  const alts = splitTopLevelPipe(content);
  if (alts.length === 0 || alts.every(a => a.trim() === '')) {
    return { picked: '', choice: null };
  }
  const picked = pickWeighted(rng, alts);
  return { picked, choice: { token, kind: 'alt', picked, source: null } };
}

function pickFileLine(rng, content) {
  const lines = [];
  for (const raw of (content || '').split(/\r?\n/)) {
    const s = raw.trim();
    if (!s || s.startsWith('#')) continue;
    lines.push(s);
  }
  if (lines.length === 0) return null;
  return rng.choice(lines);
}

// ---------- 公開 API ----------

/** テンプレートを 1 回展開する。
 *  @param {string} template
 *  @param {object} opts
 *  @param {Object<string,string>=} opts.files   name → content の辞書
 *  @param {number=} opts.rngSeed                決定的展開したいとき
 *  @returns {{ text: string, choices: Array, warnings: string[] }}
 */
export function expand(template, { files = {}, rngSeed = null } = {}) {
  const rng = makeRng(rngSeed);
  const choices = [];
  const warnings = [];
  let out = '';
  const n = template.length;
  let i = 0;

  while (i < n) {
    const c = template[i];

    // Escape
    if (c === '\\' && i + 1 < n) {
      out += template[i + 1];
      i += 2;
      continue;
    }

    // Brace
    if (c === '{') {
      const close = findMatchingBrace(template, i);
      if (close >= 0) {
        const inner = template.slice(i + 1, close);
        const { picked, choice } = expandBrace(inner, rng);
        out += picked;
        if (choice) choices.push(choice);
        i = close + 1;
        continue;
      }
      // 閉じられない `{` はリテラル
    }

    // File ref
    if (c === '_' && i + 1 < n && template[i + 1] === '_') {
      const rest = template.slice(i);
      const m = FILE_RE.exec(rest);
      if (m) {
        const name = m[1];
        const full = m[0];
        if (Object.prototype.hasOwnProperty.call(files, name)) {
          const picked = pickFileLine(rng, files[name]);
          if (picked !== null && picked !== undefined) {
            out += picked;
            choices.push({ token: full, kind: 'file', picked, source: `file:${name}` });
            i += full.length;
            continue;
          }
          warnings.push(`wildcard file \`${name}\` に有効な行が無い`);
        } else {
          warnings.push(`wildcard file \`${name}\` が未定義`);
        }
        out += full;
        i += full.length;
        continue;
      }
    }

    out += c;
    i++;
  }

  return { text: out, choices, warnings };
}

/** テンプレートに wildcard トークンが含まれるかの簡易判定。
 *  ファイル一覧を事前取得する必要があるかの判断に使う（無ければ fetch 不要）。
 */
export function hasWildcardToken(template) {
  if (!template) return false;
  // `\{` をリテラル除外した上で `{` を検出
  // ざっくり: 未エスケープの `{` または `__name__` のどちらか
  let i = 0;
  const n = template.length;
  while (i < n) {
    const c = template[i];
    if (c === '\\' && i + 1 < n) { i += 2; continue; }
    if (c === '{') return true;
    if (c === '_' && i + 1 < n && template[i + 1] === '_') {
      if (FILE_RE.test(template.slice(i))) return true;
    }
    i++;
  }
  return false;
}

// ---------- ファイル辞書の取得・キャッシュ ----------

let _filesCache = null;

/** /api/generation/wildcards/bulk から name→content を取得してキャッシュ。*/
export async function loadWildcardFiles({ force = false } = {}) {
  if (_filesCache && !force) return _filesCache;
  try {
    const res = await api('/api/generation/wildcards/bulk');
    _filesCache = res?.files || {};
  } catch (err) {
    console.error('wildcard bulk load failed', err);
    _filesCache = {};
  }
  return _filesCache;
}

export function invalidateWildcardCache() {
  _filesCache = null;
}
