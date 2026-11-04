// Fixtures shaped exactly like the Pydantic response models in app/schemas.py
// (field names, optionality, nesting). Built by hand because the production API
// only answers whitelisted client IPs. NOW is the fixed clock the tests use.

export const NOW = Date.parse("2026-11-04T12:00:00Z");
const ago = (min) => new Date(NOW - min * 60000).toISOString();

// SourceOut[]  (GET /api/v1/sources)
export const sources = [
  { id: 1, name: "The Hindu", slug: "the-hindu", feed_url: "https://x/1.xml", homepage_url: "https://thehindu.com", language: "en", category: "national", region: "national", status: "active" },
  { id: 2, name: "Mint", slug: "mint", feed_url: "https://x/2.xml", homepage_url: null, language: "en", category: "business", region: "national", status: "active" },
  { id: 3, name: "Sportstar", slug: "sportstar", feed_url: "https://x/3.xml", homepage_url: null, language: "en", category: "sports", region: "national", status: "active" },
  { id: 4, name: "Nagaland Post", slug: "nagaland-post", feed_url: "https://x/4.xml", homepage_url: null, language: "en", category: "northeast", region: "northeast", status: "active" },
  { id: 5, name: "Dead Feed", slug: "dead-feed", feed_url: "https://x/5.xml", homepage_url: null, language: "hi", category: "national", region: "national", status: "disabled" },
];

// ArticleListOut
export function article(id, source_id, min, extra = {}) {
  const src = sources.filter((s) => s.id === source_id)[0];
  return {
    id, source_id, source_name: src ? src.name : "Unknown", url: "https://example.com/a/" + id,
    title: "Article " + id + " from " + (src ? src.name : "?"), snippet: "<p>Snippet for <b>article</b> " + id + ".</p>",
    content: null, published_at: ago(min), image_url: null, video_url: null, video_is_short: null,
    video_duration_seconds: null, has_pending_video: false, ...extra,
  };
}

// StoryClusterListOut
export function listCluster(id, { updated = 10, first = 120, arts, summary } = {}) {
  return {
    id, headline: "Cluster " + id + " headline", summary: summary === undefined ? "\n• First bullet for " + id + ".\n• Second bullet." : summary,
    article_count: (arts || []).length, first_seen_at: ago(first), last_updated_at: ago(updated), articles: arts || [],
  };
}

// StoryClusterOut (detail): list fields + entities/topics/framing_comparison/ai_enriched.
export function detailCluster(id, opts = {}) {
  return {
    ...listCluster(id, opts),
    entities: { persons: ["Narendra Modi"], organizations: ["RBI", "Mumbai"], locations: ["Mumbai"], backdrop: [] },
    topics: ["Monetary policy", "Inflation"],
    framing_comparison: [
      { outlet: "The Hindu", headline_angle: "Leads with the rate decision" },
      { outlet: "Mint", headline_angle: "Leads with market reaction" },
    ],
    ai_enriched: true,
    ...(opts.detail || {}),
  };
}

// GET /clusters page: 3 national-ish, 1 business, 1 sports; cluster 2 is the most covered.
export const feedPage = {
  items: [
    listCluster(1, { updated: 30, arts: [article(11, 1, 40), article(12, 2, 35)] }),
    listCluster(2, { updated: 90, arts: [article(21, 1, 100), article(22, 2, 95, { image_url: "https://img/22.jpg" }), article(23, 4, 92), article(24, 1, 91)] }),
    listCluster(3, { updated: 5, arts: [article(31, 3, 6)] }),
    listCluster(4, { updated: 15, arts: [article(41, 2, 20), article(42, 2, 18)] }),
    listCluster(5, { updated: 60, arts: [article(51, 4, 61), article(52, 1, 62), article(53, 4, 63)] }),
  ],
  next_cursor: "1.2:5", has_more: true,
};

// TimelineFeatureListItemOut / TimelineFeatureDetailOut
export const timelinesList = {
  timelines: [
    { id: 7, title: "Transport bill", context: "The bill was tabled in March. It passed in May.", story_count: 3, is_editorial_pick: true,
      narrative_generated_at: ago(120), anchor_cluster: listCluster(2, { arts: [article(21, 1, 100)] }), dropped_from_top_at: null, has_audio: true, image_url: null, view_count: 4 },
    { id: 8, title: "Monsoon", context: "Rains arrive.", story_count: 2, is_editorial_pick: false,
      narrative_generated_at: null, anchor_cluster: null, dropped_from_top_at: null, has_audio: false, image_url: "https://img/tl8.jpg", view_count: 0 },
  ],
};
export const timelineDetail7 = {
  id: 7, title: "Transport bill", context: "The bill was tabled in March. It passed in May.", is_editorial_pick: true,
  narrative_generated_at: ago(120), dropped_from_top_at: null,
  beats: [
    { date_label: "12 Mar", label: "Draft tabled", narration: "The draft was tabled.", clusters: [listCluster(2, { arts: [article(21, 1, 100)] })] },
    { date_label: "3 May", label: "Bill passes", narration: "It passed.", clusters: [listCluster(5, { arts: [article(51, 4, 61)] }), listCluster(2, { arts: [article(21, 1, 100)] })] },
  ],
  audio_url: "https://cdn/tl7.mp3", audio_duration_seconds: 204, audio_beat_offsets: [0, 100], image_url: null, view_count: 4,
};
export const timelineDetail99Archived = {
  id: 99, title: "Old story", context: "Long ago.", is_editorial_pick: false, narrative_generated_at: ago(60 * 24 * 3),
  dropped_from_top_at: ago(60 * 24), beats: [], audio_url: null, audio_duration_seconds: null, audio_beat_offsets: null, image_url: null, view_count: 0,
};

// ExplainerListItemOut / ExplainerDetailOut
export const explainersList = {
  explainers: [
    { id: 3, question: "What changed in the bill?", category: "national", teaser: "Three things changed.", updated_at: ago(240), has_audio: true, image_url: null },
    { id: 4, question: "Why are rates up?", category: "business", teaser: "Inflation.", updated_at: ago(30), has_audio: false, image_url: "https://img/x4.jpg" },
  ],
};
export const explainerDetail3 = {
  id: 3, question: "What changed in the bill?", category: "national", quick_answer: "Three things changed: fines, licences and timing.",
  sections: [
    { heading: "What is happening", body: "Parliament passed it. It takes effect in May.\n\nA second paragraph." },
    { heading: "Why it matters", body: "Fines double. Drivers notice." },
  ],
  sources: [
    { title: "Bill passes", outlet: "The Hindu", url: "https://thehindu.com/x", cluster_id: 2, source_count: 4, outlet_names: ["The Hindu", "Mint"], outlet_urls: ["https://thehindu.com/x", "https://mint.com/x"] },
    { title: "Fines explained", outlet: "Mint", url: null, cluster_id: 5, source_count: 1, outlet_names: [], outlet_urls: [] },
  ],
  hero_image_url: "https://img/x3.jpg", audio_url: "https://cdn/x3.mp3", audio_duration_seconds: 150, updated_at: ago(240),
};

export const announcements = { items: [{ id: 1, kind: "event", title: "Live Q&A", body: null, cta_label: "Join", action_type: "link", action_value: "https://x", priority: 30 }] };
export const word = { date: "2026-11-04", word: "Lucid", pronunciation: "LOO-sid", part_of_speech: "adjective", definition: "Clear.", example: "A lucid account.", origin: "Latin" };
export const quote = { date: "2026-11-04", quote: "Be brief.", author: "Someone", background_image: null };
export const poll = { id: 9, date: "2026-11-04", question: "Should it pass?", context: "", source_cluster_id: 2, source_headline: "x", closes_at: ago(-600),
  options: [{ id: 1, text: "Yes", votes: null, percentage: null }, { id: 2, text: "No", votes: null, percentage: null }], selected_option_id: null, total_votes: null };

// Minimal fake fetch: routes path -> body (or a status code), records calls.
export function fakeFetch(routes) {
  const calls = [];
  const f = async (path) => {
    calls.push(path);
    const key = Object.keys(routes).filter((k) => k === path)[0];
    const v = key === undefined ? 404 : routes[key];
    if (typeof v === "number") return { ok: false, status: v, statusText: "err", json: async () => ({ detail: "status " + v }) };
    if (v instanceof Error) throw v;
    return { ok: true, status: 200, statusText: "OK", json: async () => JSON.parse(JSON.stringify(v)) };
  };
  f.calls = calls;
  return f;
}
