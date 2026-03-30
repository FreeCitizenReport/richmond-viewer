const CACHE = 'richmond-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => clients.claim())
));

self.addEventListener('fetch', e => {
  const url  = new URL(e.request.url);
  const path = url.pathname;
  if (!path.endsWith('data.json') && !path.endsWith('recent.json') && !path.endsWith('latest.json')) return;

  // Strip ?v=… cache-busting param so we always use the same cache key
  const cacheKey = new Request(url.origin + url.pathname);

  e.respondWith(
    caches.open(CACHE).then(cache =>
      cache.match(cacheKey).then(cached => {
        const fresh = fetch(e.request)
          .then(resp => { if (resp.ok) cache.put(cacheKey, resp.clone()); return resp; })
          .catch(() => cached);
        return cached || fresh;   // stale-while-revalidate
      })
    )
  );
});
