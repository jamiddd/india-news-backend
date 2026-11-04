import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/auth.js", () => ({ idToken: async () => "tok-123" }));
import * as api from "../src/api.js";
import { push, syncNow, clearLocal } from "../src/userData.js";

// A tiny fake backend: records calls, and holds one account's saved stories and follows.
function server(init = {}) {
  const st = { saved: init.saved || [], topics: init.topics || [], subs: init.subs || [], calls: [], fail: null, nextId: 100 };
  const sources = [{ id: 7, name: "The Hindu" }, { id: 9, name: "Nagaland Post" }];
  const f = vi.fn(async (url, opts = {}) => {
    const m = (opts.method || "GET").toUpperCase(), u = String(url);
    st.calls.push({ m, u, auth: opts.headers && opts.headers.Authorization, body: opts.body ? JSON.parse(opts.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u === "/api/v1/sources") return ok(sources);
    if (st.fail && u.includes("/users/")) return { ok: false, status: st.fail, json: async () => ({}) };
    let r;
    if ((r = u.match(/\/saved-stories\/(\d+)$/)) && m === "DELETE") { st.saved = st.saved.filter((x) => x !== +r[1]); return ok({}); }
    if (u.endsWith("/saved-stories") && m === "POST") { const id = JSON.parse(opts.body).cluster_id; if (!st.saved.includes(id)) st.saved.unshift(id); return ok({}); }
    if (u.includes("/saved-stories?")) return ok({ items: st.saved.map((id) => ({ cluster: { id } })), has_more: false, next_cursor: null });
    if (u.endsWith("/followed-topics") && m === "POST") { st.topics.push({ id: st.nextId++, topic: JSON.parse(opts.body).topic }); return ok({}); }
    if (u.endsWith("/followed-topics")) return ok({ items: st.topics });
    if ((r = u.match(/\/followed-topics\/(\d+)$/))) { st.topics = st.topics.filter((t) => t.id !== +r[1]); return ok({}); }
    if ((r = u.match(/\/sources\/(\d+)\/subscribe$/))) { st.subs = m === "POST" ? st.subs.concat([+r[1]]) : st.subs.filter((x) => x !== +r[1]); return ok({}); }
    if (u.endsWith("/sources/subscribed")) return ok({ items: st.subs.map((id) => ({ id, source: sources.find((s) => s.id === id) })) });
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", f);
  return st;
}

const store = {};
beforeEach(() => {
  for (const k of Object.keys(store)) delete store[k];
  vi.stubGlobal("localStorage", { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } });
  api.clearCache();
  store["oin-session"] = JSON.stringify({ user_id: "usr_abc", name: "T", email: "t@x.co" });
});
const ls = (k) => JSON.parse(store[k] || "null");

describe("userData sync", () => {
  it("sends a save with the bearer token, and a removal as a DELETE", async () => {
    const s = server();
    await push({ k: "save", id: 41, on: true });
    await push({ k: "save", id: 41, on: false });
    expect(s.calls[0]).toMatchObject({ m: "POST", u: "/api/v1/users/usr_abc/saved-stories", auth: "Bearer tok-123", body: { cluster_id: 41 } });
    expect(s.calls[1]).toMatchObject({ m: "DELETE", u: "/api/v1/users/usr_abc/saved-stories/41" });
  });

  it("does nothing while signed out", async () => {
    const s = server(); delete store["oin-session"];
    await push({ k: "save", id: 1, on: true });
    expect(await syncNow()).toBe(false);
    expect(s.calls).toHaveLength(0);
  });

  it("queues a change that fails and retries it on the next sync", async () => {
    const s = server(); s.fail = 503;
    store["oin-saved"] = "[5]";
    await push({ k: "save", id: 5, on: true });
    expect(ls("oin-sync-q")).toEqual([{ k: "save", id: 5, on: true }]);
    s.fail = null;
    await syncNow();
    expect(s.saved).toContain(5);
    expect(ls("oin-sync-q")).toEqual([]);
  });

  it("drops a change the server will never accept instead of retrying forever", async () => {
    const s = server(); s.fail = 404;
    await push({ k: "save", id: 999, on: true });
    expect(ls("oin-sync-q")).toBeNull();
  });

  it("on the first sync merges what was saved before signing in with the account", async () => {
    const s = server({ saved: [3, 2], subs: [7] });          // server lists newest first
    store["oin-saved"] = "[2,10]"; store["oin-follow"] = JSON.stringify(["topic:monetary-policy"]);
    expect(await syncNow()).toBe(true);
    expect(ls("oin-saved").sort((a, b) => a - b)).toEqual([2, 3, 10]);
    expect(ls("oin-follow").sort()).toEqual(["source:the-hindu", "topic:monetary-policy"]);
    expect(s.saved).toContain(10);                           // pushed up
    expect(s.topics.map((t) => t.topic)).toEqual(["monetary policy"]);
    expect(ls("oin-sync").user).toBe("usr_abc");
  });

  it("after the first sync the server is the truth: a story removed elsewhere disappears here", async () => {
    server({ saved: [3] });
    store["oin-sync"] = JSON.stringify({ user: "usr_abc", at: 1 });
    store["oin-saved"] = "[3,4]";
    await syncNow();
    expect(ls("oin-saved")).toEqual([3]);
  });

  it("keeps a not-yet-sent change on top of the server's lists", async () => {
    const s = server({ saved: [3] }); s.fail = null;
    store["oin-sync"] = JSON.stringify({ user: "usr_abc", at: 1 });
    store["oin-sync-q"] = JSON.stringify([{ k: "save", id: 8, on: true }]);
    await syncNow();
    expect(ls("oin-saved").sort((a, b) => a - b)).toEqual([3, 8]);
  });

  it("follows a publisher by looking its id up from the sources list", async () => {
    const s = server();
    await push({ k: "follow", key: "source:nagaland-post", on: true });
    await push({ k: "follow", key: "source:nagaland-post", on: false });
    expect(s.calls.filter((c) => c.u.includes("/subscribe")).map((c) => c.m + " " + c.u)).toEqual([
      "POST /api/v1/users/usr_abc/sources/9/subscribe", "DELETE /api/v1/users/usr_abc/sources/9/subscribe"]);
  });

  it("unfollows a topic by finding its server id, and follows with the display name", async () => {
    const s = server({ topics: [{ id: 55, topic: "Monetary Policy" }] });
    await push({ k: "follow", key: "topic:monetary-policy", on: false });
    expect(s.topics).toEqual([]);
    await push({ k: "follow", key: "topic:ipl-2026", on: true, name: "IPL 2026" });
    expect(s.topics.map((t) => t.topic)).toEqual(["IPL 2026"]);
  });

  it("clearLocal removes this reader's lists and history", () => {
    store["oin-saved"] = "[1]"; store["oin-follow"] = "[]"; store["oin-read"] = "[1]"; store["oin-sync"] = "{}";
    clearLocal();
    expect(["oin-saved", "oin-follow", "oin-read", "oin-sync"].every((k) => !(k in store))).toBe(true);
    expect("oin-session" in store).toBe(true);
  });
});
