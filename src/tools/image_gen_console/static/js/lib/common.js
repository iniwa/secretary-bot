/** Image Gen Console 共通ヘルパ（generate / jobs / gallery / prompts 共通）。*/

export function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function fmtTime(iso) {
  if (!iso) return '---';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return esc(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtDate(iso) {
  if (!iso) return '---';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return esc(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function statusBadgeClass(status) {
  switch (status) {
    case 'done':          return 'badge-success';
    case 'running':       return 'badge-info';
    case 'warming_cache': return 'badge-info';
    case 'dispatching':   return 'badge-info';
    case 'queued':        return 'badge-accent';
    case 'failed':        return 'badge-error';
    case 'cancelled':     return 'badge-muted';
    default:              return 'badge-muted';
  }
}

export function isTerminal(s) {
  return s === 'done' || s === 'failed' || s === 'cancelled';
}

/** 汎用ライトボックス。画像・動画・音声を kind で切替表示。
 *  item: { url, kind, job_id?, positive?, negative?, favorite?, tags? }
 *  opts.onReuse(item): 「この設定で再現」が押されたコールバック
 *  opts.onFavoriteToggle(item, next): ⭐ ボタン押下時のコールバック
 *  opts.onTagsEdit(item): 🏷 ボタン押下時のコールバック（呼び出し側でモーダル表示）
 */
export function openLightbox(item, opts = {}) {
  const el = document.createElement('div');
  el.className = 'imggen-lightbox';
  const kind = (item.kind || 'image');
  let media = '';
  if (kind === 'video') {
    media = `<video src="${esc(item.url)}" controls autoplay></video>`;
  } else if (kind === 'audio') {
    media = `<audio src="${esc(item.url)}" controls autoplay style="width:60vw"></audio>`;
  } else {
    media = `<img src="${esc(item.url)}" alt="">`;
  }
  const actions = [];
  if (opts.onFavoriteToggle) {
    const star = item.favorite ? '★' : '☆';
    actions.push(`<button data-act="fav" title="お気に入り" class="imggen-lb-star ${item.favorite ? 'on' : ''}">${star}</button>`);
  }
  if (opts.onTagsEdit) {
    actions.push(`<button data-act="tags" title="タグ編集">🏷 タグ</button>`);
  }
  if (opts.onExtract) {
    // Extract ページへ遷移して PNG メタデータ抽出を行うフック
    actions.push(`<button data-act="extract" title="Extract ページでプロンプトを抽出">📝 プロンプト → Extract</button>`);
  } else if (item.positive || item.negative) {
    actions.push(`<button data-act="prompt" title="プロンプトを表示">📝 プロンプト</button>`);
  }
  if (opts.onReuse) {
    actions.push(`<button data-act="reuse">この設定で再現</button>`);
  }
  actions.push(`<a href="${esc(item.url)}" target="_blank" rel="noopener" style="color:var(--text-primary);text-decoration:none;padding:0.3rem 0.8rem;border:1px solid var(--border);border-radius:4px;background:var(--bg-surface,#1d1d1d);font-size:0.75rem;">開く ↗</a>`);
  actions.push(`<button data-act="close">閉じる</button>`);
  const tagsLine = (item.tags && item.tags.length)
    ? `<div class="imggen-lb-tags">${item.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>`
    : '';
  el.innerHTML = `
    ${media}
    ${tagsLine}
    <div class="imggen-lightbox-actions">${actions.join('')}</div>
  `;
  const close = () => el.remove();
  el.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('button');
    if (btn?.dataset.act === 'close') return close();
    if (btn?.dataset.act === 'reuse') {
      opts.onReuse?.(item);
      return close();
    }
    if (btn?.dataset.act === 'fav') {
      ev.stopPropagation();
      const next = !item.favorite;
      const ok = await opts.onFavoriteToggle?.(item, next);
      if (ok !== false) {
        item.favorite = next;
        btn.textContent = next ? '★' : '☆';
        btn.classList.toggle('on', next);
      }
      return;
    }
    if (btn?.dataset.act === 'prompt') {
      ev.stopPropagation();
      openPromptModal(item);
      return;
    }
    if (btn?.dataset.act === 'extract') {
      ev.stopPropagation();
      opts.onExtract?.(item);
      return close();
    }
    if (btn?.dataset.act === 'tags') {
      ev.stopPropagation();
      opts.onTagsEdit?.(item, (newTags) => {
        item.tags = newTags;
        const cur = el.querySelector('.imggen-lb-tags');
        const html = newTags.length
          ? newTags.map(t => `<span class="tag">${esc(t)}</span>`).join('')
          : '';
        if (cur) {
          if (html) cur.innerHTML = html;
          else cur.remove();
        } else if (html) {
          const div = document.createElement('div');
          div.className = 'imggen-lb-tags';
          div.innerHTML = html;
          el.querySelector('.imggen-lightbox-actions')?.before(div);
        }
      });
      return;
    }
    if (ev.target === el) close();
  });
  document.addEventListener('keydown', function onKey(e) {
    if (e.key === 'Escape') {
      close();
      document.removeEventListener('keydown', onKey);
    }
  });
  document.body.appendChild(el);
  return close;
}

/** プロンプト文字列を section_composer の ",\n" 境界で断片に分割する。 */
function splitPromptFragments(text) {
  if (!text) return [];
  return String(text).split(/,\s*\n/).map(s => s.trim()).filter(Boolean);
}

/** プロンプト 1 ブロック（POSITIVE か NEGATIVE）の HTML を返す。
 *  - 既定はセクション断片カード表示（,\n 境界で分割）
 *  - ヘッダの「📄 生データ」トグルで生 <pre> 表示に切替
 *  - 「📋 コピー」で全文コピー
 *
 *  返り値: { html, bind(rootEl) } — bind で button などのイベントを接続する。
 */
export function buildPromptBlock(label, text) {
  const safeText = text || '';
  const fragments = splitPromptFragments(safeText);
  const id = `pb-${Math.random().toString(36).slice(2, 9)}`;
  const fragmentsHtml = fragments.length
    ? fragments.map(f => `<div class="imggen-prompt-frag">${esc(f)}</div>`).join('')
    : '<div class="text-muted">(empty)</div>';
  const html = `
    <div class="imggen-prompt-block" data-block="${id}">
      <div class="imggen-prompt-head">
        <span class="imggen-prompt-label">${esc(label)}</span>
        <span class="imggen-prompt-actions">
          <button data-act="toggle-raw" class="btn btn-sm" title="セクション断片 / 生データを切替">📄 生データ</button>
          <button data-act="copy" class="btn btn-sm">📋 コピー</button>
        </span>
      </div>
      <div class="imggen-prompt-body" data-mode="frag">
        <div class="imggen-prompt-frags">${fragmentsHtml}</div>
        <pre class="imggen-prompt-raw" hidden>${esc(safeText) || '<span class="text-muted">(empty)</span>'}</pre>
      </div>
    </div>
  `;
  function bind(rootEl) {
    const block = rootEl.querySelector(`[data-block="${id}"]`);
    if (!block) return;
    const body = block.querySelector('.imggen-prompt-body');
    const frags = block.querySelector('.imggen-prompt-frags');
    const raw = block.querySelector('.imggen-prompt-raw');
    const toggle = block.querySelector('[data-act="toggle-raw"]');
    const copy = block.querySelector('[data-act="copy"]');
    toggle?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const next = body.dataset.mode === 'frag' ? 'raw' : 'frag';
      body.dataset.mode = next;
      frags.hidden = (next === 'raw');
      raw.hidden = (next === 'frag');
      toggle.textContent = next === 'raw' ? '🧩 断片表示' : '📄 生データ';
    });
    copy?.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try { await navigator.clipboard.writeText(safeText); } catch { /* ignore */ }
    });
  }
  return { html, bind };
}

/** ライトボックスから開くプロンプト表示モーダル。
 *  positive / negative は section_composer の出力フォーマット（ ",\n" でセクション境界）
 *  を断片カードで表示。「📄 生データ」トグルで全文 pre 表示にも切替可能。
 */
function openPromptModal(item) {
  const overlay = document.createElement('div');
  overlay.className = 'imggen-lb-prompt-overlay';
  const posBlock = buildPromptBlock('POSITIVE', item.positive || '');
  const negBlock = buildPromptBlock('NEGATIVE', item.negative || '');
  overlay.innerHTML = `
    <div class="imggen-lb-prompt-card">
      <div class="imggen-lb-prompt-head">
        <span>📝 Prompt</span>
        <button data-act="close" class="btn btn-sm">×</button>
      </div>
      <div class="imggen-lb-prompt-body">
        ${posBlock.html}
        ${negBlock.html}
      </div>
    </div>
  `;
  posBlock.bind(overlay);
  negBlock.bind(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener('click', (ev) => {
    const btn = ev.target.closest('button');
    if (btn?.dataset.act === 'close') return close();
    if (ev.target === overlay) close();
  });
  function onKey(e) {
    if (e.key !== 'Escape') return;
    // 親 lightbox の Esc ハンドラより先に走らせて閉じ伝播を止める
    e.stopImmediatePropagation();
    close();
    document.removeEventListener('keydown', onKey, true);
  }
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(overlay);
}

/** タグ編集の簡易プロンプト。OK で新タグ配列を返す（カンマ区切り）。Cancel で null。 */
export function promptTags(currentTags) {
  const cur = (currentTags || []).join(', ');
  const input = window.prompt('タグ（カンマ区切り）', cur);
  if (input === null) return null;
  const out = [];
  const seen = new Set();
  for (const part of input.split(',')) {
    const s = part.trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

/** HTML5 drag で要素並び替えを可能にする。
 *  container: 子要素を並べる親。各子に draggable="true" が付与される。
 *  onReorder(order): 並び替え結果の data-key 配列を返す。
 */
export function makeSortable(container, onReorder) {
  if (!container) return;
  let dragging = null;
  container.querySelectorAll('[data-key]').forEach((el) => {
    el.setAttribute('draggable', 'true');
    el.addEventListener('dragstart', () => {
      dragging = el;
      el.classList.add('dragging');
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      dragging = null;
      const order = Array.from(container.querySelectorAll('[data-key]'))
        .map(n => n.dataset.key);
      onReorder?.(order);
    });
  });
  container.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (!dragging) return;
    const after = getDragAfter(container, e.clientX, e.clientY);
    if (after == null) container.appendChild(dragging);
    else container.insertBefore(dragging, after);
  });
}

function getDragAfter(container, x, y) {
  const els = [...container.querySelectorAll('[data-key]:not(.dragging)')];
  return els.reduce((closest, child) => {
    const rect = child.getBoundingClientRect();
    const offset = (y - rect.top - rect.height / 2);
    if (offset < 0 && offset > closest.offset) {
      return { offset, element: child };
    }
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
}

/** モーダル背景クリックで閉じる際、テキスト選択ドラッグの離し位置が
 *  背景上だった場合に意図せず閉じてしまう問題を回避する。
 *  mousedown の起点も背景でなければ閉じない。 */
export function bindModalBackdropClose(backdropEl, closeFn) {
  if (!backdropEl) return;
  let downOnBackdrop = false;
  backdropEl.addEventListener('mousedown', (e) => {
    downOnBackdrop = (e.target === backdropEl);
  });
  backdropEl.addEventListener('click', (e) => {
    if (downOnBackdrop && e.target === backdropEl) closeFn();
    downOnBackdrop = false;
  });
}

/** localStorage を介した簡易 stash（ページ間のプリセット受け渡し）。
 *  メイン WebGUI と同じキーを使うので、外部からの prefill とも互換。
 */
const STASH_KEY = 'imggen:stash';
export function stashSet(data) {
  try { localStorage.setItem(STASH_KEY, JSON.stringify(data)); }
  catch { /* ignore */ }
}
export function stashGet() {
  try {
    const raw = localStorage.getItem(STASH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
export function stashClear() {
  try { localStorage.removeItem(STASH_KEY); } catch { /* ignore */ }
}
