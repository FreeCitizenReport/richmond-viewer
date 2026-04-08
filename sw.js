const CACHE = 'richmond-v3';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  .then(() => clients.claim())
));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  const path = url.pathname;
  if (!path.endsWith('data.json')) return;
  const cacheKey = new Request(url.origin + url.pathname);
  e.respondWith(
    fetch(e.request, {cache: 'no-store'}).then(resp => {
      if (resp.ok) caches.open(CACHE).then(cache => cache.put(cacheKey, resp.clone()));
      return resp;
    }).catch(() => caches.open(CACHE).then(cache => cache.match(cacheKey)))
  );
});