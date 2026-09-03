# Multi-source feed — implementation plan (drafted 2026-09-03)

Status: **draft for review**. Nothing here is implemented yet.

The decision: the app stops showing single-source clusters. A story earns a
place in the feed by being covered by 2+ distinct outlets. Single-source RSS
items keep being ingested — they are the raw material clustering works on, and
any of them may earn its way in later — but they are never shown and never
enriched.

Everything below is measured on production unless it says "estimate".

---

## 1. Why

The app's premise is comparative coverage. It was not shipping that:

| Fact (7 days to 2026-09-03) | Value |
|---|---|
| Clusters created | 29,227 |
| **1 article, 1 source** | **28,483 (97.5%)** |
| 2+ articles, 1 source | 169 (0.6%) |
| 2+ sources | 575 (2.0%) |

97.5% of the feed is a single RSS item. For each one the backend pays an LLM to
rewrite the headline and summarise a **250-character snippet** — while a ~2,985
character scraped article sits unused in the same row (82% of articles have
>400 chars of `content`). That is ~$238/month to paraphrase text already on
screen.

Read behaviour points the same way, though at 110 reads/week it is directional
only, not a forecast:

| | clusters | ever read | rate |
|---|---|---|---|
| Multi-source | 575 | 60 | **10.4%** |
| Single-source | 28,666 | 50 | **0.17%** |

Multi-source clusters are ~60x more likely to be read.

---

## 2. Is there enough volume?

Yes. From the eval replay of the shipped config over a 3-day fixture
(14,603 articles): **1,046 multi-source clusters ≈ 350/day at 2+ sources**,
~115/day at 3+.

Note this is higher than production's own row counts (~420 over the same
window) for a boring reason: the clustering fix (`c63a76e`) deployed
2026-09-02 17:01 UTC, but the fixture spans 08-31 12:46 → 09-03 15:43, so ~69%
of it ran the old algorithm. The replay applies the current config to all three
days. **350/day is the steady-state number; do not use the production row
counts, they are contaminated by pre-fix days.**

For scale: NYT publishes roughly 150-250 pieces/day.

---

## 3. What it costs: latency

A multi-source feed is structurally behind the news, because corroboration
takes time. Median minutes from first article to second distinct outlet:

| Final source count | Clusters | Median lag |
|---|---|---|
| 2 | 322 | **238 min** |
| 3 | 66 | 82 min |
| 4 | 19 | 140 min (n small) |
| 5+ | 23 | **21 min** |

Two things follow.

Big stories corroborate fast — 21 minutes, which **is the 20-minute poll
cycle**. For well-covered stories the constraint is already polling frequency,
not the news cycle.

The 238-minute median at 2+ is dominated by marginal stories that barely reach
two outlets. Adding overlapping sources moves stories up this table, so lag
improves as coverage grows.

**This makes the app a "how outlets covered it" product, not a "what just
happened" product.** That is a positioning decision, not a defect — but the
`breaking` notification path is built on the latter and needs revisiting
(see §5.G).

---

## 4. Clustering is now the content pipeline

Measured 2026-09-03 on a fresh fixture, 1,000 newly-labelled pairs:

| Config | Precision | Recall | F1 | Singletons |
|---|---|---|---|---|
| Pre-rework | 1.000 | 0.036 | 0.070 | 98.4% |
| **Shipped** | **0.955** | **0.542** | **0.691** | **91.7%** |

Reproduces the rework's 0.944/0.528 on data it was never tuned against, so
recall ~0.54 is a real property, not overfitting.

**Recall 0.542 is the headline risk.** ~46% of genuinely-related pairs are not
merged. Today those stories are invisible-among-singletons; under this plan
they are *invisible*. Real multi-source volume is likely 600-700/day, not 350 —
the gap is recall.

This changes the answer to the question `clustering-rework-handoff.md` §2
parked ("recall headroom — recommend waiting — see whether 10.4% is actually
the constraint"). **It is now the constraint**, because it sets how much
content the app has at all. Phase 2 (embeddings) moves from optional polish to
the content pipeline.

**Precision 0.955 is less alarming than it looks.** `inspect --shipped` on the
four largest clusters found all four genuinely correct — Disha Salian CBI probe
(29 articles / 15 sources), Apple CEO succession (20/13), IndiGo emergency
landing (18/13), Mamata Banerjee's niece (18/13). No templated mega-merges of
the kind the poller comments warn about (Yahoo Finance earnings, job listings);
the same-source guard is holding. The 4.5% is near-miss noise on marginal
pairs.

**Therefore gate at 2+, not 3+.** An earlier draft of this plan argued for 3+
on precision grounds; the inspect run does not support that. Keep the threshold
in config so it can be tightened if false merges appear at scale.

---

## 5. Changes

### A. Feed gate

New setting `FEED_MIN_DISTINCT_SOURCES: int = 2` (config, not hardcoded).
Every cluster-listing query filters `distinct_source_count >= threshold`:
the default feed, topic/category feeds, search, related stories.

Detail-by-id is deliberately NOT gated — an existing deep link or notification
should still open.

### B. `became_multi_source_at`

New nullable column on `story_clusters`, set in `poller.py` at the point
`distinct_source_count` crosses the threshold (same place as the current
re-enrichment gate).

Feed ordering uses `COALESCE(became_multi_source_at, first_seen_at)`.
Without this, a cluster first seen at 09:00 that qualifies at 12:00 enters the
feed already three hours deep — pre-buried, and invisible to the user it is
new to. Migration backfills existing multi-source rows from `first_seen_at`.

### C. Enrichment gated to multi-source

`scripts/enrich_all_clusters.py` selection adds
`distinct_source_count >= FEED_MIN_DISTINCT_SOURCES`. Demand falls from
~4,800/day to ~350/day. Singletons keep their original RSS headline and are
never sent to an LLM.

### D. Event-driven enrichment

The poller already detects the crossing (B). Enqueue enrichment there rather
than waiting for the timer. The timer becomes a safety net and can drop to
40-60 min.

Rationale: with timer-only enrichment, a 40m poll plus a 40m enrich tick means
up to **80 minutes** between a story becoming corroborated and being
presentable — on a feed whose whole value is corroborated stories.

### E. Delete the doubling gate

`should_reenrich_on_new_outlet()` (thresholds 2, 4, 8, 16) was a cost fix for a
problem this plan removes. At ~350 qualifying clusters/day, re-enriching on
**every** new outlet costs ~$0.70/day more and keeps the summary and framing
comparison correct as coverage grows. Framing that lists 3 outlets when the
story now has 6 is the staleness bug seen on 2026-09-03.

Delete the gate; restore per-outlet re-enrichment.

### F. Use the article content we already scrape

`enrichment.py` sends `(art.snippet or "")[:250]`. Send `content` (avg ~2,985
chars, present on 82% of articles), falling back to snippet.

Per-call cost rises (~700 → ~1,450 input tokens) but total falls sharply
because volume drops 93%. Estimate: **~$85-110/month vs ~$292 today**, with
summaries built from ~473 words instead of ~40.

### G. Notifications

`breaking` currently selects on `headline_score`, which is useless at this
scale (11 of 29,241 clusters score >= 1). It must also require multi-source, or
it will push exactly the single-source stories the feed hides.

### H. Android client

If the API filters, the client needs no query change. It does need:
- an empty/quiet-period state (feed is ~350/day, not ~4,800)
- refresh behaviour — see §6

---

## 6. Open questions

**Poll interval.** The plan proposed 20m -> 40m. Recommend **keeping 20m**:
well-covered stories already corroborate at the 21-minute poll floor, so
doubling the interval doubles the floor for exactly the stories the feed is
built on. Polling is RSS fetches with no LLM cost — the saving is negligible
and the product cost is not.

**Single-source exceptions for low-corroboration topics (health, lifestyle).**
Cannot be built as specified: `topics` is free-text LLM output with **36,038
distinct strings in 7 days** (27,943 lowercased), including `agriculture` /
`Agriculture` and `floods` / `Flood`. There is no taxonomy to filter on.
Also no topic is safe — the best corroboration rate anywhere is Technology at
20.5%, then Judiciary 18.2%, Politics 12.4%.

If topic gating is wanted, it needs either a constrained enum in the enrichment
prompt or `Source`/`article.categories` (feed-assigned, controlled) as the
basis. Recommend **against** relaxing corroboration for health specifically —
single-source health claims are the highest-risk content to show uncorroborated.

Alternative worth considering: a clearly-labelled "Single report — not yet
corroborated" section. Keeps breadth without implying corroboration, and needs
no taxonomy.

**Client-side rotation on refresh.** Probably unnecessary. At ~350/day spread
15-24/hour through waking hours, most refreshes have genuinely new content, and
re-showing seen items reads as churn. Fix ordering (B) first and re-evaluate.

**Which sources to add.** Corroboration yield per article varies ~15x:
Yahoo Finance 3,386 articles -> 25 multi-source clusters (0.7%); Hindustan
Times 634 -> 66 (10.4%). Add Indian general-news outlets covering the same
events as HT/NDTV/India Today. Another finance wire inflates ingestion and
moves nothing.

---

## 7. Sequencing

1. **B** (`became_multi_source_at` + migration) — everything else orders on it.
2. **C + F** (gate enrichment, use full content) — cost falls immediately,
   independent of any client change.
3. **A** (feed gate, behind config, default off) — flip on when ready.
4. **D + E** (event-driven enrichment, drop the doubling gate).
5. **G** (notifications).
6. **H** (client empty state).

Recall work (Phase 2 embeddings) runs in parallel and is the long pole for
feed density.

---

## 8. Not verified

- The 288-config grid did not finish — it died on the droplet, almost
  certainly OOM (`multiprocessing.Pool` copies the 14,603-article fixture per
  worker; the box has 3.9GB with ~1.7GB in use). `SHIPPED` and `BASELINE`
  both printed and are the numbers used above. Re-run with fewer workers if a
  better config is wanted; the shipped config was selected by this same grid
  during the rework, so it is unlikely to be beaten by its own sweep.
- Cost figures in §5.F are estimates from measured per-call token counts
  (Haiku 707/219, Sonnet 1,143/369) times projected volume — not billing data.
- Read-rate figures are directional only at 110 reads/week.
