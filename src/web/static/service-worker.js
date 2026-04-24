/* Mimi Secretary Bot — minimal service worker
 *
 * 方針: 静的アセットのみ cache-first で高速化。/api/ や /health は常にネットワーク。
 * Cloudflare Access 経由でもCookieを送るよう credentials: 'include' を明示し、
 * ログインHTMLを誤ってキャッシュしないよう Content-Type もチェックする。
 * バージョンアップは CACHE_VERSION を上げれば旧キャッシュが自動で整理される。
 */
const CACHE_VERSION = 'mimi-static-v2';
const PRECACHE_URLS = [
  '/',
  '/static/index.html',
  '/static/favicon.svg',
  '/static/manifest.webmanifest',
  '/static/css/base.css',
  '/static/css/layout.css',
  '/static/css/components.css',
  '/static/js/api.js',
  '/static/js/app.js',
];

// Cloudflare Access のログインHTMLを掴まないためのガード
function isCacheableResponse(resp, url) {
  if (!resp || !resp.ok) return false;
  const ct = (resp.headers.get('Content-Type') || '').toLowerCase();
  // HTMLレスポンスは / と /static/*.html 以外には返ってこないはず。
  // 静的アセット(js/css/svg/webmanifest)のURLに対してHTMLが返ってきたらAccessログインページの可能性が高い
  if (url.pathname.match(/\.(js|css|svg|webmanifest|png|jpg|jpeg|gif|woff2?)$/)) {
    if (ct.includes('text/html')) return false;
  }
  return true;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then(async (cache) => {
      // addAll だと1件でもAccessに蹴られた瞬間にinstall全体が失敗するため、
      // 個別にフェッチしてキャッシュ可能なものだけ入れる
      await Promise.all(PRECACHE_URLS.map(async (u) => {
        try {
          const req = new Request(u, { credentials: 'include' });
          const resp = await fetch(req);
          if (isCacheableResponse(resp, new URL(u, self.location.origin))) {
            await cache.put(u, resp);
          }
        } catch (_) { /* 個別失敗は無視 */ }
      }));
    }).catch(() => {}),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  // API / health は常にネットワーク
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') return;

  // 静的アセットは cache-first、なければネットワーク→キャッシュに積む
  if (url.pathname.startsWith('/static/') || url.pathname === '/') {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        // Cloudflare Access を通過するため Cookie を明示的に送る
        const networkReq = new Request(req, { credentials: 'include' });
        return fetch(networkReq).then((resp) => {
          if (isCacheableResponse(resp, url)) {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy)).catch(() => {});
          }
          return resp;
        }).catch(() => cached);
      }),
    );
  }
});
