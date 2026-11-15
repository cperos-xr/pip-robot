// Offline placeholder for the robot's web app (robotd/web/sw.js, served at /sw.js).
// When a page load cannot reach the robot (the Cloudflare tunnel answers 530/502/503/504, or there is no network at
// all), the browser shows the cached offline.html instead of a Cloudflare error. It only touches page loads
// (navigations): API calls, the live camera stream and everything else go straight to the robot as before.
// Works in browsers that opened the site once while the robot was online. Bump VERSION when offline.html changes.
const VERSION = "pip-offline-v1";
const OFFLINE = "/offline.html";
const DOWN = new Set([502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530]);

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.add(new Request(OFFLINE, {cache: "reload"}))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith("pip-offline-") && k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const offline = () => caches.match(OFFLINE).then(r => r || new Response(
  "<!doctype html><meta charset=utf-8><title>Pip is offline</title><p>Pip is offline. Try again in a minute.</p>",
  {status: 503, headers: {"Content-Type": "text/html; charset=utf-8"}}));

self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).then(r => DOWN.has(r.status) ? offline() : r).catch(offline));
});
