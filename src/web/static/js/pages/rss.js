/** RSS page — tabbed interface for feeds and articles. */
import { api } from '../api.js';
import { toast } from '../app.js';

// ============================================================
// State
// ============================================================
let activeTab = 'feeds';
let feeds = [];
let categories = {};
let disabledCategories = [];
let selectedCategory = '';
let articles = [];
let articlesLoading = false;
let articlesHasMore = true;
let articlesOffset = 0;
let fetchingFeeds = false;

const ARTICLES_LIMIT = 30;

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function truncate(str, max = 200) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '...' : str;
}

function truncateUrl(url, max = 50) {
  if (!url) return '';
  try {
    const u = new URL(url);
    const display = u.hostname + u.pathname;
    return display.length > max ? display.slice(0, max) + '...' : display;
  } catch {
    return url.length > max ? url.slice(0, max) + '...' : url;
  }
}

function fmtTime(iso) {
  if (!iso) return '---';
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mi}`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .rss-tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }
  .rss-tab {
    padding: 0.4rem 1rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 0.8125rem;
    font-weight: 500;
    transition: all var(--ease);
  }
  .rss-tab:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .rss-tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .rss-tab-panel { display: none; }
  .rss-tab-panel.active { display: block; }

  /* Add feed form */
  .rss-add-form {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: flex-end;
    margin-bottom: 1rem;
  }
  .rss-add-form .form-group {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .rss-add-form .form-group.url-group {
    flex: 2;
    min-width: 200px;
  }
  .rss-add-form .form-group.opt-group {
    flex: 1;
    min-width: 120px;
  }

  /* Category filter pills */
  .rss-categories {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin-bottom: 1rem;
  }
  .rss-cat-pill {
    padding: 0.25rem 0.7rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg-surface);
    color: var(--text-secondary);
    font-size: 0.75rem;
    cursor: pointer;
    transition: all var(--ease);
    user-select: none;
  }
  .rss-cat-pill:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .rss-cat-pill.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .rss-cat-pill .cat-count {
    margin-left: 0.3rem;
    opacity: 0.7;
    font-size: 0.6875rem;
  }

  /* Feed table */
  .rss-feed-url {
    max-width: 250px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rss-feed-url a {
    color: var(--accent);
    text-decoration: none;
  }
  .rss-feed-url a:hover {
    text-decoration: underline;
  }

  /* Fetch button */
  .rss-toolbar {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }

  /* Article cards */
  .rss-article-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .rss-article-card {
    padding: 1rem 1.25rem;
  }
  .rss-article-title {
    font-size: 0.95rem;
    font-weight: 600;
    margin: 0 0 0.35rem;
    line-height: 1.4;
  }
  .rss-article-title a {
    color: var(--text-primary);
    text-decoration: none;
  }
  .rss-article-title a:hover {
    color: var(--accent);
    text-decoration: underline;
  }
  .rss-article-summary {
    font-size: 0.825rem;
    color: var(--text-secondary);
    line-height: 1.55;
    margin-bottom: 0.5rem;
  }
  .rss-article-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    font-size: 0.7rem;
    color: var(--text-muted);
  }

  /* Feedback buttons */
  .rss-article-actions {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.5rem;
  }
  .rss-feedback-btn {
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg-surface);
    color: var(--text-secondary);
    font-size: 0.8rem;
    cursor: pointer;
    transition: all var(--ease);
    user-select: none;
    line-height: 1.2;
  }
  .rss-feedback-btn:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
  .rss-feedback-btn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .rss-feedback-btn.active.down {
    background: var(--danger, #c0392b);
    border-color: var(--danger, #c0392b);
  }

  /* Disabled feed row */
  .rss-feed-row.disabled {
    opacity: 0.5;
  }
  .badge-disabled {
    background: var(--text-muted);
    color: #fff;
  }

  /* Load more */
  .load-more-wrap {
    text-align: center;
    padding: 1rem 0;
  }

  /* Empty state */
  .rss-empty {
    text-align: center;
    padding: 2.5rem 1rem;
    color: var(--text-muted);
    font-size: 0.9rem;
  }

  @media (max-width: 600px) {
    .rss-add-form {
      flex-direction: column;
    }
    .rss-add-form .form-group {
      width: 100%;
    }
    .rss-article-card {
      padding: 0.75rem;
    }
  }
</style>

<div class="rss-page">
  <div class="rss-tabs">
    <button class="rss-tab active" data-tab="feeds">Feeds</button>
    <button class="rss-tab" data-tab="articles">Articles</button>
  </div>

  <!-- Feeds Tab -->
  <div class="rss-tab-panel active" id="panel-feeds">
    <div class="rss-add-form">
      <div class="form-group url-group">
        <label class="form-label">Feed URL</label>
        <input type="text" class="form-input" id="rss-add-url" placeholder="https://example.com/feed.xml" />
      </div>
      <div class="form-group opt-group">
        <label class="form-label">Title (optional)</label>
        <input type="text" class="form-input" id="rss-add-title" placeholder="Feed title" />
      </div>
      <div class="form-group opt-group">
        <label class="form-label">Category (optional)</label>
        <input type="text" class="form-input" id="rss-add-category" placeholder="Category" />
      </div>
      <button class="btn btn-primary" id="rss-add-btn">Add</button>
    </div>

    <div class="rss-categories" id="rss-feed-categories"></div>

    <div class="rss-toolbar">
      <button class="btn btn-sm btn-primary" id="rss-fetch-btn">Fetch Now</button>
    </div>

    <div class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>URL</th>
              <th>Category</th>
              <th>Preset</th>
              <th>Added by</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="rss-feeds-tbody">
            <tr><td colspan="6" class="rss-empty">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Articles Tab -->
  <div class="rss-tab-panel" id="panel-articles">
    <div class="rss-categories" id="rss-article-categories"></div>
    <div class="rss-article-list" id="rss-article-list">
      <div class="rss-empty">Loading...</div>
    </div>
    <div class="load-more-wrap" id="rss-articles-more-wrap" style="display:none">
      <button class="btn btn-sm" id="rss-articles-more">Load more</button>
    </div>
  </div>
</div>`;
}

// ============================================================
// Category pills
// ============================================================
function renderCategoryPills(containerId, selected) {
  const container = $(containerId);
  if (!container) return;

  const entries = Object.entries(categories);
  if (entries.length === 0) {
    container.innerHTML = '';
    return;
  }

  // feeds から各カテゴリの件数を集計
  const allCount = feeds.length;
  let html = `<button class="rss-cat-pill${!selected ? ' active' : ''}" data-category="">All<span class="cat-count">${allCount}</span></button>`;
  for (const [key, label] of entries) {
    const count = feeds.filter(f => f.category === key).length;
    html += `<button class="rss-cat-pill${selected === key ? ' active' : ''}" data-category="${esc(key)}">${esc(label)}<span class="cat-count">${count}</span></button>`;
  }
  container.innerHTML = html;
}

// ============================================================
// Feeds tab
// ============================================================
function renderFeedRows(feedList) {
  if (!feedList.length) {
    return '<tr><td colspan="6" class="rss-empty">No feeds found</td></tr>';
  }
  return feedList.map(f => {
    const catBadge = f.category
      ? `<span class="badge badge-info">${esc(f.category)}</span>`
      : '<span class="text-muted">---</span>';
    const presetBadge = f.is_preset
      ? '<span class="badge badge-accent">preset</span>'
      : '';
    const disabled = !!f.user_disabled;
    const rowCls = disabled ? 'rss-feed-row disabled' : 'rss-feed-row';
    const disabledBadge = disabled
      ? ' <span class="badge badge-disabled">disabled</span>'
      : '';
    const toggleLabel = disabled ? 'Enable' : 'Disable';
    const toggleTo = disabled ? 'true' : 'false';
    return `<tr class="${rowCls}">
      <td>${esc(f.title || '(no title)')}${disabledBadge}</td>
      <td class="rss-feed-url"><a href="${esc(f.url)}" target="_blank" rel="noopener" title="${esc(f.url)}">${esc(truncateUrl(f.url))}</a></td>
      <td>${catBadge}</td>
      <td>${presetBadge}</td>
      <td class="text-xs">${esc(f.added_by || '---')}</td>
      <td>
        <button class="btn btn-sm" data-action="toggle-feed" data-feed-id="${f.id}" data-enabled="${toggleTo}">${toggleLabel}</button>
        <button class="btn btn-sm btn-danger" data-action="delete-feed" data-feed-id="${f.id}">Delete</button>
      </td>
    </tr>`;
  }).join('');
}

function getFilteredFeeds() {
  if (!selectedCategory) return feeds;
  return feeds.filter(f => f.category === selectedCategory);
}

async function loadFeeds() {
  try {
    const data = await api('/api/rss/feeds');
    feeds = data?.feeds || [];
    categories = data?.categories || {};
    disabledCategories = data?.disabled_categories || [];
    renderCategoryPills('rss-feed-categories', selectedCategory);
    renderCategoryPills('rss-article-categories', selectedCategory);
    const tbody = $('rss-feeds-tbody');
    if (tbody) {
      tbody.innerHTML = renderFeedRows(getFilteredFeeds());
    }
  } catch (err) {
    toast('Failed to load feeds', 'error');
    console.error(err);
  }
}

async function addFeed() {
  const url = $('rss-add-url')?.value?.trim();
  if (!url) {
    toast('Please enter a feed URL', 'info');
    return;
  }
  const title = $('rss-add-title')?.value?.trim() || undefined;
  const category = $('rss-add-category')?.value?.trim() || undefined;
  const body = { url };
  if (title) body.title = title;
  if (category) body.category = category;

  try {
    await api('/api/rss/feeds', { method: 'POST', body });
    toast('Feed added', 'success');
    if ($('rss-add-url')) $('rss-add-url').value = '';
    if ($('rss-add-title')) $('rss-add-title').value = '';
    if ($('rss-add-category')) $('rss-add-category').value = '';
    await loadFeeds();
  } catch (err) {
    toast('Failed to add feed: ' + err.message, 'error');
  }
}

async function deleteFeed(feedId) {
  if (!confirm('Delete this feed?')) return;
  try {
    await api(`/api/rss/feeds/${feedId}`, { method: 'DELETE' });
    toast('Feed deleted', 'success');
    await loadFeeds();
  } catch (err) {
    toast('Failed to delete feed: ' + err.message, 'error');
  }
}

async function toggleFeed(feedId, enabled) {
  try {
    await api(`/api/rss/feeds/${feedId}/toggle`, {
      method: 'POST',
      body: { enabled },
    });
    toast(enabled ? 'Feed enabled' : 'Feed disabled', 'success');
    await loadFeeds();
  } catch (err) {
    toast('Failed to toggle feed: ' + err.message, 'error');
  }
}

async function fetchNow() {
  if (fetchingFeeds) return;
  fetchingFeeds = true;
  const btn = $('rss-fetch-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Fetching...';
  }
  try {
    await api('/api/rss/fetch', { method: 'POST' });
    toast('Feed fetch completed', 'success');
    await loadFeeds();
  } catch (err) {
    toast('Feed fetch failed: ' + err.message, 'error');
  } finally {
    fetchingFeeds = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Fetch Now';
    }
  }
}

// ============================================================
// Articles tab
// ============================================================
function renderArticleCards(list) {
  if (!list.length) {
    return '<div class="rss-empty">No articles found</div>';
  }
  return list.map(a => {
    const feedName = feeds.find(f => f.id === a.feed_id)?.title || '';
    const summaryHtml = a.summary
      ? `<div class="rss-article-summary">${esc(truncate(a.summary, 200))}</div>`
      : '';
    const rating = Number(a.user_rating) || 0;
    const upCls = rating === 1 ? 'rss-feedback-btn active' : 'rss-feedback-btn';
    const downCls = rating === -1 ? 'rss-feedback-btn active down' : 'rss-feedback-btn';
    return `<div class="card rss-article-card" data-article-id="${a.id}">
      <div class="rss-article-title"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title || '(no title)')}</a></div>
      ${summaryHtml}
      <div class="rss-article-meta">
        <span>${fmtTime(a.published_at)}</span>
        ${feedName ? `<span>${esc(feedName)}</span>` : ''}
      </div>
      <div class="rss-article-actions">
        <button class="${upCls}" data-action="feedback" data-article-id="${a.id}" data-rating="1">👍</button>
        <button class="${downCls}" data-action="feedback" data-article-id="${a.id}" data-rating="-1">👎</button>
      </div>
    </div>`;
  }).join('');
}

async function loadArticles(reset = false) {
  if (articlesLoading) return;
  if (reset) {
    articlesOffset = 0;
    articles = [];
    articlesHasMore = true;
  }
  if (!articlesHasMore) return;

  articlesLoading = true;
  try {
    const params = { limit: ARTICLES_LIMIT };
    if (articlesOffset > 0) params.offset = articlesOffset;
    if (selectedCategory) params.category = selectedCategory;

    const data = await api('/api/rss/articles', { params });
    const list = data?.articles || [];

    articles = reset ? list : articles.concat(list);
    articlesOffset += list.length;
    articlesHasMore = list.length >= ARTICLES_LIMIT;

    const container = $('rss-article-list');
    if (container) {
      if (reset) {
        container.innerHTML = renderArticleCards(articles);
      } else {
        container.insertAdjacentHTML('beforeend', renderArticleCards(list));
      }
    }

    const moreWrap = $('rss-articles-more-wrap');
    if (moreWrap) moreWrap.style.display = articlesHasMore ? '' : 'none';
  } catch (err) {
    toast('Failed to load articles', 'error');
    console.error(err);
  } finally {
    articlesLoading = false;
  }
}

async function sendFeedback(articleId, rating) {
  // 同じボタンを再度押したら取り消し
  const article = articles.find(a => String(a.id) === String(articleId));
  const current = article ? (Number(article.user_rating) || 0) : 0;
  const nextRating = current === rating ? 0 : rating;
  try {
    const res = await api(`/api/rss/articles/${articleId}/feedback`, {
      method: 'POST',
      body: { rating: nextRating },
    });
    // ローカル状態を更新
    if (article) article.user_rating = res?.rating ?? nextRating;
    // 該当カードだけ再描画
    const card = document.querySelector(`.rss-article-card[data-article-id="${articleId}"]`);
    if (card) {
      const upBtn = card.querySelector('[data-rating="1"]');
      const downBtn = card.querySelector('[data-rating="-1"]');
      if (upBtn) {
        upBtn.classList.toggle('active', nextRating === 1);
        upBtn.classList.remove('down');
      }
      if (downBtn) {
        downBtn.classList.toggle('active', nextRating === -1);
        downBtn.classList.toggle('down', nextRating === -1);
      }
    }
  } catch (err) {
    toast('Failed to send feedback: ' + err.message, 'error');
  }
}

// ============================================================
// Tab switching
// ============================================================
function switchTab(tab) {
  if (tab === activeTab) return;
  activeTab = tab;

  document.querySelectorAll('.rss-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  document.querySelectorAll('.rss-tab-panel').forEach(el => {
    el.classList.toggle('active', el.id === `panel-${tab}`);
  });

  if (tab === 'articles' && articles.length === 0) {
    loadArticles(true);
  }
}

// ============================================================
// Category selection handler
// ============================================================
function onCategoryClick(e) {
  const pill = e.target.closest('.rss-cat-pill');
  if (!pill) return;
  const cat = pill.dataset.category || '';
  if (cat === selectedCategory) return;
  selectedCategory = cat;

  // Update both pill containers
  renderCategoryPills('rss-feed-categories', selectedCategory);
  renderCategoryPills('rss-article-categories', selectedCategory);
  attachCategoryHandlers();

  if (activeTab === 'feeds') {
    const tbody = $('rss-feeds-tbody');
    if (tbody) tbody.innerHTML = renderFeedRows(getFilteredFeeds());
  } else {
    loadArticles(true);
  }
}

function attachCategoryHandlers() {
  $('rss-feed-categories')?.addEventListener('click', onCategoryClick);
  $('rss-article-categories')?.addEventListener('click', onCategoryClick);
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  // Tab switching
  document.querySelectorAll('.rss-tab').forEach(el => {
    el.addEventListener('click', () => switchTab(el.dataset.tab));
  });

  // Add feed
  $('rss-add-btn')?.addEventListener('click', addFeed);
  $('rss-add-url')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addFeed();
  });

  // Fetch now
  $('rss-fetch-btn')?.addEventListener('click', fetchNow);

  // Feed table — delegated delete/toggle handler
  $('rss-feeds-tbody')?.addEventListener('click', e => {
    const delBtn = e.target.closest('[data-action="delete-feed"]');
    if (delBtn) {
      const feedId = delBtn.dataset.feedId;
      if (feedId) deleteFeed(feedId);
      return;
    }
    const togBtn = e.target.closest('[data-action="toggle-feed"]');
    if (togBtn) {
      const feedId = togBtn.dataset.feedId;
      const enabled = togBtn.dataset.enabled === 'true';
      if (feedId) toggleFeed(feedId, enabled);
    }
  });

  // Article list — delegated feedback handler
  $('rss-article-list')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-action="feedback"]');
    if (!btn) return;
    const articleId = btn.dataset.articleId;
    const rating = parseInt(btn.dataset.rating, 10);
    if (articleId && (rating === 1 || rating === -1)) {
      sendFeedback(articleId, rating);
    }
  });

  // Load more articles
  $('rss-articles-more')?.addEventListener('click', () => loadArticles(false));

  // Category handlers
  attachCategoryHandlers();

  // Initial data load
  await loadFeeds();
}

export function unmount() {
  activeTab = 'feeds';
  feeds = [];
  categories = {};
  disabledCategories = [];
  selectedCategory = '';
  articles = [];
  articlesLoading = false;
  articlesHasMore = true;
  articlesOffset = 0;
  fetchingFeeds = false;
}
