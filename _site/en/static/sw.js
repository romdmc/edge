const CACHE_NAME = 'edge-pwa-v1';
const MAX_CACHE_ITEMS = 50;

const CORE_ASSETS = [
  '/',
  '/index.html',
  '/all.html',
  '/archives.html',
  '/tags.html',
  '/sources.html',
  '/series.html',
  '/manifesto.html',
  '/digest.html',
  '/trends.html',
  '/static/style.css'
];

// Helper: trim cache to MAX_CACHE_ITEMS
async function trimCache() {
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  if (keys.length > MAX_CACHE_ITEMS) {
    const toDelete = keys.slice(0, keys.length - MAX_CACHE_ITEMS);
    await Promise.all(toDelete.map(key => cache.delete(key)));
  }
}

// Install: cache core assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(CORE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate: claim clients immediately
self.addEventListener('activate', event => {
  event.waitUntil(
    Promise.all([
      self.clients.claim(),
      // Clean up old caches
      caches.keys().then(keys =>
        Promise.all(
          keys.filter(key => key !== CACHE_NAME)
              .map(key => caches.delete(key))
        )
      )
    ])
  );
});

// Fetch: cache-first for static assets, network-first for HTML pages
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  const isHTML = request.headers.get('accept')?.includes('text/html') ||
                 url.pathname.endsWith('.html') ||
                 url.pathname === '/';

  if (isHTML) {
    // Network-first for HTML pages
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(request, clone);
              trimCache();
            });
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
  } else {
    // Cache-first for static assets (CSS, JS, images, etc.)
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(request, clone);
              trimCache();
            });
          }
          return response;
        });
      })
    );
  }
});
