// Keeps a signed-in reader's saved stories and follows (publishers, topics) in step with their
// account on the server, so they follow them across browsers and the Android app.
//
// localStorage stays what the UI reads (instant, works offline). Signed in, every change is
// also sent to the backend; changes that fail (offline, rate limit) wait in a small queue and
// are retried on the next sync. On sign-in and on each page load the account's lists are pulled
// down. The first time an account syncs in this browser, what was saved here before signing in
// is merged in rather than thrown away.
import { idToken } from "./auth.js";
import { slug, sourceList } from "./api.js";

const SAVED = "oin-saved", FOLLOW = "oin-follow", NAMES = "oin-follow-names", QUEUE = "oin-sync-q", MARK = "oin-sync", SESSION = "oin-session", READ = "oin-read", READ_AT = "oin-read-at";

const rd = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } };
const wr = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode / full */ } };
const userId = () => (rd(SESSION, null) || {}).user_id || null;

class HttpError extends Error { constructor(status) { super("HTTP " + status); this.status = status; } }

async function call(method, path, body) {
  const id = userId();
  if (!id) throw new HttpError(401);
  const token = await idToken();
  if (!token) throw new HttpError(401);
  const r = await fetch("/api/v1/users/" + encodeURIComponent(id) + path, {
    method, credentials: "same-origin", body: body ? JSON.stringify(body) : undefined,
    headers: { Authorization: "Bearer " + token, Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!r.ok) throw new HttpError(r.status);
  try { return await r.json(); } catch (e) { return null; }
}

// ---- one change, sent to the server ----------------------------------------------------
// op: { k: "save", id, on }  |  { k: "follow", key: "source:<slug>" | "topic:<slug>", on, name? }
const humanize = (sl) => sl.replace(/-+/g, " ").trim();
const topicText = (op, sl) => String(op.name || humanize(sl)).trim().slice(0, 80);

async function apply(op) {
  if (op.k === "save") return op.on ? call("POST", "/saved-stories", { cluster_id: Number(op.id) }) : call("DELETE", "/saved-stories/" + Number(op.id));
  const i = op.key.indexOf(":"), kind = op.key.slice(0, i), sl = op.key.slice(i + 1);
  if (kind === "source") {
    const s = (await sourceList()).find((x) => slug(x.name) === sl);
    if (!s) return null;                       // a publisher we no longer list: nothing to sync
    return call(op.on ? "POST" : "DELETE", "/sources/" + s.id + "/subscribe");
  }
  if (kind === "topic") {
    if (op.on) return call("POST", "/followed-topics", { topic: topicText(op, sl) });
    const row = (((await call("GET", "/followed-topics")) || {}).items || []).find((t) => slug(t.topic) === sl);
    return row ? call("DELETE", "/followed-topics/" + row.id) : null;
  }
  return null;
}

// ---- retry queue ----------------------------------------------------------------------
const same = (a, b) => a.k === b.k && (a.k === "save" ? Number(a.id) === Number(b.id) : a.key === b.key);
function enqueue(op) { wr(QUEUE, rd(QUEUE, []).filter((x) => !same(x, op)).concat([op])); }
// 4xx other than "not signed in" / "slow down" will never succeed (cluster gone, follow limit)
const permanent = (e) => e && e.status >= 400 && e.status < 500 && e.status !== 401 && e.status !== 429;

async function flush() {
  for (const op of rd(QUEUE, [])) {
    try { await apply(op); } catch (e) { if (!permanent(e)) return false; }
    wr(QUEUE, rd(QUEUE, []).filter((x) => !same(x, op)));
  }
  return true;
}

// Called by the UI right after it changes localStorage. Never throws.
export async function push(op) {
  if (!userId()) return;
  if (op.k === "follow" && op.name) wr(NAMES, { ...rd(NAMES, {}), [op.key]: op.name });
  try { await apply(op); } catch (e) { if (!permanent(e)) enqueue(op); }
}

// ---- pull + reconcile -----------------------------------------------------------------
async function pull() {
  const ids = [];
  let cursor = null;
  for (let page = 0; page < 6; page++) {      // up to 300 saved stories
    const r = (await call("GET", "/saved-stories?limit=50" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""))) || {};
    (r.items || []).forEach((x) => { if (x.cluster) ids.push(x.cluster.id); });
    if (!r.has_more || !r.next_cursor) break;
    cursor = r.next_cursor;
  }
  const topics = (((await call("GET", "/followed-topics")) || {}).items || []);
  const subs = (((await call("GET", "/sources/subscribed")) || {}).items || []);
  const names = {};
  topics.forEach((t) => { names["topic:" + slug(t.topic)] = t.topic; });
  return {
    saved: ids.reverse(),                     // the server lists newest first; the web keeps oldest first
    follow: subs.map((x) => "source:" + slug(x.source.name)).concat(topics.map((t) => "topic:" + slug(t.topic))),
    names,
  };
}

// Puts still-queued changes back on top of what the server said, so an offline change isn't undone.
function withQueue(saved, follow) {
  rd(QUEUE, []).forEach((op) => {
    if (op.k === "save") { const i = saved.indexOf(Number(op.id)); if (op.on && i < 0) saved.push(Number(op.id)); if (!op.on && i > -1) saved.splice(i, 1); }
    else { const i = follow.indexOf(op.key); if (op.on && i < 0) follow.push(op.key); if (!op.on && i > -1) follow.splice(i, 1); }
  });
}

let running = null;
// Returns true when the lists on this device changed (so the page can refresh). Never throws.
export function syncNow() {
  if (!userId()) return Promise.resolve(false);
  if (!running) running = run().catch(() => false).finally(() => { running = null; });
  return running;
}

async function run() {
  const id = userId();
  const before = JSON.stringify([rd(SAVED, []), rd(FOLLOW, [])]);
  await flush();
  const remote = await pull();
  let saved = remote.saved.map(Number), follow = remote.follow;
  const names = { ...rd(NAMES, {}), ...remote.names };

  if ((rd(MARK, {}) || {}).user !== id) {
    // First sync of this account here: keep what was saved/followed before signing in.
    const ls = rd(SAVED, []).map(Number), lf = rd(FOLLOW, []);
    ls.filter((x) => saved.indexOf(x) < 0).forEach((x) => { saved.push(x); enqueue({ k: "save", id: x, on: true }); });
    lf.filter((k) => follow.indexOf(k) < 0).forEach((k) => { follow.push(k); enqueue({ k: "follow", key: k, on: true, name: names[k] }); });
    await flush();
  }
  withQueue(saved, follow);
  wr(SAVED, saved); wr(FOLLOW, follow); wr(NAMES, names); wr(MARK, { user: id, at: Date.now() });
  return JSON.stringify([saved, follow]) !== before;
}

// Signing out must not leave this reader's lists behind for the next person on the browser.
export function clearLocal() {
  [SAVED, FOLLOW, NAMES, QUEUE, MARK, READ, READ_AT].forEach((k) => { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } });
}
