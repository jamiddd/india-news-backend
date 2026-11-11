// Open Indian News service worker. It makes the site installable as an app, shows a friendly page
// on an offline launch, and keeps publisher images so they aren't downloaded again on every visit
// (many publishers send no-store or short cache lifetimes). It does not cache stories or API data:
// the news must always be live.
const OFFLINE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline - Open Indian News</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0A0A0A;color:#fff;font:16px/1.5 system-ui,sans-serif;text-align:center;padding:24px}
h1{font-size:22px;margin:0 0 8px}p{margin:0 0 20px;color:#A0A0A8}button{font:inherit;font-weight:700;border:0;border-radius:999px;padding:12px 24px;background:#fff;color:#0A0A0A}</style></head>
<body><div><h1>You're offline</h1><p>Open Indian News needs a connection to bring you live stories.</p><button onclick="location.reload()">Try again</button></div></body></html>`;

// ---- publisher images ------------------------------------------------------------------
// Cross-origin <img> responses are opaque (status unreadable), so a bad one can't be told from a
// good one: entries expire after 3 days, the cache is capped, and the page asks us to drop any
// image that failed to display (see components/Photo.jsx).
const IMG = "oin-img-v1", META = "oin-img-meta-v1", MAX_IMAGES = 80, TTL = 3 * 24 * 3600 * 1000;
const readMeta = async () => { try { const r = await (await caches.open(META)).match("/meta"); return r ? await r.json() : {}; } catch (e) { return {}; } };
const writeMeta = async (m) => { try { await (await caches.open(META)).put("/meta", new Response(JSON.stringify(m), { headers: { "Content-Type": "application/json" } })); } catch (e) { /* quota */ } };

async function trim(cache, meta) {
  const keys = (await cache.keys()).map((r) => r.url);
  if (keys.length <= MAX_IMAGES) return;
  keys.sort((a, b) => (meta[a] || 0) - (meta[b] || 0));
  for (const u of keys.slice(0, keys.length - MAX_IMAGES)) { await cache.delete(u); delete meta[u]; }
}

async function image(req) {
  const cache = await caches.open(IMG), key = req.url, meta = await readMeta(), hit = await cache.match(key);
  if (hit && meta[key] && Date.now() - meta[key] < TTL) return hit;
  try {
    const res = await fetch(req);
    if (res && (res.ok || res.type === "opaque")) {
      await cache.put(key, res.clone());
      meta[key] = Date.now();
      await trim(cache, meta);
      await writeMeta(meta);
    }
    return res;
  } catch (e) {
    if (hit) return hit;                         // offline: a stale picture beats a broken one
    throw e;
  }
}

self.addEventListener("message", (e) => {
  const u = e.data && e.data.evict;
  if (u) e.waitUntil(Promise.all([caches.open(IMG).then((c) => c.delete(u)), readMeta().then((m) => { delete m[u]; return writeMeta(m); })]));
});

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const r = e.request;
  if (r.method === "GET" && r.destination === "image") {
    const u = new URL(r.url);
    if (u.protocol === "https:" && u.origin !== self.location.origin) e.respondWith(image(r));   // our own files are HTTP-cached already
    return;
  }
  if (r.mode !== "navigate") return;           // everything else goes straight to the network
  e.respondWith(fetch(r).catch(() => new Response(OFFLINE, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })));
});

// ---- push notifications ----------------------------------------------------------------
// The backend sends data-only FCM messages (title, body, cluster_id, channel_id, type, ...), so the
// worker builds the notification itself, as the Android app does. Where a tap goes:
//   cluster_id > 0         -> the story
//   type topic_update      -> that topic's page
//   url (admin alerts)     -> that path, same site only
//   anything else          -> the front page
const slugOf = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
function pushTarget(d) {
  const id = parseInt(d.cluster_id, 10);
  if (id > 0) return "/story/" + id;
  if (d.type === "topic_update" && d.topic) return "/topic/" + slugOf(d.topic);
  if (d.url) { try { const u = new URL(d.url, self.location.origin); if (u.origin === self.location.origin) return u.pathname + u.search; } catch (e) { /* ignore */ } }
  return "/";
}

self.addEventListener("push", (e) => {
  let p = {};
  try { p = e.data ? e.data.json() : {}; } catch (err) { /* not JSON */ }
  const d = p.data || p.notification || p;
  const title = d.title || "Open Indian News", id = parseInt(d.cluster_id, 10);
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || "",
    icon: "/icon-192.png",
    badge: "/favicon-48.png",
    tag: id > 0 ? "story-" + id : d.type || d.channel_id || "oin",
    renotify: true,
    data: { url: pushTarget(d) },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if (new URL(c.url).origin === self.location.origin && "focus" in c) {
        return c.focus().then((w) => (w && "navigate" in w ? w.navigate(url) : w));
      }
    }
    return self.clients.openWindow(url);
  }));
});
