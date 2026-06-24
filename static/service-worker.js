const CACHE_NAME = 'easy-admin-mobile-pwa-v7-staff-payslips';
const APP_SHELL = [
  '/mobile',
  '/mobile/offline',
  '/staff/mobile',
  '/static/mobile.css?v=20260622',
  '/static/mobile.js?v=20260622',
  '/static/easyadmin-formatters.js?v=20260622-format',
  '/static/session-timeout.js?v=20260623-timeout',
  '/static/staff-portal.css?v=20260624-payslips',
  '/static/staff-portal.js?v=20260624-payslips',
  '/static/easy_admin_logo.png',
  '/static/pwa-icon-192.png',
  '/static/pwa-icon-512.png',
  '/manifest.webmanifest'
];

const DYNAMIC_PATH_PREFIXES = [
  '/api/',
  '/bookings',
  '/booking_staff_hours',
  '/download_attachment/',
  '/staff/download_payslip/',
  '/staff/download_attachment/',
  '/static/uploads/',
  '/uploads/'
];

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

  const isDynamic = DYNAMIC_PATH_PREFIXES.some((prefix) => url.pathname.startsWith(prefix));
  if (isDynamic) {
    event.respondWith(fetch(request, { cache: 'no-store' }));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/mobile/offline')));
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
