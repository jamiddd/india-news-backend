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
problem this plan removes. Framing that lists 3 outlets when the story now has
6 is the staleness bug seen on 2026-09-03.

Delete the gate; restore per-outlet re-enrichment.

Cost of doing so, corrected 2026-09-03 after measuring full-content tokens:
average 2.39 sources/cluster means ~1.39 enrichments per cluster (487 calls/day)
versus ~1.09 with the gate (382 calls/day). That is **~$1.50/day (~$45/month)**,
not the ~$0.70/day claimed in the first draft of this plan. Still worth paying
for correct framing, but it is a real trade, not a rounding error — revisit if
multi-source volume grows past ~700/day.

### F. Use the article content we already scrape

`enrichment.py` sends `(art.snippet or "")[:250]`. Send `content` instead
(avg ~2,985 chars, present on 82% of articles), falling back to snippet.

Two caps, and they are the knobs that bound input cost as volume grows:
`CONTENT_CAP = 1500` chars per article and `MAX_ARTICLES = 6` per cluster —
without the second, one 29-article cluster dominates a run.

Measured with the `count_tokens` endpoint (free) over 25 real multi-source
clusters, mean 3.16 articles each after the cap:

| | mean input tokens |
|---|---|
| snippet-only (today) | 1,361 |
| **full content, caps applied** | **2,527** (median 2,069, max 4,519) |

Summaries are then built from ~473 words instead of ~40.

### G. Batch API for enrichment  (decided 2026-09-03)

Enrichment is background work triggered by a timer or the poller, with no user
waiting on the response, so asynchronous processing costs nothing in product
terms. The Batch API runs the same requests at **50% of the price**.

This halves whatever the rest of the plan lands on and stacks with every other
lever, which is why it is part of the plan rather than a follow-up.

Turnaround is asynchronous — usually minutes, up to 24h. A cluster can
therefore sit corroborated-but-unenriched for a while, showing its original RSS
headline. Acceptable, but it interacts with §5.D: the *event-driven* path
should stay synchronous for the 1→2 crossing (that is the story entering the
feed), and batching applies to the re-enrichment passes at 3, 4, 5... outlets,
which are refinements nobody is waiting for.

### H. Notifications

`breaking` currently selects on `headline_score`, which is useless at this
scale (11 of 29,241 clusters score >= 1). It must also require multi-source, or
it will push exactly the single-source stories the feed hides.

### I. Android client

If the API filters, the client needs no query change. It does need:
- an empty/quiet-period state (feed is ~350/day, not ~4,800)
- refresh behaviour — see §6

### J. Cost summary

All figures measured 2026-09-03: input tokens via `count_tokens` on 25 real
clusters, output tokens (369, rounded to 450 for more outlets in the framing
list) from live calls, volume from the eval replay. Sonnet 5 at $3/$15 per
MTok, Haiku 4.5 at $1/$5.

Per Sonnet call with full content: 2,527 in + 450 out = **$0.0143**.
At 487 calls/day (350 clusters x 1.39 enrichments):

| Scenario | $/day | $/month |
|---|---|---|
| Today (every singleton, snippets only) | $9.70 | $292 |
| Plan, Sonnet, full content, per-outlet | $6.98 | $210 |
| Plan + keep the doubling gate | $5.47 | $164 |
| **Plan + Batch API  (chosen)** | **$3.49** | **$105** |
| Plan on Haiku instead of Sonnet | $2.33 | $70 |

**The scaling caveat is the important line here.** These assume ~350
multi-source clusters/day. This plan deliberately grows that by adding
overlapping sources, and lifting recall from 0.542 would roughly double it
again. At 700/day the cost doubles — **~$7/day even with batching**, i.e. back
to roughly today's spend while serving a far better product. Cost scales with
exactly the thing the plan is trying to increase, so `CONTENT_CAP`,
`MAX_ARTICLES` and the model choice should stay configurable.

An earlier draft of this section estimated $85-110/month. That was wrong — it
predated the token measurement and assumed 250 clusters/day with snippet-sized
inputs. The real saving versus today is ~64% with batching, not ~70% without.

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
2. **C + F** (gate enrichment to multi-source, use full content) — this is
   where the cost drop happens, and it needs no client change.
3. **G** (Batch API for the re-enrichment passes) — halves whatever 2 lands on.
4. **A** (feed gate, behind config, default off) — flip on when ready.
5. **D + E** (event-driven enrichment, drop the doubling gate).
6. **H** (notifications must require multi-source too, or they push exactly
   the stories the feed hides).
7. **I** (client empty state).

Steps 1-3 are backend-only and independently useful: they cut spend ~64% and
improve summary quality whether or not the feed gate ever ships. Step 4 is the
product commitment and the one to think hardest about.

Recall work (Phase 2 embeddings) runs in parallel and is the long pole for feed
density.

---

## 8. Not verified

- ~~The 288-config grid did not finish.~~ **CORRECTED 2026-09-03.** It did
  finish. `/root/evalrun/grid.out` on `newsapp` holds the complete ranked
  output: `BASELINE`, `SHIPPED`, "126/288 configs rejected for producing a
  cluster larger than 60 articles", and the ranked survivors. The OOM was
  inferred from a truncated terminal scrollback, not from the file, and the
  claim that the shipped config "is unlikely to be beaten by its own sweep"
  was wrong on both counts — it was never checked against the file, and the
  file shows it beaten:

  | Config | P | R | F1 | max cluster |
  |---|---|---|---|---|
  | Shipped today | 0.955 | 0.542 | 0.691 | 29 |
  | `jaccard/title>=0.3 shared>=3` | 0.901 | 0.701 | 0.789 | 59 |
  | `idf_overlap/title>=0.5 shared>=3` | 0.890 | 0.723 | 0.798 | 55 |

  Neither is safe to ship on F1 alone: both sit against
  `MAX_PLAUSIBLE_CLUSTER = 60`, and the handoff's own gotcha records 148 of
  288 configs scoring well while building blob clusters. Validate with
  `inspect` before adopting either. See §9.
- Cost figures in §5.J combine measured token counts (`count_tokens` over 25
  real clusters for input; live calls for output) with projected volume from
  the eval replay. They are not billing data — the account key is a regular
  API key, so actual spend has to be read from console.anthropic.com.
- Output tokens are assumed flat at 450/call as outlet count rises. Framing
  entries scale with outlets, so this likely under-estimates slightly for
  large clusters.
- The 50% Batch API saving is list pricing, not something measured on this
  workload.
- Read-rate figures are directional only at 110 reads/week.

---

## 9. Error analysis: where recall is actually lost (2026-09-03)

Run with `scripts/eval_clustering.py errors` over the same fixture and
labels that produced the 0.542 recall (469 labelled `same` pairs present in
the replay; 254 merged, 215 missed).

| Reason the pair was not merged | Count | Share | Median jaccard |
|---|---|---|---|
| `below_threshold` | 183 | 85.1% | 0.267 |
| `lost_to_ordering` | 16 | 7.4% | 0.545 |
| `geo_guard` | 16 | 7.4% | 0.500 |

Similarity of the 215 missed pairs, against a 0.40 threshold:

| Band | Count | Share |
|---|---|---|
| 0.00-0.05 | **0** | **0.0%** |
| 0.05-0.15 | 8 | 3.7% |
| 0.15-0.25 | 59 | 27.4% |
| 0.25-0.40 | 124 | 57.7% |
| 0.40+ | 24 | 11.2% |

**This is the argument against Phase 2 (embeddings), and it is not close.**
The premise of that phase is that outlets describe one event in words that
do not overlap, so only a semantic representation can bridge them. Among
pairs this harness can evaluate, that case does not occur even once. The
misses are near-things: "Piyush Goyal defends India's 7.8% GDP growth,
rejects questions over data credibility" against "Piyush Goyal Defends
India's 7.8% GDP Growth, Hits Back At Opposition Critics" scores 0.385 and
is rejected by a 0.40 cutoff.

**The caveat that keeps this honest.** Candidate pairs are generated by
blocking plus top-k similarity (`_build_pairs`), so a pair with no lexical
overlap is never proposed to the labeller in the first place. The zero in
the top band is therefore partly an artifact of how the pair set is built:
it shows the harness cannot see that failure mode, not that the failure
mode does not exist. Embeddings are neither justified nor refuted by this
evidence. What is established is that a large, cheap, already-measured win
sits in front of them, and it should be taken first.

Ranked by size, the recall budget is:

1. **Threshold / metric retune — 85% of the loss.** Already priced by the
   grid above: recall 0.542 -> ~0.71 for precision 0.955 -> ~0.90. Gated on
   the `inspect` check for blob clusters.
2. **`geo_guard` — 7.4%.** Regional outlets covering national stories are
   blocked from merging with national ones (Deccan Chronicle and Mid-Day
   both reporting the same Nanded road crash). The guard is doing real work
   against genuinely local stories; it needs a carve-out, not removal.
3. **`lost_to_ordering` — 7.4%.** Pairs that pass every gate and are still
   separated, because assignment is greedy and single-pass: whichever
   cluster a article sees first wins. Median 0.545, one at 0.833. No
   threshold change reaches these — it needs a second pass or a merge step.
