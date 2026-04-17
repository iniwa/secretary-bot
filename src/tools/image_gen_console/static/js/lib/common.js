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
