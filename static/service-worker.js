// Nafti AI - Service Worker with optimized caching strategy
const CACHE_VERSION = 'nafti-v2';
const STATIC_CACHE = CACHE_VERSION + '-static';
const DYNAMIC_CACHE = CACHE_VERSION + '-dynamic';

const STATIC_ASSETS = [
  '/',
  '/static/manifest.json',
  '/static/service-worker.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500&display=swap',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/marked/marked.min.js'
];

// Install event: cache static assets
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker...');
  
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        console.log('[SW] Caching static assets...');
        return cache.addAll(STATIC_ASSETS);
      })
      .then(() => self.skipWaiting())
      .catch((err) => console.error('[SW] Install error:', err))
  );
});

// Activate event: clean up old caches
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames.map((cacheName) => {
            if (cacheName !== STATIC_CACHE && cacheName !== DYNAMIC_CACHE) {
              console.log('[SW] Deleting old cache:', cacheName);
              return caches.delete(cacheName);
            }
          })
        );
      })
      .then(() => self.clients.claim())
  );
});

// Fetch event: cache-first for static, network-first for API
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Never cache API responses: history, account data and signed-in state are private.
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // Cache-first strategy for static assets
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Network-first for HTML pages
  event.respondWith(networkFirst(request));
});

// Cache-first strategy: return cached content if available
function cacheFirst(request) {
  return caches.match(request)
    .then((response) => {
      if (response) {
        console.log('[SW] Cache hit:', request.url);
        return response;
      }

      return fetch(request)
        .then((response) => {
          // Only cache successful responses
          if (!response || response.status !== 200) {
            return response;
          }

          const responseToCache = response.clone();
          caches.open(DYNAMIC_CACHE)
            .then((cache) => {
              cache.put(request, responseToCache);
            });

          return response;
        })
        .catch((err) => {
          console.error('[SW] Fetch error:', err);
          // Return cached response as fallback
          return caches.match(request);
        });
    });
}

// Network-first strategy: try network first, fall back to cache
function networkFirst(request) {
  return fetch(request)
    .then((response) => {
      if (!response || response.status !== 200) {
        return response;
      }

      const responseToCache = response.clone();
      caches.open(DYNAMIC_CACHE)
        .then((cache) => {
          cache.put(request, responseToCache);
        });

      return response;
    })
    .catch((err) => {
      console.log('[SW] Network error, trying cache:', request.url);
      return caches.match(request)
        .then((response) => {
          return response || new Response('Offline - content not available', {
            status: 503,
            statusText: 'Service Unavailable'
          });
        });
    });
}

// Check if URL is a static asset
function isStaticAsset(url) {
  const pathname = url.pathname;
  const staticExtensions = ['.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.woff', '.woff2', '.ttf'];
  return staticExtensions.some((ext) => pathname.endsWith(ext)) || 
         pathname.includes('/static/') ||
         pathname.includes('/fonts/');
}

