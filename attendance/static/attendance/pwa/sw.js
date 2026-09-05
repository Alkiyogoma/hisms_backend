// Hodari Attendance PWA — Service Worker
// Version is fetched dynamically from the server config endpoint.
// When pwa_version changes in admin settings, all users get the new version.

const CACHE_NAME = 'hodari-attendance-v';
const CONFIG_URL = '/attendance/pwa/config/';
const OFFLINE_URL = '/static/attendance/pwa/offline.html';
const SYNC_TAG = 'hodari-offline-sync';
const DB_NAME = 'hodari-pwa-sync';
const DB_VERSION = 1;
const STORE_NAME = 'pending-submissions';
const CONFIG_TIMEOUT_MS = 5000;

let appVersion = 1;
let baseUrl = '';

// ─── IndexedDB helpers for offline sync queue ───

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function saveToSyncQueue(entry) {
  return openDB().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).add(entry);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

function getSyncQueue() {
  return openDB().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
}

function clearSyncQueue() {
  return openDB().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

function removeFromSyncQueue(id) {
  return openDB().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

// ─── Config fetch with timeout ───

function fetchConfigWithTimeout() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CONFIG_TIMEOUT_MS);
  return fetch(CONFIG_URL, { signal: controller.signal })
    .then(res => { clearTimeout(timeoutId); return res.json(); })
    .catch(() => { clearTimeout(timeoutId); return null; });
}

// ─── Static assets to precache on install ───

const PRECACHE_ASSETS = [
  '/static/attendance/pwa/offline.html',
  '/static/attendance/pwa/icons/icon-192.svg',
  '/static/attendance/pwa/icons/icon-512.svg',
  '/static/branding/favicon.ico',
];

// Install: fetch config, precache critical assets, and activate immediately
self.addEventListener('install', (event) => {
  event.waitUntil(
    fetchConfigWithTimeout()
      .then(config => {
        if (config) {
          baseUrl = config.base_url || '';
          appVersion = config.version || 1;
        }
      })
      .then(() => {
        return caches.open(CACHE_NAME + appVersion).then(cache => {
          return cache.addAll(PRECACHE_ASSETS).catch(() => {
            return Promise.allSettled(
              PRECACHE_ASSETS.map(url =>
                fetch(url).then(r => {
                  if (r.ok) return cache.put(url, r);
                }).catch(() => {})
              )
            );
          });
        });
      })
      .then(() => self.skipWaiting())
  );
});

// Activate: clean ALL old hodari-attendance caches (not just current prefix)
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(key => key.startsWith('hodari-attendance-v') && key !== CACHE_NAME + appVersion)
            .map(key => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: network-first for API calls and PWA shell, offline fallback to cache
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET requests (but handle POST via sync)
  if (event.request.method !== 'GET') return;

  // API calls: network first, fall back to cache
  if (url.pathname.includes('/attendance/api/')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME + appVersion).then(cache => {
              cache.put(event.request, clone);
            });
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // PWA shell: network first, fall back to cache, then offline page
  if (url.pathname.startsWith('/attendance/pwa/') || url.pathname === '/attendance/pwa') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME + appVersion).then(cache => {
              cache.put(event.request, clone);
            });
          }
          return response;
        })
        .catch(() => {
          return caches.match(event.request).then(cached => {
            return cached || caches.match(OFFLINE_URL);
          });
        })
    );
    return;
  }

  // Static assets: cache first, then network
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME + appVersion).then(cache => {
              cache.put(event.request, clone);
            });
          }
          return response;
        });
      }).catch(() => {
        if (event.request.mode === 'navigate') {
          return caches.match(OFFLINE_URL);
        }
        return new Response('', { status: 503, statusText: 'Service Unavailable' });
      })
    );
    return;
  }

  // Everything else: network only
  event.respondWith(fetch(event.request));
});

// ─── Background Sync: replay offline attendance submissions ───

self.addEventListener('sync', (event) => {
  if (event.tag === SYNC_TAG) {
    event.waitUntil(replayOfflineSubmissions());
  }
});

function replayOfflineSubmissions() {
  return getSyncQueue().then(entries => {
    if (!entries.length) return;

    const grouped = { checkin: [], checkout: [] };
    entries.forEach(entry => {
      if (grouped[entry.type]) grouped[entry.type].push(entry);
    });

    const promises = [];

    if (grouped.checkin.length) {
      const csrfToken = grouped.checkin[0].csrfToken || '';
      const body = {
        students: grouped.checkin.map(e => ({ id: e.studentId, timestamp: e.timestamp })),
      };
      promises.push(
        fetch('/attendance/api/checkin/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(body),
        }).then(r => r.json()).then(d => {
          const ok = d.summary && d.summary.successful || 0;
          return clearSyncQueue().then(() => ok);
        }).catch(() => 0)
      );
    }

    if (grouped.checkout.length) {
      const csrfToken = grouped.checkout[0].csrfToken || '';
      const body = {
        students: grouped.checkout.map(e => ({
          id: e.studentId, timestamp: e.timestamp, parent_name: '', reason: '',
        })),
      };
      promises.push(
        fetch('/attendance/api/checkout/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(body),
        }).then(r => r.json()).then(d => {
          const ok = d.summary && d.summary.successful || 0;
          return clearSyncQueue().then(() => ok);
        }).catch(() => 0)
      );
    }

    return Promise.all(promises).then(results => {
      const totalOk = results.reduce((a, b) => a + b, 0);
      if (totalOk > 0) {
        self.clients.matchAll().then(clients => {
          clients.forEach(client => {
            client.postMessage({ type: 'SYNC_COMPLETE', synced: totalOk });
          });
        });
      }
    });
  });
}

// ─── Message handler: version check + offline queue commands ───

self.addEventListener('message', (event) => {
  const data = event.data;
  if (!data) return;

  if (data.type === 'CHECK_VERSION') {
    fetchConfigWithTimeout().then(config => {
      if (config && config.version && config.version !== appVersion) {
        appVersion = config.version;
        baseUrl = config.base_url || '';
        self.skipWaiting();
        self.clients.matchAll().then(clients => {
          clients.forEach(client => {
            client.postMessage({ type: 'NEW_VERSION', version: appVersion });
          });
        });
      }
    });
  }

  if (data.type === 'QUEUE_OFFLINE_SUBMISSION') {
    const entry = {
      type: data.submissionType, // 'checkin' or 'checkout'
      studentId: data.studentId,
      timestamp: data.timestamp,
      csrfToken: data.csrfToken || '',
      queuedAt: Date.now(),
    };
    saveToSyncQueue(entry).then(() => {
      // Attempt background sync if available
      if ('sync' in self.registration) {
        self.registration.sync.register(SYNC_TAG).catch(() => {});
      }
      event.ports[0].postMessage({ ok: true });
    }).catch(() => {
      event.ports[0].postMessage({ ok: false });
    });
  }

  if (data.type === 'GET_SYNC_QUEUE') {
    getSyncQueue().then(entries => {
      event.ports[0].postMessage({ entries });
    }).catch(() => {
      event.ports[0].postMessage({ entries: [] });
    });
  }

  if (data.type === 'CLEAR_SYNC_QUEUE') {
    clearSyncQueue().then(() => {
      event.ports[0].postMessage({ ok: true });
    }).catch(() => {
      event.ports[0].postMessage({ ok: false });
    });
  }
});

// Push event: show notification when push message arrives
self.addEventListener('push', (event) => {
  let data = { title: 'Attendance', body: 'Update available' };
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }
  const options = {
    body: data.body,
    icon: '/static/attendance/pwa/icons/icon-192.svg',
    badge: '/static/attendance/pwa/icons/icon-192.svg',
    vibrate: [200, 100, 200],
    tag: 'attendance-notification',
    renotify: true,
  };
  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Notification click: open app
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const urlToOpen = '/attendance/pwa/app/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes('/attendance/pwa/') && 'focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(urlToOpen);
      }
    })
  );
});
