// 마켓 펄스 서비스워커: 화면 파일만 저장해 두고(오프라인에서도 열림), 뉴스·시세 데이터는 항상 새로 받음
const CACHE = "mp-shell-v4";
const SHELL = ["./", "index.html", "assets/style.css", "assets/app.js", "manifest.webmanifest", "icons/icon-192.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // 다른 주소(데이터)·쿼리가 붙은 요청·GET 이 아닌 요청은 건드리지 않음
  if (e.request.method !== "GET" || url.origin !== location.origin || url.search) return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then((r) => r || (e.request.mode === "navigate" ? caches.match("index.html") : Response.error()))));
});
