/** Extract page — AI 生成 PNG のメタデータ（プロンプト等）を抽出する。
 *
 *  対応形式:
 *    - ComfyUI: tEXt/iTXt の "prompt" / "workflow" JSON
 *    - A1111 系: tEXt/iTXt の "parameters" 自然言語テキスト
 *
 *  実装は pure JS。zTXt（deflate 圧縮）は依存を増やさないため未対応。
 */
import { toast } from '../app.js';
import { esc, stashSet } from '../lib/common.js';

let lastResult = null;   // { source, positive, negative, params, model, raw }
let lastFileName = '';
let lastImageDataUrl = '';

function $(id) { return document.getElementById(id); }

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<section class="card imggen-section">
  <div class="imggen-header">
    <h3>🔍 Extract — PNG メタデータからプロンプト抽出</h3>
  </div>

  <div id="ex-drop" class="ex-dropzone">
    <div class="ex-dropzone-inner">
      <div class="ex-dropzone-icon">📥</div>
      <div class="ex-dropzone-msg">
        PNG をここにドラッグ&ドロップ<br>
        <span class="text-xs text-muted">または</span>
      </div>
      <button id="ex-pick" class="btn btn-primary btn-sm">ファイル選択</button>
      <input id="ex-file" type="file" accept="image/png" style="display:none;">
      <div class="ex-dropzone-note">
        ComfyUI / A1111 (Stable Diffusion WebUI) 形式に対応 / PNG のみ
      </div>
    </div>
  </div>

  <div id="ex-result" style="margin-top: 1rem;"></div>
</section>
`;
}

// ============================================================
// PNG chunk parser
// ============================================================
const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

function isPng(bytes) {
  if (bytes.length < 8) return false;
  for (let i = 0; i < 8; i++) if (bytes[i] !== PNG_SIG[i]) return false;
  return true;
}

/** PNG の text 系チャンク（tEXt / iTXt）を { key: value, ... } で返す。
 *  同一キーが複数あれば最後勝ち（実例ではほぼ単一）。
 */
function parsePngText(buffer) {
  const bytes = new Uint8Array(buffer);
  if (!isPng(bytes)) throw new Error('PNG ではありません');
  const view = new DataView(buffer);
  const out = {};
  let p = 8;
  const td = new TextDecoder('utf-8', { fatal: false });
  const td_latin1 = new TextDecoder('latin1');
  while (p + 8 <= bytes.length) {
    const len = view.getUint32(p);
    const type = String.fromCharCode(bytes[p+4], bytes[p+5], bytes[p+6], bytes[p+7]);
    const dataStart = p + 8;
    const dataEnd = dataStart + len;
    if (dataEnd + 4 > bytes.length) break;

    if (type === 'tEXt') {
      const data = bytes.subarray(dataStart, dataEnd);
      const nul = data.indexOf(0);
      if (nul > 0) {
        const key = td_latin1.decode(data.subarray(0, nul));
        const val = td_latin1.decode(data.subarray(nul + 1));
        out[key] = val;
      }
    } else if (type === 'iTXt') {
      // iTXt: keyword\0 compressionFlag(1) compressionMethod(1) langTag\0 translatedKey\0 text(UTF-8)
      const data = bytes.subarray(dataStart, dataEnd);
      let i = data.indexOf(0);
      if (i < 0) { p = dataEnd + 4; continue; }
      const key = td_latin1.decode(data.subarray(0, i));
      i++;
      const compFlag = data[i++];
      i++; // compMethod
      const langEnd = data.indexOf(0, i);
      if (langEnd < 0) { p = dataEnd + 4; continue; }
      i = langEnd + 1;
      const transEnd = data.indexOf(0, i);
      if (transEnd < 0) { p = dataEnd + 4; continue; }
      i = transEnd + 1;
      if (compFlag === 0) {
        out[key] = td.decode(data.subarray(i));
      }
      // compFlag !== 0 (zTXt 同様 deflate) は依存追加を避けるためスキップ
    } else if (type === 'IEND') {
      break;
    }
    p = dataEnd + 4; // skip CRC
  }
  return out;
}

// ============================================================
// ComfyUI prompt JSON parser
// ============================================================
/** ComfyUI workflow JSON ({ node_id: { class_type, inputs } }) から
 *  生成パラメタを抽出する。 KSampler / KSamplerAdvanced 系を起点に辿る。
 */
function parseComfyPrompt(promptJson) {
  let g;
  try { g = typeof promptJson === 'string' ? JSON.parse(promptJson) : promptJson; }
  catch { return null; }
  if (!g || typeof g !== 'object') return null;

  const isSampler = (cls) => /KSampler|KSamplerAdvanced|SamplerCustom/i.test(cls || '');
  const samplers = [];
  for (const [nid, node] of Object.entries(g)) {
    if (node && isSampler(node.class_type)) samplers.push([nid, node]);
  }
  // 一番大きい node id を採用（後段の KSampler を優先）
  samplers.sort((a, b) => Number(b[0]) - Number(a[0]));
  const [, ks] = samplers[0] || [null, null];

  const result = { positive: '', negative: '', params: {}, model: '' };

  const traceText = (link) => {
    if (!Array.isArray(link) || link.length < 1) return '';
    const node = g[String(link[0])];
    if (!node) return '';
    if (/CLIPTextEncode/i.test(node.class_type) && typeof node.inputs?.text === 'string') {
      return node.inputs.text;
    }
    // 文字列 inputs.text を持つ任意ノードに対応
    if (typeof node.inputs?.text === 'string') return node.inputs.text;
    return '';
  };

  if (ks) {
    const ins = ks.inputs || {};
    result.positive = traceText(ins.positive);
    result.negative = traceText(ins.negative);
    if (typeof ins.steps === 'number') result.params.STEPS = ins.steps;
    if (typeof ins.cfg === 'number') result.params.CFG = ins.cfg;
    if (typeof ins.sampler_name === 'string') result.params.SAMPLER = ins.sampler_name;
    if (typeof ins.scheduler === 'string') result.params.SCHEDULER = ins.scheduler;
    if (typeof ins.seed === 'number') result.params.SEED = ins.seed;
    else if (typeof ins.noise_seed === 'number') result.params.SEED = ins.noise_seed;
  }

  // EmptyLatentImage / EmptySD3LatentImage 等から W/H
  for (const node of Object.values(g)) {
    if (!node) continue;
    if (/EmptyLatentImage|EmptySD3LatentImage|EmptyHunyuanLatentImage/i.test(node.class_type || '')) {
      const w = node.inputs?.width, h = node.inputs?.height;
      if (typeof w === 'number') result.params.WIDTH = w;
      if (typeof h === 'number') result.params.HEIGHT = h;
      break;
    }
  }
  // Checkpoint / Model loader
  for (const node of Object.values(g)) {
    if (!node) continue;
    if (/CheckpointLoader|UNETLoader/i.test(node.class_type || '')) {
      result.model = node.inputs?.ckpt_name || node.inputs?.unet_name || '';
      if (result.model) break;
    }
  }
  return result;
}

// ============================================================
// A1111 parameters parser
// ============================================================
/** A1111 形式 "parameters" テキストを { positive, negative, params, model } に分解。
 *
 *  典型例:
 *    masterpiece, 1girl
 *    Negative prompt: blurry, lowres
 *    Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 12345, Size: 512x768, Model: foo
 */
function parseA1111(text) {
  if (typeof text !== 'string' || !text.trim()) return null;
  const result = { positive: '', negative: '', params: {}, model: '' };

  const negIdx = text.indexOf('\nNegative prompt:');
  // Steps: ... 行を最終行から検出
  const stepsMatch = text.match(/(^|\n)(Steps:[^\n]+)$/);
  let head = text;
  let metaLine = '';
  if (stepsMatch) {
    head = text.slice(0, stepsMatch.index + (stepsMatch[1] ? 1 : 0)).replace(/\n+$/, '');
    metaLine = stepsMatch[2];
  }
  if (negIdx >= 0 && negIdx < head.length) {
    result.positive = head.slice(0, negIdx).trim();
    result.negative = head.slice(negIdx + '\nNegative prompt:'.length).trim();
  } else {
    result.positive = head.trim();
  }

  if (metaLine) {
    // "Key: value" を ", " で区切る。値中の "," には対応しないが大半のケースで OK
    const parts = metaLine.split(/,\s+(?=[A-Z][\w \-]*:\s)/);
    const meta = {};
    for (const p of parts) {
      const m = p.match(/^([\w \-]+?):\s*(.*)$/);
      if (!m) continue;
      meta[m[1].trim()] = m[2].trim();
    }
    if (meta['Steps']) result.params.STEPS = Number(meta['Steps']);
    if (meta['CFG scale']) result.params.CFG = Number(meta['CFG scale']);
    if (meta['Sampler']) result.params.SAMPLER = meta['Sampler'];
    if (meta['Schedule type']) result.params.SCHEDULER = meta['Schedule type'];
    if (meta['Seed']) result.params.SEED = Number(meta['Seed']);
    if (meta['Size']) {
      const sz = meta['Size'].split('x');
      if (sz.length === 2) {
        result.params.WIDTH = Number(sz[0]);
        result.params.HEIGHT = Number(sz[1]);
      }
    }
    if (meta['Model']) result.model = meta['Model'];
    result.params._raw_meta = meta;
  }
  return result;
}

// ============================================================
// File handling
// ============================================================
async function handleFile(file) {
  if (!file) return;
  if (!/\.png$/i.test(file.name) && file.type !== 'image/png') {
    toast('PNG ファイルのみ対応です', 'error');
    return;
  }
  lastFileName = file.name;
  const buf = await file.arrayBuffer();
  let texts;
  try { texts = parsePngText(buf); }
  catch (e) {
    toast(`PNG 解析失敗: ${e.message}`, 'error');
    return;
  }

  // ComfyUI 優先 → A1111
  let result = null;
  if (texts['prompt']) {
    const r = parseComfyPrompt(texts['prompt']);
    if (r) {
      r.source = 'comfyui';
      r.raw = texts;
      result = r;
    }
  }
  if (!result && texts['parameters']) {
    const r = parseA1111(texts['parameters']);
    if (r) {
      r.source = 'a1111';
      r.raw = texts;
      result = r;
    }
  }
  if (!result) {
    // 何も見つからなかった場合でも生 text は表示
    result = { source: 'unknown', positive: '', negative: '', params: {}, model: '', raw: texts };
  }

  lastResult = result;
  // サムネイル用に dataURL も作る（小さい PNG 想定）
  lastImageDataUrl = await fileToDataUrl(file);
  renderResult();
}

function fileToDataUrl(file) {
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ''));
    r.onerror = () => resolve('');
    r.readAsDataURL(file);
  });
}

// ============================================================
// Result render
// ============================================================
function renderResult() {
  const el = $('ex-result');
  if (!el) return;
  if (!lastResult) { el.innerHTML = ''; return; }

  const r = lastResult;
  const params = r.params || {};
  const paramRows = Object.entries(params)
    .filter(([k]) => !k.startsWith('_'))
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(String(v))}</td></tr>`)
    .join('') || '<tr><td colspan="2" class="text-muted">（なし）</td></tr>';

  const rawKeys = Object.keys(r.raw || {});
  const rawRows = rawKeys.map(k => {
    const v = String(r.raw[k] ?? '');
    const short = v.length > 800 ? v.slice(0, 800) + ' …' : v;
    return `<details class="ex-raw-item"><summary>${esc(k)} <span class="text-xs text-muted">(${v.length} chars)</span></summary><pre>${esc(short)}</pre></details>`;
  }).join('') || '<div class="text-muted">テキスト系チャンクなし</div>';

  el.innerHTML = `
<div class="ex-result-grid">
  <div class="ex-thumb-col">
    ${lastImageDataUrl ? `<img src="${esc(lastImageDataUrl)}" class="ex-thumb" alt="">` : ''}
    <div class="ex-fname">${esc(lastFileName)}</div>
    <div class="ex-source-badge">source: <strong>${esc(r.source)}</strong></div>
    ${r.model ? `<div class="ex-model">model: <strong>${esc(r.model)}</strong></div>` : ''}
  </div>
  <div class="ex-info-col">
    <div class="ex-section">
      <div class="ex-label">Positive</div>
      <pre class="ex-prompt">${esc(r.positive || '(empty)')}</pre>
    </div>
    <div class="ex-section">
      <div class="ex-label">Negative</div>
      <pre class="ex-prompt">${esc(r.negative || '(empty)')}</pre>
    </div>
    <div class="ex-section">
      <div class="ex-label">Parameters</div>
      <table class="ex-params"><tbody>${paramRows}</tbody></table>
    </div>
    <div class="ex-actions">
      <button id="ex-to-gen" class="btn btn-primary btn-sm">🎨 この設定で生成へ</button>
      <button id="ex-copy-pos" class="btn btn-sm">Positive コピー</button>
      <button id="ex-copy-neg" class="btn btn-sm">Negative コピー</button>
    </div>
    <div class="ex-section">
      <div class="ex-label">Raw text chunks</div>
      ${rawRows}
    </div>
  </div>
</div>
`;

  $('ex-to-gen')?.addEventListener('click', handleToGenerate);
  $('ex-copy-pos')?.addEventListener('click', () => copy(r.positive));
  $('ex-copy-neg')?.addEventListener('click', () => copy(r.negative));
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text || '');
    toast('コピーしました', 'success');
  } catch {
    toast('コピー失敗', 'error');
  }
}

function handleToGenerate() {
  if (!lastResult) return;
  stashSet({
    source: 'extract',
    positive: lastResult.positive || '',
    negative: lastResult.negative || '',
    params: lastResult.params || {},
  });
  location.hash = '#/generate?prefill=extract';
  toast('Generate に取り込みました', 'info');
}

// ============================================================
// Drop / pick wiring
// ============================================================
function bindDrop() {
  const dz = $('ex-drop');
  const file = $('ex-file');
  const pick = $('ex-pick');
  if (!dz || !file || !pick) return;

  pick.addEventListener('click', () => file.click());
  file.addEventListener('change', () => {
    const f = file.files?.[0];
    if (f) handleFile(f);
    file.value = '';
  });

  ['dragenter', 'dragover'].forEach(ev => {
    dz.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      dz.classList.add('dragover');
    });
  });
  ['dragleave', 'drop'].forEach(ev => {
    dz.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      dz.classList.remove('dragover');
    });
  });
  dz.addEventListener('drop', (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) handleFile(f);
  });
}

let _pasteBound = false;
function onPaste(e) {
  if (location.hash.split('?')[0] !== '#/extract') return;
  const items = e.clipboardData?.items || [];
  for (const it of items) {
    if (it.kind === 'file' && it.type === 'image/png') {
      const f = it.getAsFile();
      if (f) { handleFile(f); break; }
    }
  }
}

// ============================================================
// Lifecycle
// ============================================================
export async function mount() {
  bindDrop();
  if (!_pasteBound) {
    document.addEventListener('paste', onPaste);
    _pasteBound = true;
  }
  if (lastResult) renderResult();
}
