import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Loads app/static/sw.js against a fake Cache Storage and a fake network.
const SRC = readFileSync(new URL("../../app/static/sw.js", import.meta.url), "utf8");

function boot() {
  const stores = new Map(), handlers = {};
  const open = async (name) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const m = stores.get(name);
    return {
      match: async (k) => { const v = m.get(String(k)); return v instanceof Response ? v.clone() : v; },
      put: async (k, v) => { m.set(String(k), v); },
      delete: async (k) => m.delete(String(k)),
      keys: async () => [...m.keys()].map((url) => ({ url })),
    };
  };
  const net = { calls: [], impl: async () => ({ ok: true, type: "opaque", clone() { return this; } }) };
  const fetchFn = vi.fn(async (req) => { net.calls.push(req.url); return net.impl(req); });
  const self = { location: { origin: "https://openindiannews.com" }, addEventListener: (t, f) => { handlers[t] = f; }, skipWaiting() {}, clients: { claim() {} } };
  new Function("self", "caches", "fetch", "Response", "URL", SRC)(self, { open }, fetchFn, Response, URL);
  const ask = (url, extra = {}) => {
    let p = null;
    handlers.fetch({ request: { url, method: "GET", destination: "image", mode: "no-cors", ...extra }, respondWith: (x) => { p = x; } });
    return p;
  };
  return { stores, net, ask, handlers, fetchFn };
}

const IMG = "https://img.publisher.com/a.jpg";
let sw;
beforeEach(() => { sw = boot(); });

describe("service worker image cache", () => {
  it("downloads an image once and serves repeat requests from the cache", async () => {
    await sw.ask(IMG); await sw.ask(IMG); await sw.ask(IMG);
    expect(sw.net.calls).toEqual([IMG]);
  });

  it("downloads again once an entry is older than three days", async () => {
    await sw.ask(IMG);
    const meta = sw.stores.get("oin-img-meta-v1").get("/meta"); const m = await meta.clone().json();
    m[IMG] = Date.now() - 4 * 24 * 3600 * 1000;
    sw.stores.get("oin-img-meta-v1").set("/meta", new Response(JSON.stringify(m)));
    await sw.ask(IMG);
    expect(sw.net.calls).toEqual([IMG, IMG]);
  });

  it("does not keep an error response", async () => {
    sw.net.impl = async () => ({ ok: false, status: 404, type: "basic", clone() { return this; } });
    await sw.ask(IMG); await sw.ask(IMG);
    expect(sw.net.calls).toHaveLength(2);
    expect(sw.stores.get("oin-img-v1").size).toBe(0);
  });

  it("keeps only the newest 80 images", async () => {
    for (let i = 0; i < 83; i++) { await sw.ask("https://img.publisher.com/" + i + ".jpg"); await new Promise((r) => setTimeout(r, 1)); }
    const keys = [...sw.stores.get("oin-img-v1").keys()];
    expect(keys).toHaveLength(80);
    expect(keys).not.toContain("https://img.publisher.com/0.jpg");
    expect(keys).toContain("https://img.publisher.com/82.jpg");
  });

  it("shows a stale picture rather than nothing when offline", async () => {
    await sw.ask(IMG);
    const m = await sw.stores.get("oin-img-meta-v1").get("/meta").clone().json(); m[IMG] = 1;
    sw.stores.get("oin-img-meta-v1").set("/meta", new Response(JSON.stringify(m)));
    sw.net.impl = async () => { throw new TypeError("offline"); };
    await expect(sw.ask(IMG)).resolves.toBeTruthy();
  });

  it("drops an image the page reports as broken", async () => {
    await sw.ask(IMG);
    let done; sw.handlers.message({ data: { evict: IMG }, waitUntil: (p) => { done = p; } });
    await done;
    expect(sw.stores.get("oin-img-v1").has(IMG)).toBe(false);
    await sw.ask(IMG);
    expect(sw.net.calls).toEqual([IMG, IMG]);
  });

  it("leaves our own images, non-images and plain http alone", () => {
    expect(sw.ask("https://openindiannews.com/static/img/a.png")).toBeNull();
    expect(sw.ask("http://img.publisher.com/a.jpg")).toBeNull();
    expect(sw.ask("https://img.publisher.com/a.json", { destination: "empty", mode: "cors" })).toBeNull();
    expect(sw.net.calls).toHaveLength(0);
  });
});
