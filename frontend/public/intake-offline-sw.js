const CACHE_NAME = 'posterpro-intake-v2';
const SHELL_URLS = ['/intake', '/intake/slate', '/settings/intake', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  if (event.request.mode === 'navigate' && url.pathname.startsWith('/intake')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy)).catch(() => undefined);
          return response;
        })
        .catch(async () => (await caches.match(event.request)) || caches.match('/intake/slate')),
    );
    return;
  }

  // Do not cache Next.js bundles here.  This worker has origin-wide scope and
  // caching those assets made unrelated pages such as /listings appear stuck
  // on a previous deployment.  Intake navigation remains network-first with
  // its own offline fallback above; all application bundles stay fresh.
});
