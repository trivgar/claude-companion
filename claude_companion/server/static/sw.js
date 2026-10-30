// Network-only: a cached dashboard is a lying dashboard. Navigations pass
// through; every other fetch revalidates so a stale script is never served.
self.addEventListener('fetch', e => e.respondWith(
  e.request.mode === 'navigate' ? fetch(e.request) : fetch(e.request, {cache: 'no-cache'})));

// Take over on the next load rather than after every window has closed.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Companion', {
    body: d.body || '',
    // The same tag replaces in place instead of stacking.
    tag: d.tag || 'companion',
    renotify: !!d.renotify,
    icon: d.icon || '/static/icon.svg',
    data: {url: d.url || '/'},
    ...(Array.isArray(d.actions) ? {actions: d.actions} : {}),
  }));
});

// A tap opens the dashboard at the session it names (/?agent=<name>), reusing
// an open window rather than stacking another.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const raw = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    let dest;
    try { dest = new URL(raw, self.location.origin); } catch (err) { dest = new URL('/', self.location.origin); }
    if (dest.origin !== self.location.origin) return clients.openWindow(dest.href);
    const all = await clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const c of all) {
      if (new URL(c.url).origin === self.location.origin && 'focus' in c) {
        if ('navigate' in c) { try { await c.navigate(dest.href); } catch (err) {} }
        return c.focus();
      }
    }
    return clients.openWindow(dest.href);
  })());
});
