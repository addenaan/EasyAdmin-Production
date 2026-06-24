const CACHE_NAME = 'easy-admin-mobile-pwa-v7-live-data-refresh';
const APP_SHELL = [
  '/mobile/offline',
  '/static/mobile.css?v=20260622',
  '/static/mobile.js?v=20260622',
  '/static/easyadmin-formatters.js?v=20260622-format',
  '/static/session-timeout.js?v=20260623-timeout',
  '/static/staff-portal.css?v=20260623-staff2',
  '/static/staff-portal.js?v=20260623-staff2',
  '/static/easy_admin_logo.png',
  '/static/pwa-icon-192.png',
  '/static/pwa-icon-512.png',
  '/manifest.webmanifest'
];

function isAppShellAsset(url) {
  return APP_SHELL.some((entry) => {
    const entryUrl = new URL(entry, self.location.origin);
    return entryUrl.pathname === url.pathname;
  });
}

function isCacheableStaticAsset(url) {
  if (url.pathname.startsWith('/uploads/') || url.pathname.startsWith('/static/uploads/')) return false;
  if (url.pathname === '/manifest.webmanifest') return true;
  return url.pathname.startsWith('/static/') && isAppShellAsset(url);
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request, { cache: 'no-store' }).catch(() => caches.match('/mobile/offline')));
    return;
  }

  // All database-backed routes must always be fresh. This includes companies,
  // employees, bookings, payroll, staff portal data, reports and downloads.
  if (!isCacheableStaticAsset(url)) {
    event.respondWith(fetch(request, { cache: 'no-store' }));
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (!response || response.status !== 200 || response.type !== 'basic') return response;
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
      return response;
    }).catch(() => cached))
  );
});
