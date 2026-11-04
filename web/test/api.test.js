import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../src/api.js";
import * as F from "./fixtures.js";

const SAMPLE = JSON.parse(readFileSync(new URL("../../prototype/data/sample.json", import.meta.url), "utf8"));
const IDX = api.indexSources(F.sources);
const CX = { sources: IDX, now: F.NOW };

// Every key a page reads from a sample object must exist on the adapted object.
function expectSampleKeys(sampleObj, adapted) {
  Object.keys(sampleObj).forEach((k) => expect(adapted, "missing key " + k).toHaveProperty(k));
}

beforeEach(() => {
  api.clearCache();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(F.NOW);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("helpers", () => {
  it("splits the stored bullet summary", () => {
    expect(api.splitBullets("\n• One.\n• Two.")).toEqual(["One.", "Two."]);
    expect(api.splitBullets("Line a\nLine b")).toEqual(["Line a", "Line b"]);
    expect(api.splitBullets(null)).toEqual([]);
  });
  it("derives minutes from timestamps and never goes negative", () => {
    expect(api.minutesAgo(new Date(F.NOW - 90 * 60000).toISOString(), F.NOW)).toBe(90);
    expect(api.minutesAgo(new Date(F.NOW + 60000).toISOString(), F.NOW)).toBe(0);
    expect(api.minutesAgo(null, F.NOW)).toBe(0);
    expect(api.minutesAgo("garbage", F.NOW)).toBe(0);
  });
  it("title-cases categories", () => {
    expect(api.titleCase("northeast")).toBe("Northeast");
    expect(api.titleCase("world_news")).toBe("World News");
  });
});

describe("adaptCluster", () => {
  it("maps a slim list cluster to the sample story shape", () => {
    const s = api.adaptCluster(F.feedPage.items[1], CX);
    expectSampleKeys(SAMPLE.stories[0], s);
    expect(s.id).toBe(2);
    expect(s.category).toBe("National");            // 2 of 4 articles from a national source
    expect(s.outlet_count).toBe(3);                 // distinct sources, not article_count (4)
    expect(s.minutes_ago).toBe(90);
    expect(s.summary).toEqual(["First bullet for 2.", "Second bullet."]);
    expect(s.dek).toBe("Snippet for article 21.");  // HTML stripped
    expect(s.framing).toEqual([]);                  // list items carry no framing
    expect(s.topics).toEqual([]);
    expect(s.coverage[0]).toEqual({ outlet: "The Hindu", title: "Article 21 from The Hindu", minutes_ago: 100, url: "https://example.com/a/21" });
    expect(s.media).toEqual([{ kind: "image", label: "image", src: "https://img/22.jpg" }]);
  });
  it("maps detail-only fields: framing, topics + entities (no backdrop), media", () => {
    const c = F.detailCluster(2, {
      arts: [F.article(21, 1, 100, { image_urls: ["https://img/a.jpg", "https://img/b.jpg"], image_url: "https://img/a.jpg", video_url: "https://v/1.m3u8", video_duration_seconds: 75 })],
      detail: { entities: { persons: ["Narendra Modi"], organizations: ["RBI"], locations: ["Mumbai"], backdrop: ["Mumbai"] } },
    });
    // _entities_for_response already strips backdrop names server-side; the adapter must never list the backdrop key itself.
    const s = api.adaptCluster(c, CX);
    expect(s.framing).toEqual([
      { outlet: "The Hindu", angle: "Leads with the rate decision", tag: "" },
      { outlet: "Mint", angle: "Leads with market reaction", tag: "" },
    ]);
    expect(s.topics.map((t) => t.label + "/" + t.kind)).toEqual(["Monetary policy/topic", "Inflation/topic", "Narendra Modi/person", "RBI/organisation", "Mumbai/place"]);
    expect(s.media.map((m) => m.kind)).toEqual(["video", "image", "image"]);
    expect(s.video).toEqual({ outlet: "The Hindu", duration: 75, article_id: 21, src: "https://v/1.m3u8", poster: "https://img/a.jpg" });
    expect(s.media[0]).toMatchObject({ kind: "video", poster: "https://img/a.jpg", article_id: 21 });
    expect(s.framing_pending).toBe(false);
  });
  it("degrades on a cluster with no articles, summary or category", () => {
    const s = api.adaptCluster({ id: 9, headline: "", summary: null, article_count: 0, first_seen_at: null, last_updated_at: null, articles: [] });
    expect(s).toMatchObject({ id: 9, headline: "Untitled story", category: "News", outlet_count: 0, minutes_ago: 0, dek: "", summary: [], coverage: [], media: [], topics: [], framing: [] });
  });
  it("lets a category tab force the category", () => {
    expect(api.adaptCluster(F.feedPage.items[1], { ...CX, category: "Business" }).category).toBe("Business");
  });
});

describe("front page ordering (left = latest, middle = most covered, right = top)", () => {
  const stories = F.feedPage.items.map((c) => api.adaptCluster(c, CX));
  it("puts the most-covered story first and the rest newest first", () => {
    const S = api.orderFrontPage(stories, "All");
    expect(S[0].id).toBe(2);                                    // 3 outlets
    expect(S.slice(1).map((s) => s.minutes_ago)).toEqual([5, 15, 30, 60]);
  });
  it("picks the lead within the active category", () => {
    const S = api.orderFrontPage(stories, "Northeast");
    expect(S[0].id).toBe(5);
    expect(S.filter((s) => s.category === "Northeast")[0].id).toBe(5);
  });
  it("builds tabs: All plus the biggest categories, rest under More", () => {
    const t = api.categoryTabs(stories, 2);
    expect(t.categories[0]).toBe("All");
    expect(t.categories.length).toBe(3);
    expect(t.categories.concat(t.moreCategories).sort()).toEqual(["All", "Business", "National", "Northeast", "Sports"]);
  });
});

describe("timelines", () => {
  it("maps a list item + detail to the sample timeline shape", () => {
    const t = api.adaptTimeline(F.timelinesList.timelines[0], F.timelineDetail7, CX);
    expectSampleKeys(SAMPLE.timelines[0], t);
    expect(t.updated).toBe("Updated 2h ago");
    expect(t.dek).toBe("The bill was tabled in March.");
    expect(t.category).toBe("National");
    expect(t.points).toEqual([{ date: "12 Mar", text: "Draft tabled" }, { date: "3 May", text: "Bill passes" }]);
    expect(t.beats[1]).toEqual({ date: "3 May", label: "Bill passes", body: "It passed.", stories: [5, 2] });
    expect(t.audio).toEqual({ label: "Listen to this timeline", duration: 204, src: "https://cdn/tl7.mp3" });
  });
  it("degrades without a detail (no beats, no audio)", () => {
    const t = api.adaptTimeline(F.timelinesList.timelines[1], null, CX);
    expect(t).toMatchObject({ id: 8, points: [], beats: [], audio: null, updated: "", category: "Timeline", image: "https://img/tl8.jpg" });
  });
  it("hydrates beat stories once each", () => {
    expect(api.timelineStories(F.timelineDetail7, CX).map((s) => s.id)).toEqual([2, 5]);
  });
});

describe("explainers", () => {
  it("maps a list item", () => {
    const x = api.adaptExplainerItem(F.explainersList.explainers[0], CX);
    expect(x).toMatchObject({ id: 3, title: "What changed in the bill?", category: "National", dek: "Three things changed.", read: "Updated 4h ago", points: [], sections: [] });
  });
  it("maps a detail to the sample explainer shape", () => {
    const x = api.adaptExplainerDetail(F.explainerDetail3, CX);
    expectSampleKeys(SAMPLE.explainers[0], x);
    expect(x.quick).toBe("Three things changed: fines, licences and timing.");
    expect(x.sections[0]).toEqual({ heading: "What is happening", body: ["Parliament passed it. It takes effect in May.", "A second paragraph."] });
    expect(x.excerpt).toBe("Parliament passed it. It takes effect in May.");
    expect(x.points).toEqual(["Parliament passed it.", "Fines double."]);
    expect(x.sources).toEqual([2, 5]);
    expect(x.read).toBe("1 min read");
    expect(x.audio).toEqual({ label: "Listen to this explainer", duration: 150, src: "https://cdn/x3.mp3" });
  });
  it("turns sources into minimal stories the attribution line can read", () => {
    const st = api.explainerSourceStories(F.explainerDetail3, "National");
    expect(st[0]).toMatchObject({ id: 2, headline: "Bill passes", outlet_count: 4 });
    expect(st[0].coverage.map((c) => c.outlet)).toEqual(["The Hindu", "Mint"]);
    expect(st[1].coverage.map((c) => c.outlet)).toEqual(["Mint"]);   // empty outlet_names falls back to `outlet`
  });
});

describe("daily + announcements", () => {
  it("maps word/quote/poll to the sample daily shape and tolerates gaps", () => {
    const d = api.adaptDaily(F.word, F.quote, F.poll);
    expectSampleKeys(SAMPLE.daily, d);
    expect(d.word).toMatchObject({ w: "Lucid", m: "Clear." });
    expect(d.quote).toBe("“Be brief.” — Someone");
    expect(d.poll).toMatchObject({ q: "Should it pass?", a: ["Yes", "No"] });
    expect(api.adaptDaily(null, null, null)).toEqual({ word: null, quote: null, poll: null });
  });
  it("passes announcements through with a string body", () => {
    const a = api.adaptAnnouncements(F.announcements);
    expectSampleKeys(SAMPLE.announcements[0], a[0]);
    expect(a[0].body).toBe("");
  });
});

describe("client", () => {
  it("dedupes identical requests and caches the result", async () => {
    const f = F.fakeFetch({ "/x": { a: 1 } });
    vi.stubGlobal("fetch", f);
    const [a, b] = await Promise.all([api.getJSON("/x"), api.getJSON("/x")]);
    await api.getJSON("/x");
    expect(a).toEqual({ a: 1 }); expect(b).toEqual({ a: 1 });
    expect(f.calls).toEqual(["/x"]);
  });
  it("surfaces HTTP errors with status and detail, and does not cache them", async () => {
    const f = F.fakeFetch({ "/y": 503 });
    vi.stubGlobal("fetch", f);
    await expect(api.getJSON("/y")).rejects.toMatchObject({ name: "ApiError", status: 503, message: "status 503", path: "/y" });
    await expect(api.getJSON("/y")).rejects.toBeInstanceOf(api.ApiError);
    expect(f.calls.length).toBe(2);
  });
  it("reports network failures as status 0", async () => {
    vi.stubGlobal("fetch", F.fakeFetch({ "/z": new TypeError("Failed to fetch") }));
    await expect(api.getJSON("/z")).rejects.toMatchObject({ status: 0 });
  });
});

describe("page loaders against the real endpoints (fake fetch)", () => {
  const routes = {
    "/api/v1/sources": F.sources,
    "/api/v1/clusters?limit=50": F.feedPage,
    "/api/v1/clusters?limit=50&category=northeast": { items: [F.listCluster(60, { updated: 2, arts: [F.article(61, 1, 3)] })], next_cursor: null, has_more: false },
    "/api/v1/clusters/2": F.detailCluster(2, { updated: 90, arts: F.feedPage.items[1].articles }),
    "/api/v1/clusters/2/related?limit=6": { items: [F.detailCluster(5, { arts: F.feedPage.items[4].articles })], actor: "RBI" },
    "/api/v1/timelines": F.timelinesList,
    "/api/v1/timelines/7": F.timelineDetail7,
    "/api/v1/timelines/8": 500,
    "/api/v1/timelines/99": F.timelineDetail99Archived,
    "/api/v1/explainers": F.explainersList,
    "/api/v1/explainers/3": F.explainerDetail3,
    "/api/v1/clusters?limit=50&source_id=4": { items: [F.feedPage.items[4]], next_cursor: null, has_more: false },
    "/api/v1/search?q=monetary%20policy&limit=50": { items: [F.feedPage.items[0]], next_cursor: null, has_more: false },
    "/api/v1/clusters/videos?kind=watch&limit=30": { items: [F.listCluster(70, { arts: [F.article(71, 3, 4, { video_url: "https://v/71", video_duration_seconds: 42 })] })], next_cursor: null, has_more: false },
    "/api/v1/announcements/active": F.announcements,
  };
  let f;
  beforeEach(() => { f = F.fakeFetch(routes); vi.stubGlobal("fetch", f); });

  it("loadHome builds the home payload", async () => {
    const d = await api.loadHome({ category: "All" });
    expect(d.stories[0].id).toBe(2);
    expect(d.stories[0].framing.length).toBe(2);            // detail merged into the lead
    expect(d.stories[0].category).toBe("National");
    expect(d.stories.slice(1).map((s) => s.id)).toEqual([3, 4, 1, 5]);
    expect(d.categories[0]).toBe("All");
    expect(d.timelines.map((t) => t.points.length)).toEqual([2, 0]);   // timeline 8's detail failed: degrades
    expect(d.explainers[0].excerpt).toBeTruthy();            // featured explainer hydrated
    expect(d.explainers[1].excerpt).toBe("");
  });
  it("loadHome for a category tab merges that tab's own feed", async () => {
    const d = await api.loadHome({ category: "Northeast" });
    const ne = d.stories.filter((s) => s.category === "Northeast");
    expect(ne.map((s) => s.id)).toContain(60);
    expect(d.stories[0].category).toBe("Northeast");
  });
  it("loadHome still renders when everything but the feed fails", async () => {
    vi.stubGlobal("fetch", F.fakeFetch({ "/api/v1/clusters?limit=50": F.feedPage }));
    const d = await api.loadHome({});
    expect(d.stories.length).toBe(5);
    expect(d.stories[0].category).toBe("News");   // no /sources -> no categories
    expect(d.timelines).toEqual([]); expect(d.explainers).toEqual([]);
  });
  it("loadHome rejects when the feed itself fails", async () => {
    vi.stubGlobal("fetch", F.fakeFetch({}));
    await expect(api.loadHome({})).rejects.toMatchObject({ status: 404 });
  });
  it("adaptMedia keeps a late video even when earlier articles already have 8+ images", () => {
    const arts = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, image_url: "https://img/" + i + ".jpg" }));
    arts.push({ id: 99, image_url: "https://img/v.jpg", has_pending_video: true });
    const m = api.adaptMedia(arts);
    expect(m).toHaveLength(8);
    expect(m[0]).toMatchObject({ kind: "video", article_id: 99, src: null, poster: "https://img/v.jpg" });
  });
  it("loadPastTimelines lists timelines no longer tracked, and is empty rather than failing", async () => {
    vi.stubGlobal("fetch", F.fakeFetch({ "/api/v1/sources": [], "/api/v1/timelines/archived": { timelines: [{ id: 108, title: "Texas", context: "A Senate seat. More.", story_count: 24, narrative_generated_at: "2026-11-03T00:34:33Z", dropped_from_top_at: "2026-11-04T00:39:29Z", anchor_cluster: null }] } }));
    const past = await api.loadPastTimelines();
    expect(past).toHaveLength(1);
    expect(past[0]).toMatchObject({ id: 108, title: "Texas", archived: true, story_count: 24 });
    api.clearCache();
    vi.stubGlobal("fetch", F.fakeFetch({ "/api/v1/sources": [], "/api/v1/timelines/archived": new TypeError("offline") }));
    expect(await api.loadPastTimelines()).toEqual([]);
  });
  it("resolveVideoUrl returns the fresh URL, or null when none is available", async () => {
    vi.stubGlobal("fetch", F.fakeFetch({ "/api/v1/articles/7/video-url": { video_url: "https://m/x.m3u8" }, "/api/v1/articles/8/video-url": { video_url: null } }));
    expect(await api.resolveVideoUrl(7)).toBe("https://m/x.m3u8");
    expect(await api.resolveVideoUrl(8)).toBeNull();
  });
  it("loadStory returns the story then related, and [] for an unknown id", async () => {
    const d = await api.loadStory(2);
    expect(d.stories.map((s) => s.id)).toEqual([2, 5]);
    expect((await api.loadStory(12345)).stories).toEqual([]);
  });
  it("loadList resolves a publisher by name slug", async () => {
    const d = await api.loadList("source", "nagaland-post");
    expect(d.stories.map((s) => s.id)).toEqual([5]);
    expect(d.stories[0].coverage.some((c) => c.outlet === "Nagaland Post")).toBe(true);
    expect((await api.loadList("source", "nobody")).stories).toEqual([]);
  });
  it("loadList tags topic search results with the topic", async () => {
    const d = await api.loadList("topic", "monetary-policy");
    expect(d.stories[0].topics[0]).toEqual({ label: "Monetary Policy", kind: "topic" });
  });
  it("loadWatch carries the video's outlet and length", async () => {
    const d = await api.loadWatch();
    expect(d.stories[0].video).toMatchObject({ outlet: "Sportstar", duration: 42 });
    expect(d.stories[0].media[0].kind).toBe("video");
  });
  it("loadSourcesPage lists active publishers with real region/language", async () => {
    const d = await api.loadSourcesPage();
    expect(d.sources.map((s) => s.key)).toEqual(["the-hindu", "mint", "sportstar", "nagaland-post"]);
    expect(d.sources[3]).toMatchObject({ region: "Northeast", lang: "EN" });
  });
  it("loadTimelinePage includes an archived timeline reached by direct link", async () => {
    const d = await api.loadTimelinePage(99);
    expect(d.timelines[0].id).toBe(99);
    expect(d.timelines[0].archived).toBe(true);
    const d7 = await api.loadTimelinePage(7);
    expect(d7.stories.map((s) => s.id)).toEqual([2, 5]);
  });
  it("loadExplainerPage puts the detail first with its source stories", async () => {
    const d = await api.loadExplainerPage(3);
    expect(d.explainers[0].sections.length).toBe(2);
    expect(d.explainers.map((x) => x.id)).toEqual([3, 4]);
    expect(d.stories.map((s) => s.id)).toEqual([2, 5]);
    expect((await api.loadExplainerPage(404)).stories).toEqual([]);
  });
  it("loadStoriesByIds drops ids that no longer resolve", async () => {
    const s = await api.loadStoriesByIds([2, 999]);
    expect(s.map((x) => x.id)).toEqual([2]);
    expect(await api.loadStoriesByIds([])).toEqual([]);
  });
  it("reports outages instead of empty results", async () => {
    vi.stubGlobal("fetch", F.fakeFetch({ "/api/v1/sources": 502, "/api/v1/clusters/2": 502 }));
    await expect(api.loadList("source", "mint")).rejects.toMatchObject({ status: 502 });
    await expect(api.loadSourcesPage()).rejects.toMatchObject({ status: 502 });
    await expect(api.loadStoriesByIds([2])).rejects.toMatchObject({ status: 502 });
    await expect(api.loadStoriesByIds([999])).resolves.toEqual([]);   // 404: story gone, not an outage
  });
  it("loadAnnouncements and searchStories", async () => {
    expect((await api.loadAnnouncements())[0].id).toBe(1);
    expect(await api.searchStories("a")).toBeNull();   // under the API's 2-char minimum
  });
});

describe("VITE_USE_SAMPLE=1 keeps the sample on the same loader path", () => {
  it("serves sample.json through the loaders", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_USE_SAMPLE", "1");
    const m = await import("../src/api.js");
    const f = F.fakeFetch({ "/api/sample": SAMPLE });
    vi.stubGlobal("fetch", f);
    expect(m.USE_SAMPLE).toBe(true);
    expect((await m.loadHome({})).stories.length).toBe(SAMPLE.stories.length);
    expect(await m.loadTimelines()).toEqual(SAMPLE.timelines);
    expect(await m.loadContext()).toEqual({ timelines: SAMPLE.timelines, explainers: SAMPLE.explainers });
    expect(await m.searchStories("anything")).toBeNull();
    const id = SAMPLE.stories[0].id;
    expect((await m.loadStoriesByIds([id])).map((s) => s.id)).toEqual([id]);
    expect(f.calls.every((p) => p === "/api/sample")).toBe(true);
    vi.unstubAllEnvs();
  });
});
