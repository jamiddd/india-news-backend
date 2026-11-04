// Data layer for the SPA: a small same-origin JSON client for the public /api/v1
// endpoints, adapters that turn their responses into the shapes the pages were
// built against (prototype/data/sample.json), and one loader per page.
//
// Dev modes (see vite.config.js):
//   npm run dev          -> /api/v1/* proxied to API_PROXY_TARGET (default a local
//                           backend on 127.0.0.1:8000)
//   npm run dev:sample   -> (VITE_USE_SAMPLE=1) fully offline: every loader reads
//                           /api/sample, which the dev server serves from
//                           prototype/data/sample.json. Pages call the same loaders
//                           either way. Don't ship a build made with the flag: prod
//                           has no /api/sample route.
//
// No React in here, so the adapters can be unit tested in plain node
// (web/test/api.test.js).

const ENV = (typeof import.meta !== "undefined" && import.meta.env) || {};
export const USE_SAMPLE = ENV.VITE_USE_SAMPLE === "1" || ENV.VITE_USE_SAMPLE === "true";
const V1 = "/api/v1";

// ---- client -----------------------------------------------------------------

export class ApiError extends Error {
  constructor(status, message, path) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

const cache = new Map(); // path -> { at, promise }
const TTL_MS = 60 * 1000;

// GET a same-origin JSON resource. Identical concurrent calls share one request,
// and a success is reused for TTL_MS; failures are never cached.
export function getJSON(path, { ttl = TTL_MS, fetchImpl } = {}) {
  const now = Date.now();
  const hit = cache.get(path);
  if (hit && now - hit.at < ttl) return hit.promise;
  const f = fetchImpl || globalThis.fetch;
  const promise = Promise.resolve()
    .then(() => f(path, { headers: { Accept: "application/json" }, credentials: "same-origin" }))
    .then(async (r) => {
      if (!r.ok) {
        let msg = r.status + " " + (r.statusText || "error");
        try { const b = await r.json(); if (b && b.detail) msg = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail); } catch (e) { /* not JSON */ }
        throw new ApiError(r.status, msg, path);
      }
      try { return await r.json(); } catch (e) { throw new ApiError(r.status, "Response was not JSON", path); }
    }, (e) => { throw new ApiError(0, "Network error: " + ((e && e.message) || "request failed"), path); });
  cache.set(path, { at: now, promise });
  promise.catch(() => { if (cache.get(path) && cache.get(path).promise === promise) cache.delete(path); });
  return promise;
}
export function clearCache() { cache.clear(); }

const notFound = (e) => e instanceof ApiError && e.status === 404;
const qs = (o) => Object.keys(o).filter((k) => o[k] != null && o[k] !== "").map((k) => k + "=" + encodeURIComponent(o[k])).join("&");

// Runs fn over items with at most n in flight (keeps per-id lookups under the rate limit).
async function mapLimit(items, n, fn) {
  const out = new Array(items.length);
  let i = 0;
  const worker = async () => { while (i < items.length) { const j = i++; out[j] = await fn(items[j], j); } };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return out;
}

// ---- small helpers ------------------------------------------------------------

export function slug(n) { return String(n).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""); }
export function titleCase(s) {
  if (!s) return "";
  return String(s).replace(/[_]+/g, " ").replace(/\b([a-z])/g, (m) => m.toUpperCase());
}
export function minutesAgo(ts, now = Date.now()) {
  const t = ts ? Date.parse(ts) : NaN;
  if (isNaN(t)) return 0;
  return Math.max(0, Math.round((now - t) / 60000));
}
export function agoShort(m) { return m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : Math.round(m / 1440) + "d"; }
function updatedLabel(ts, now) { return ts ? "Updated " + agoShort(minutesAgo(ts, now)) + " ago" : ""; }
function stripHtml(s) { return String(s || "").replace(/<[^>]*>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim(); }
function clip(s, n) { return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s; }
export function firstSentence(s) {
  const t = stripHtml(s);
  const m = t.match(/^.+?[.!?](?=\s|$)/);
  return m ? m[0] : t;
}

// StoryCluster.summary is stored as "\n• bullet\n• bullet" (see apply_baseline_enrichment).
export function splitBullets(summary) {
  if (!summary) return [];
  const s = String(summary);
  const parts = s.indexOf("•") > -1 ? s.split(/\s*•\s*/) : s.split(/\n+/);
  return parts.map((x) => x.trim()).filter(Boolean);
}

// ---- sources (outlet -> category/region/language) ---------------------------

// SourceOut[] -> lookup by id. Story clusters carry no category of their own;
// the API derives categories from the publisher (Source.category), so the web
// client does the same.
// Outlet name -> homepage domain, filled whenever the sources list is indexed. Source logos
// are the publisher's favicon (same approach as the Android app), so no backend field is needed.
const HOME_BY_NAME = {};
export function faviconFor(name) {
  const home = HOME_BY_NAME[String(name || "").toLowerCase()];
  if (!home) return null;
  try { return "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(new URL(home).hostname.replace(/^www\./, "")) + "&sz=64"; } catch (e) { return null; }
}

export function indexSources(list) {
  const byId = {};
  (list || []).forEach((s) => { byId[s.id] = s; if (s.name && s.homepage_url) HOME_BY_NAME[String(s.name).toLowerCase()] = s.homepage_url; });
  return { byId, list: list || [] };
}
const EMPTY_SOURCES = indexSources([]);

export function deriveCategory(articles, sources = EMPTY_SOURCES, fallback = "News") {
  const counts = {}, order = [];
  (articles || []).forEach((a) => {
    const src = sources.byId[a.source_id];
    const c = src && src.category;
    if (!c) return;
    if (!(c in counts)) { counts[c] = 0; order.push(c); }
    counts[c]++;
  });
  if (!order.length) return fallback;
  let best = order[0];
  order.forEach((c) => { if (counts[c] > counts[best]) best = c; });
  return titleCase(best);
}

// ---- story clusters -> sample "story" ------------------------------------------

const ENTITY_KIND = { persons: "person", organizations: "organisation", locations: "place" };
export function adaptTopics(topics, entities) {
  const out = [], seen = {};
  const add = (label, kind) => {
    const l = String(label || "").trim();
    if (!l || seen[l.toLowerCase()]) return;
    seen[l.toLowerCase()] = 1; out.push({ label: l, kind });
  };
  (Array.isArray(topics) ? topics : []).forEach((t) => {
    if (typeof t === "string") add(t, "topic");
    else if (t && (t.label || t.name)) add(t.label || t.name, t.kind || "topic");
  });
  if (entities && typeof entities === "object" && !Array.isArray(entities)) {
    Object.keys(entities).forEach((k) => {
      if (k === "backdrop" || !Array.isArray(entities[k])) return;
      const kind = ENTITY_KIND[k] || k.replace(/s$/, "").replace(/_/g, " ");
      entities[k].forEach((n) => add(typeof n === "string" ? n : n && n.name, kind));
    });
  }
  return out;
}

export function adaptMedia(articles) {
  // Images lead and videos come last, but the cap reserves room at the end for them: a big story
  // can have a dozen articles' images, which used to push a later video out of the list.
  const videos = [], images = [], seen = {};
  (articles || []).forEach((a) => {
    if (a.video_url || a.has_pending_video) {
      const key = a.video_url || "pending:" + a.id;
      if (!seen[key]) { seen[key] = 1; videos.push({ kind: "video", label: "video", src: a.video_url || null, poster: a.image_url || null, article_id: a.id, duration: a.video_duration_seconds || null, short: !!a.video_is_short }); }
    }
    const imgs = (a.image_urls && a.image_urls.length ? a.image_urls : a.image_url ? [a.image_url] : []);
    // A video article's own thumbnail is the video's poster, not a separate picture.
    const hasVideo = !!(a.video_url || a.has_pending_video);
    imgs.forEach((u) => { if (u && !seen[u] && !(hasVideo && u === a.image_url)) { seen[u] = 1; images.push({ kind: "image", label: "image", src: u }); } });
  });
  const tail = videos.slice(0, 3);
  return images.slice(0, 8 - tail.length).concat(tail);
}

export function adaptFraming(fc) {
  return (Array.isArray(fc) ? fc : []).filter((f) => f && f.outlet).map((f) => ({ outlet: f.outlet, angle: f.headline_angle || f.angle || "", tag: f.tag || "" }));
}

// StoryClusterOut | StoryClusterListOut -> sample story.
export function adaptCluster(c, { sources = EMPTY_SOURCES, now = Date.now(), category } = {}) {
  const arts = c.articles || [];
  const outlets = {};
  arts.forEach((a) => { outlets[a.source_id != null ? a.source_id : a.source_name] = 1; });
  const outletCount = Object.keys(outlets).length || c.article_count || 0;
  const lead = arts.filter((a) => a.snippet)[0];
  const video = arts.filter((a) => a.video_url || a.has_pending_video)[0];
  const imageArt = arts.filter((a) => a.image_url)[0];
  return {
    id: c.id,
    headline: c.headline || (arts[0] && arts[0].title) || "Untitled story",
    category: category || deriveCategory(arts, sources),
    outlet_count: outletCount,
    article_count: c.article_count || arts.length,
    minutes_ago: minutesAgo(c.last_updated_at, now),
    first_seen_minutes_ago: minutesAgo(c.first_seen_at, now),
    dek: lead ? clip(stripHtml(lead.snippet), 280) : "",
    summary: splitBullets(c.summary),
    framing: adaptFraming(c.framing_comparison),
    // True when the detail says enrichment has not run yet (framing may still arrive).
    framing_pending: c.ai_enriched === false,
    coverage: arts.map((a) => ({ outlet: a.source_name || "Unknown", title: a.title || "", minutes_ago: minutesAgo(a.published_at, now), url: a.url || null })),
    topics: adaptTopics(c.topics, c.entities),
    media: adaptMedia(arts),
    image: imageArt ? imageArt.image_url : null,
    video: video ? { outlet: video.source_name, duration: video.video_duration_seconds || null, article_id: video.id, src: video.video_url || null, poster: video.image_url || (imageArt && imageArt.image_url) || null } : null,
  };
}

// Most covered = most distinct outlets; ties keep the API's own ranking order.
export function mostCovered(stories) {
  let best = null;
  stories.forEach((s) => { if (!best || s.outlet_count > best.outlet_count) best = s; });
  return best;
}

// Front-page order (project memory: left = latest, middle = ONE most-covered
// story, right = top / most covered). The page puts S[0] in the middle and
// S[1..] in the Latest column, and sorts its own "Most covered" list, so: the
// most-covered story (within the active category) first, the rest newest first.
export function orderFrontPage(stories, category) {
  const pool = !category || category === "All" || category === "For you" ? stories : stories.filter((s) => s.category === category);
  const lead = mostCovered(pool);
  const rest = stories.filter((s) => s !== lead).sort((a, b) => a.minutes_ago - b.minutes_ago);
  return lead ? [lead].concat(rest) : rest;
}

// Tabs: "All" plus the six biggest categories; the rest go under "More".
export function categoryTabs(stories, max = 6) {
  const n = {};
  stories.forEach((s) => { n[s.category] = (n[s.category] || 0) + 1; });
  const ks = Object.keys(n).sort((a, b) => n[b] - n[a] || a.localeCompare(b));
  return { categories: ["All"].concat(ks.slice(0, max)), moreCategories: ks.slice(max) };
}

// ---- timelines -------------------------------------------------------------------

// TimelineFeatureListItemOut (+ optional TimelineFeatureDetailOut) -> sample timeline.
export function adaptTimeline(item, detail, { sources = EMPTY_SOURCES, now = Date.now() } = {}) {
  const base = item || detail || {};
  const d = detail || {};
  const beats = (d.beats || []).map((b) => ({ date: b.date_label || "", label: b.label || "", body: b.narration || "", stories: (b.clusters || []).map((c) => c.id) }));
  const anchorArts = (item && item.anchor_cluster && item.anchor_cluster.articles) || [];
  const beatArts = (d.beats || []).reduce((a, b) => a.concat(...(b.clusters || []).map((c) => c.articles || [])), []);
  const context = base.context || d.context || "";
  const anchorImg = anchorArts.filter((a) => a.image_url)[0];
  return {
    id: base.id,
    title: base.title || d.title || "",
    category: deriveCategory(anchorArts.length ? anchorArts : beatArts, sources, "Timeline"),
    updated: updatedLabel(base.narrative_generated_at || d.narrative_generated_at, now),
    dek: firstSentence(context),
    context,
    beats,
    points: beats.map((b) => ({ date: b.date, text: b.label })),
    story_count: (item && item.story_count) || beats.length,
    audio: d.audio_url ? { label: "Listen to this timeline", duration: d.audio_duration_seconds || 0, src: d.audio_url } : null,
    has_audio: !!(d.audio_url || (item && item.has_audio)),
    image: base.image_url || d.image_url || (anchorImg && anchorImg.image_url) || null,
    archived: !!(base.dropped_from_top_at || d.dropped_from_top_at),
  };
}
export function timelineStories(detail, ctx) {
  const out = [], seen = {};
  ((detail && detail.beats) || []).forEach((b) => (b.clusters || []).forEach((c) => { if (!seen[c.id]) { seen[c.id] = 1; out.push(adaptCluster(c, ctx)); } }));
  return out;
}

// ---- explainers --------------------------------------------------------------------

function paragraphs(body) { return String(body || "").split(/\n\s*\n|\n/).map((p) => p.trim()).filter(Boolean); }

export function adaptExplainerItem(x, { now = Date.now() } = {}) {
  const updated = updatedLabel(x.updated_at, now);
  return {
    id: x.id, title: x.question || "", category: titleCase(x.category) || "Explainer",
    dek: x.teaser || "", read: updated, updated, image: x.image_url || null, has_audio: !!x.has_audio,
    excerpt: "", points: [], quick: "", sections: [], sources: [], audio: null,
  };
}
export function adaptExplainerDetail(x, { now = Date.now() } = {}) {
  const sections = (x.sections || []).map((s) => ({ heading: s.heading || "", body: paragraphs(s.body) }));
  const words = [x.quick_answer || ""].concat(...sections.map((s) => s.body)).join(" ").split(/\s+/).filter(Boolean).length;
  const updated = updatedLabel(x.updated_at, now);
  return {
    id: x.id, title: x.question || "", category: titleCase(x.category) || "Explainer",
    dek: clip(stripHtml(x.quick_answer || ""), 160), quick: x.quick_answer || "",
    read: Math.max(1, Math.round(words / 200)) + " min read", updated,
    excerpt: (sections[0] && sections[0].body[0]) || "",
    // "In brief": the opening sentence of each of the first three sections.
    points: sections.slice(0, 3).map((s) => firstSentence(s.body[0] || "")).filter(Boolean),
    sections,
    sources: (x.sources || []).map((s) => s.cluster_id),
    image: x.hero_image_url || null,
    has_audio: !!x.audio_url,
    audio: x.audio_url ? { label: "Listen to this explainer", duration: x.audio_duration_seconds || 0, src: x.audio_url } : null,
  };
}
// ExplainerSourceOut -> minimal story so the page's "Sources" list and its
// attribution ("Outlet A, Outlet B and N others") work unchanged.
export function explainerSourceStories(x, category) {
  return (x.sources || []).map((s) => {
    const names = s.outlet_names && s.outlet_names.length ? s.outlet_names : [s.outlet];
    return {
      id: s.cluster_id, headline: s.title || "", category: titleCase(category) || "News",
      outlet_count: s.source_count || names.length, minutes_ago: 0, dek: "", summary: [], framing: [],
      coverage: names.map((n, i) => ({ outlet: n, title: s.title || "", minutes_ago: 0, url: (s.outlet_urls || [])[i] || (i === 0 ? s.url : null) || null })),
      topics: [], media: [],
    };
  });
}

// ---- small shared shapes -----------------------------------------------------------

export function adaptAnnouncements(r) { return ((r && r.items) || []).map((a) => ({ ...a, body: a.body || "" })); }

// WordOfTheDayOut / QuoteOfTheDayOut / DailyPollOut -> sample `daily`. Any of the
// three may be null (failed or unavailable); that key is then null too.
export function adaptDaily(word, quote, poll) {
  return {
    word: word ? { w: word.word, m: word.definition, pronunciation: word.pronunciation, part_of_speech: word.part_of_speech, example: word.example } : null,
    quote: quote ? "“" + quote.quote + "” — " + quote.author : null,
    poll: poll ? { id: poll.id, q: poll.question, a: (poll.options || []).map((o) => o.text), options: poll.options || [], closes_at: poll.closes_at } : null,
  };
}

// ---- raw fetchers (real API) -------------------------------------------------------

const sample = () => getJSON("/api/sample");
const sourcesStrict = () => getJSON(V1 + "/sources", { ttl: 10 * 60 * 1000 });
const sourcesRaw = () => sourcesStrict().catch(() => []);
export const sourceList = () => sourcesRaw();   // categories are optional: degrade to "News"
async function ctx() { return { sources: indexSources(await sourcesRaw()), now: Date.now() }; }
const feedPage = (o = {}) => getJSON(V1 + "/clusters?" + qs({ limit: 50, ...o }));

async function feedStories(c) {
  const cx = c || (await ctx());
  const page = await feedPage();
  return page.items.map((x) => adaptCluster(x, cx));
}

async function timelinesFull(cx) {
  const list = (await getJSON(V1 + "/timelines")).timelines || [];
  const details = await mapLimit(list, 3, (t) => getJSON(V1 + "/timelines/" + t.id).catch(() => null));
  return { list, details };
}

async function explainerList(cx) {
  const r = await getJSON(V1 + "/explainers");
  return (r.explainers || []).map((x) => adaptExplainerItem(x, cx));
}

// ---- page loaders --------------------------------------------------------------------

// Home: { stories, categories, moreCategories, timelines, explainers }
export async function loadHome({ category } = {}) {
  if (USE_SAMPLE) return sample();
  const cx = await ctx();
  const isTab = category && category !== "All" && category !== "For you";
  const [page, catPage, tl, exs] = await Promise.all([
    feedPage(),
    isTab ? feedPage({ category: category.toLowerCase() }).catch(() => null) : null,
    timelinesFull(cx).catch(() => ({ list: [], details: [] })),
    explainerList(cx).catch(() => []),
  ]);
  const byId = {};
  let stories = [];
  const push = (s) => { if (byId[s.id]) Object.assign(byId[s.id], { category: s.category }); else { byId[s.id] = s; stories.push(s); } };
  page.items.forEach((x) => push(adaptCluster(x, cx)));
  // A category tab's own page is authoritative for that tab, whatever the
  // majority-outlet derivation says.
  if (catPage) catPage.items.forEach((x) => push(adaptCluster(x, { ...cx, category })));
  stories = orderFrontPage(stories, isTab ? category : "All");
  // The middle column shows the lead's framing and topics, which only the detail carries.
  const lead = stories[0];
  if (lead) {
    const full = await getJSON(V1 + "/clusters/" + lead.id).catch(() => null);
    if (full) stories[0] = { ...adaptCluster(full, cx), category: lead.category };
  }
  // The featured explainer shows an excerpt and key points, which only the detail carries.
  if (exs[0]) {
    const full = await getJSON(V1 + "/explainers/" + exs[0].id).catch(() => null);
    if (full) exs[0] = adaptExplainerDetail(full, cx);
  }
  const tabs = categoryTabs(stories);
  if (isTab && tabs.categories.indexOf(category) < 0 && tabs.moreCategories.indexOf(category) < 0) tabs.moreCategories.push(category);
  return {
    stories, ...tabs,
    timelines: tl.list.map((t, i) => adaptTimeline(t, tl.details[i], cx)),
    explainers: exs,
  };
}

// Story page: { stories: [story, ...related] } (empty when the id is unknown).
export async function loadStoryMain(id) {
  if (USE_SAMPLE) return sample();
  // Start all three requests together; related stories are loaded separately (loadRelated)
  // so a slow related query never holds up the story itself.
  const [cx, s] = await Promise.all([
    ctx(),
    getJSON(V1 + "/clusters/" + encodeURIComponent(id)).catch((e) => { if (notFound(e)) return null; throw e; }),
  ]);
  if (!s) return { stories: [] };
  return { stories: [adaptCluster(s, cx)] };
}
export async function loadRelated(id) {
  if (USE_SAMPLE) return { stories: [] };
  const [cx, rel] = await Promise.all([
    ctx(),
    getJSON(V1 + "/clusters/" + encodeURIComponent(id) + "/related?limit=6").catch(() => ({ items: [] })),
  ]);
  return { stories: (rel.items || []).filter((x) => String(x.id) !== String(id)).map((x) => adaptCluster(x, cx)) };
}
export async function loadStory(id) {
  const [main, rel] = await Promise.all([loadStoryMain(id), loadRelated(id)]);
  return main.stories.length ? { stories: main.stories.concat(rel.stories) } : main;
}

// Publisher (/source/:key), topic (/topic/:key) and plain /list pages: { stories }.
export async function loadList(kind, key) {
  if (USE_SAMPLE) return sample();
  const cx = await ctx();
  if (kind === "source") {
    if (!cx.sources.list.length) await sourcesStrict();   // surface an outage instead of "not found"
    const src = cx.sources.list.filter((s) => slug(s.name) === key || s.slug === key)[0];
    if (!src) return { stories: [], source: null };
    const page = await feedPage({ source_id: src.id });
    return { stories: page.items.map((x) => adaptCluster(x, cx)), source: src };
  }
  if (kind === "topic") {
    const q = String(key || "").replace(/-/g, " ").trim();
    if (q.length < 2) return { stories: [] };
    const r = await getJSON(V1 + "/search?" + qs({ q, limit: 50 }));
    // Search results are slim (no topics), so tag each with the topic searched for:
    // the page finds its heading by looking the topic up in the stories' topics.
    const topic = { label: titleCase(q), kind: "topic" };
    return { stories: r.items.map((x) => { const s = adaptCluster(x, cx); s.topics = [topic].concat(s.topics); return s; }) };
  }
  return { stories: await feedStories(cx) };
}

// Watch: { stories } carrying video.
export async function loadWatch() {
  if (USE_SAMPLE) return sample();
  const cx = await ctx();
  const r = await getJSON(V1 + "/clusters/videos?" + qs({ kind: "watch", limit: 30 }));
  return { stories: r.items.map((x) => adaptCluster(x, cx)) };
}

// Sources directory: { sources: [{key,name,region,lang,homepage}], stories }.
export async function loadSourcesPage() {
  if (USE_SAMPLE) return sample();
  const cx = await ctx();
  const stories = await feedStories(cx).catch(() => []);
  if (!cx.sources.list.length) {
    // /sources failed or is empty: let the page fall back to outlets seen in the feed.
    if (!stories.length) await sourcesStrict();   // throws the real error if it failed
    return { stories };
  }
  const sources = cx.sources.list.filter((s) => !s.status || s.status === "active").map((s) => ({
    key: slug(s.name), name: s.name, region: titleCase(s.region || s.category || ""), lang: String(s.language || "").toUpperCase(), homepage: s.homepage_url || null,
  }));
  return { sources, stories };
}

// Timelines list: sample timelines[].
export async function loadTimelines() {
  if (USE_SAMPLE) return (await sample()).timelines;
  const cx = await ctx();
  const tl = await timelinesFull(cx);
  return tl.list.map((t, i) => adaptTimeline(t, tl.details[i], cx));
}

// Timelines no longer actively tracked ("Past stories"), newest-dropped first. They follow the
// tracked list. Never fails the page: a missing list just means no section.
export async function loadPastTimelines() {
  if (USE_SAMPLE) return [];
  try {
    const cx = await ctx();
    const r = await getJSON(V1 + "/timelines/archived");
    return (r.timelines || []).map((t) => adaptTimeline(t, null, cx));
  } catch (e) { return []; }
}

// One timeline: { timelines, stories } with the beats' stories.
export async function loadTimelinePage(id) {
  if (USE_SAMPLE) return sample();
  const cx = await ctx();
  const [tl, own] = await Promise.all([
    timelinesFull(cx).catch(() => ({ list: [], details: [] })),
    getJSON(V1 + "/timelines/" + encodeURIComponent(id)).catch((e) => { if (notFound(e)) return null; throw e; }),
  ]);
  const timelines = tl.list.map((t, i) => adaptTimeline(t, tl.details[i], cx));
  if (own && !timelines.some((t) => t.id === own.id)) timelines.unshift(adaptTimeline(null, own, cx)); // archived / direct link
  return { timelines, stories: own ? timelineStories(own, cx) : [] };
}

// Explainers list: sample explainers[].
export async function loadExplainers() {
  if (USE_SAMPLE) return (await sample()).explainers;
  return explainerList(await ctx());
}

// One explainer: { explainers, stories } with its source stories.
export async function loadExplainerPage(id) {
  if (USE_SAMPLE) return sample();
  const cx = await ctx();
  const [list, own] = await Promise.all([
    explainerList(cx).catch(() => []),
    getJSON(V1 + "/explainers/" + encodeURIComponent(id)).catch((e) => { if (notFound(e)) return null; throw e; }),
  ]);
  if (!own) return { explainers: list, stories: [] };
  const x = adaptExplainerDetail(own, cx);
  return { explainers: [x].concat(list.filter((e) => e.id !== x.id)), stories: explainerSourceStories(own, x.category) };
}

// Context tabs: { timelines, explainers }.
export async function loadContext() {
  if (USE_SAMPLE) { const d = await sample(); return { timelines: d.timelines, explainers: d.explainers }; }
  const [timelines, explainers] = await Promise.all([loadTimelines(), loadExplainers()]);
  return { timelines, explainers };
}

// Saved / history: stories for locally stored ids. There is no batch endpoint,
// so this looks each id up (newest `max` only, a few at a time); ids that no
// longer resolve are dropped.
export async function loadStoriesByIds(ids, { max = 30 } = {}) {
  if (USE_SAMPLE) { const d = await sample(); return d.stories.filter((s) => ids.indexOf(s.id) > -1); }
  const want = ids.slice(-max);
  if (!want.length) return [];
  const cx = await ctx();
  let failure = null;
  const got = await mapLimit(want, 4, (id) => getJSON(V1 + "/clusters/" + encodeURIComponent(id)).catch((e) => { if (!notFound(e)) failure = failure || e; return null; }));
  const found = got.filter(Boolean);
  // Only deleted stories (404) are dropped quietly; if nothing loaded because of an outage, say so.
  if (!found.length && failure) throw failure;
  return found.map((c) => adaptCluster(c, cx));
}

export async function loadAnnouncements() {
  if (USE_SAMPLE) return (await sample()).announcements || [];
  return adaptAnnouncements(await getJSON(V1 + "/announcements/active"));
}

// Story search for the search overlay; null in sample mode (it filters locally).
export async function searchStories(q) {
  if (USE_SAMPLE || !q || q.trim().length < 2) return null;
  const cx = await ctx();
  const r = await getJSON(V1 + "/search?" + qs({ q: q.trim(), limit: 20 }));
  return r.items.map((x) => adaptCluster(x, cx));
}

// Word, quote and poll of the day (no page renders these yet).
export async function loadDaily() {
  if (USE_SAMPLE) return (await sample()).daily;
  const [w, q, p] = await Promise.all([
    getJSON(V1 + "/word-of-the-day").catch(() => null),
    getJSON(V1 + "/quote-of-the-day").catch(() => null),
    getJSON(V1 + "/polls/daily").catch(() => null),
  ]);
  return adaptDaily(w, q, p);
}

// A fresh playable URL for a story video that had none at scrape time (Brightcove manifests
// are short-lived, so the backend resolves them on demand). null when unavailable.
export async function resolveVideoUrl(articleId) {
  const r = await getJSON(V1 + "/articles/" + encodeURIComponent(articleId) + "/video-url", { ttl: 30 * 1000 });
  return (r && r.video_url) || null;
}
