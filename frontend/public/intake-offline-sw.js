const CACHE_NAME = 'posterpro-capture-v4';
const SHELL_URLS = ['/intake', '/intake/slate', '/settings/intake', '/packing', '/manifest.webmanifest'];

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

  if (event.request.mode === 'navigate' && (url.pathname.startsWith('/intake') || url.pathname.startsWith('/packing'))) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy)).catch(() => undefined);
          return response;
        })
        .catch(async () => (await caches.match(event.request)) || caches.match(url.pathname.startsWith('/packing') ? '/packing' : '/intake/slate')),
    );
    return;
  }

  // Cache runtime assets only when requested by an intake client. They remain
  // network-first online, so a deployment replaces them before the cached copy
  // can be used. This lets a prepared intake screen reload when disconnected
  // without making the rest of PosterPro susceptible to stale bundles.
  if (url.pathname.startsWith('/_next/') || url.pathname.startsWith('/media/')) {
    event.respondWith((async () => {
      const client = event.clientId ? await self.clients.get(event.clientId) : null;
      const clientPath = client ? new URL(client.url).pathname : '';
      if (!client || (!clientPath.startsWith('/intake') && !clientPath.startsWith('/packing'))) return fetch(event.request);
      try {
        const response = await fetch(event.request);
        if (response.ok) caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone())).catch(() => undefined);
        return response;
      } catch {
        return (await caches.match(event.request)) || Response.error();
      }
    })());
  }
});
