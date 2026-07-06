const CACHE = "aquawiz-v3";
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

// 전부 네트워크 우선 — 배포하면 바로 최신이 보이는 게 우선(캐시는 오프라인 대체용일 뿐).
// index.html도 캐시 우선으로 두면 배포 후에도 예전 화면이 계속 보이는 문제가 생김(2026-07-01 실제 발생).
// 같은 출처 GET만 다룬다 — GitHub API 호출(도저 수동 설정 GET/PUT, 2026-07-06)은 SW를
// 거치지 않게 하고, non-GET 을 cache.put 하려다 나는 조용한 예외도 없앤다.
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || !event.request.url.startsWith(self.location.origin)) return;
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
