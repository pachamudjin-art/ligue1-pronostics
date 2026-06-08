// Service Worker — Notifications Push ENTE Pronos
self.addEventListener("push", event => {
  let data = { title: "⚽ ENTE Pronos", body: "Nouvelle notification" };
  try { data = JSON.parse(event.data.text()); } catch(e) {}
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      vibrate: [200, 100, 200],
      tag: "pronos-notif",
      renotify: true,
      data: { url: "/" },
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window" }).then(windowClients => {
      for (const client of windowClients) {
        if (client.url === "/" && "focus" in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow("/");
    })
  );
});
