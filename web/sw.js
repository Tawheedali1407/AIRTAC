// AIRTAC Advance service worker: keeps the page itself (HTML, CSS, script, icons) so the app opens
// instantly and says something useful when the PC is off. Answers, files and your data are never
// cached here: /api/ and /files/ always go to AIRTAC on your PC.
const SHELL = "airtac-shell-v19";  // a new name clears the old copy (and the old logo) on update
const V = "?v=g7";
const FILES = ["./", "index.html", "login.html", "logo.svg", ...["style.css", "boot.css", "boot.js", "hub.js", "vault.js", "app.js", "dock.css", "dock.js", "tabs.js", "attach.js", "devices.js", "chat.js", "chat.css", "sections.js", "crm.js", "dictation.js", "connectors.js", "extras.js", "reverse.js", "view.js", "login.js", "favicon.svg", "manifest.webmanifest",
  "icons/airtac-192.png", "icons/airtac-512.png", "icons/airtac-wheel.ico"].map((f) => f + V)];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/files/")) return;  // always live
  // The page: network first (so updates show up), the saved copy when the PC can't be reached.
  // cache: "no-cache" asks AIRTAC whether the file changed every time, so an update shows up on the next open
  // (Ali, 18 Oct: "the app I installed isn't updating").
  event.respondWith(fetch(event.request, { cache: "no-cache" }).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(event.request, copy)); }
    return res;
  }).catch(() => caches.match(event.request, { ignoreSearch: true }).then((hit) => hit || caches.match("index.html"))));
});
