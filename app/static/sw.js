// Open Indian News service worker. It exists so browsers treat the site as an installable app
// and so an offline launch shows a friendly page instead of the browser's error. It does not
// cache stories or API data: the news must always be live.
const OFFLINE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline - Open Indian News</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0A0A0A;color:#fff;font:16px/1.5 system-ui,sans-serif;text-align:center;padding:24px}
h1{font-size:22px;margin:0 0 8px}p{margin:0 0 20px;color:#A0A0A8}button{font:inherit;font-weight:700;border:0;border-radius:999px;padding:12px 24px;background:#fff;color:#0A0A0A}</style></head>
<body><div><h1>You're offline</h1><p>Open Indian News needs a connection to bring you live stories.</p><button onclick="location.reload()">Try again</button></div></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;   // everything else goes straight to the network
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
