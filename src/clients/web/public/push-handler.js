// Push notification handler — imported by the generated service worker.
// Receives Web Push messages from the Brain server and shows a system notification.

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data?.json() ?? {};
    } catch {
        data = { title: 'Chad', body: event.data?.text() ?? '' };
    }

    const title = data.title ?? 'Chad';
    const options = {
        body: data.body ?? '',
        icon: '/pwa-192x192.png',
        badge: '/pwa-192x192.png',
        tag: data.tag ?? 'chad-notification',
        data: data.url ? { url: data.url } : {},
        requireInteraction: data.requireInteraction ?? false,
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = event.notification.data?.url ?? '/';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            for (const client of windowClients) {
                if (client.url.startsWith(self.location.origin) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});
