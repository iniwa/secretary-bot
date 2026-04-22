/* Mimi Secretary Bot — minimal service worker
 *
 * 方針: 静的アセットのみ cache-first で高速化。/api/ や /health は常にネットワーク。
 * バージョンアップは CACHE_VERSION を上げれば旧キャッシュが自動で整理される。
 */
const CACHE_VERSION = 'mimi-static-v1';
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

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(PRECACHE_URLS)).catch(() => {}),
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
        return fetch(req).then((resp) => {
          if (resp && resp.ok && resp.type === 'basic') {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy)).catch(() => {});
          }
          return resp;
        }).catch(() => cached);
      }),
    );
  }
});
