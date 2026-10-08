// Atlas: оболочка приложения работает и без сети (звезда, подсказка), разговор — только с сетью.
const SHELL = "atlas-shell-v6";        // v6: разговор без кнопки, утренняя сводка
const FILES = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/icon-180.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/")) return;   // разговор — всегда по сети
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/")));                    // свежая страница, без сети — из кэша
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});

// Уведомления (напоминания и таймеры) — приходят, даже когда приложение закрыто
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Atlas", {
    body: d.body || "", tag: d.tag || "atlas", renotify: true, icon: "/icon-192.png", badge: "/icon-192.png",
    vibrate: [180, 80, 180], data: { url: d.url || "/" },
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) if ("focus" in c) return c.focus();
    return self.clients.openWindow(url);
  }));
});
