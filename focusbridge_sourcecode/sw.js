// Focus Bridge service worker.
// v17 — IMPORTANT FIX: HTML is now NETWORK-FIRST.
// The previous versions served index.html cache-first, which meant a redeployed
// index.html (e.g. after adding Supabase keys) could keep serving the OLD cached
// copy indefinitely. Navigations and HTML now always try the network first and
// fall back to cache only when offline. Static assets stay cache-first for speed.
const CACHE = 'focus-bridge-v30';
const CORE = ['./', './index.html', './privacy.html', './delete-account.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()).catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = req.url;

  // never intercept analytics or the database API
  if (url.indexOf('goatcounter.com') !== -1 || url.indexOf('gc.zgo.at') !== -1) return;
  if (url.indexOf('supabase.co') !== -1) return;

  const isHTML = req.mode === 'navigate'
    || (req.headers.get('accept') || '').indexOf('text/html') !== -1
    || /\.html($|\?)/.test(url);

  if (isHTML) {
    // NETWORK FIRST: always pick up a fresh deploy
    e.respondWith(
      fetch(req).then(resp => {
        try { const copy = resp.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } catch (_) {}
        return resp;
      }).catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
    );
    return;
  }

  // other assets: cache first (they are versioned/static)
  e.respondWith(
    caches.match(req).then(cached => cached || fetch(req).then(resp => {
      try { const copy = resp.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } catch (_) {}
      return resp;
    }).catch(() => cached))
  );
});
