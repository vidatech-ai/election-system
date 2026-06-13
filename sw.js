/**
 * Service Worker — Kenya Election 2027
 * Caches static assets; API calls always go network-first
 */

var CACHE_NAME = 'election-2027-v1';

var STATIC_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.json',
  'https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=Inter:wght@400;500;600&display=swap'
];

/* ─── Install: cache static assets ──────────────────────────── */
self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

/* ─── Activate: clean old caches ────────────────────────────── */
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names.filter(function (n) { return n !== CACHE_NAME; })
             .map(function (n) { return caches.delete(n); })
      );
    })
  );
  self.clients.claim();
});

/* ─── Fetch strategy ─────────────────────────────────────────
   - API calls (/api/*): network-only (voting must be real-time)
   - Static assets: cache-first with network fallback
──────────────────────────────────────────────────────────── */
self.addEventListener('fetch', function (event) {
  var url = event.request.url;

  // API calls — always network
  if (url.includes('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Static assets — cache first
  event.respondWith(
    caches.match(event.request).then(function (cached) {
      if (cached) return cached;
      return fetch(event.request).then(function (response) {
        // Cache successful GET responses
        if (event.request.method === 'GET' && response.status === 200) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function (cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      }).catch(function () {
        // Offline fallback — return cached index
        if (event.request.mode === 'navigate') {
          return caches.match('/');
        }
      });
    })
  );
});