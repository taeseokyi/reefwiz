const CACHE = "aquawiz-v2";
const SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./vendor/chart.umd.min.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// 그래프/최신값은 네트워크 우선(최신 반영) + 실패 시 캐시(오프라인 대비).
// 나머지(앱 셸)는 캐시 우선.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isLiveData = url.pathname.includes("dkh_trend") ||
    url.pathname.endsWith("dkh_latest.json") ||
    url.pathname.endsWith("dkh_series.json") ||
    url.pathname.endsWith("dkh_plateau.json");

  if (isLiveData) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
