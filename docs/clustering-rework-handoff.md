# Clustering rework — session handoff (2026-09-02)

Read this first if you are picking up the clustering/enrichment work.
Everything below is measured, not estimated, unless it says otherwise.

---

## 1. Why this work happened

The app's premise is curated, multi-source story clusters with a framing
comparison across outlets. Production measurement on 2026-09-02 showed that
product was not shipping:

| Fact (30-day window, before the fix) | Value |
|---|---|
| Story clusters | 48,473 |
| Singletons (1 article) | 47,733 — **98.5%** |
| Clusters with 2+ distinct sources | **418 — 0.9%** |
| Clusters with `framing_comparison` populated | **47,018** |

So ~99% of the flagship framing feature was an LLM "comparing" a single
article against nothing.

Scored against 1,020 LLM-labelled article pairs, the old matching rule had
**precision 1.000, recall 0.027**. It was not conservative, it was inert.

**The causal chain matters** — this was one bug, not several:

1. Clustering failed to merge → 98.5% singletons.
2. *Because* ~99.7% were singletons, the `len(articles) >= 2` cost guard in
   `enrichment.py` was deliberately removed (the comment said so).
3. So every singleton got a paid enrichment pass and a fabricated framing
   comparison.
4. ~$255/mo of spend, and Anthropic credit exhausted.

The app had **not** drifted from its mission by adding games. It shipped a
broken version of the mission and backfilled the empty feed with volume and
engagement features. Games/horoscope run on APIVerve, not Anthropic, and are
not the cost problem. Entertainment is only 4.4% of volume.

---

## 2. What is live in production right now

Both droplets (`newsapp`, `newsapp-2`) are on **`2d89631`** (as of 2026-09-03).

### Shipped clustering config

Chosen by grid search over 288 candidates against the labelled pair set —
re-tune with the harness, never by intuition.

```
metric:        Jaccard over TITLE tokens only   (was: title+snippet)
threshold:     0.40                             (was: 0.25)
min_shared:    2
simhash:       REMOVED as a gate                (was: hard veto at <= 18)
candidates:    cluster_tokens index, 48h window (was: 100 most-recent clusters)
comparison:    all cluster members              (was: representative only)
same-source:   blocked
```

### Verified results

| | Before | After |
|---|---|---|
| Precision (labelled pairs) | 1.000 | 0.944 |
| Recall (labelled pairs) | 0.027 | **0.528** |
| Merge rate, live production | ~1.4% | **10.4%** |
| Front page composition | ~98.5% singletons | 25/25 multi-article |

Live merge rate (10.4%) matched the offline dry-run prediction (10.0%) almost
exactly, so **the harness can be trusted** for future tuning.

### Also live

- Framing gated on `distinct_source_count() >= 2` (outlets, not articles).
- The falsy-`[]` overwrite bug fixed (see §5).
- `news-enrich.timer` capped at `TIMER_ENRICH_LIMIT = 100` per run, window
  narrowed to `since_days=0.5`. The unit file was edited directly on
  `newsapp`; backup at `/etc/systemd/system/news-enrich.service.bak-20260902-173250`.
- Model routing: `SINGLE_SOURCE_MODEL = claude-haiku-4-5`,
  `MULTI_SOURCE_MODEL = claude-sonnet-5`.

---

## 3. START HERE TOMORROW

*All of (a)-(e) completed 2026-09-03. Next: Phase 5 (curated front page) in §4.
Open threads: 7 unexplained post-fix fabrications; 95.6% vs 91.9%
singleton-rate gap.*

### a) Deploy the pending commit — `2d89631` — ✅ DONE (2026-09-03)

Deployed to both droplets. Production is now on `2d89631`.

**What it fixes:** `enrich_clusters` selects only
`entities IS NULL OR ai_enriched IS FALSE`, so a cluster enriched while it was
a singleton is never revisited — it keeps its one-outlet framing however many
outlets it later gains. **202 clusters already had fewer framing entries than
they had sources**, including a 3-outlet story on the front page whose framing
listed one outlet. Not invented like the old bug, but identical to a user.

### b) Rotate the Anthropic API key — SECURITY — ✅ DONE (2026-09-03)

Key rotated and `.env` updated on both droplets. The old key was pasted into
the 2026-09-02 session transcripts (both `e2c9f5d9` and `11ce9c53`) and is
dead. Never paste a live key into a prompt — export it in the shell instead.

### c) Purge historical fabrications — DONE (2026-09-03)

Applied. **47,164 rows nulled**, 536 legitimate multi-outlet framings kept.

| Check after apply | Result |
|---|---|
| Clusters with `framing_comparison` | 47,700 -> **536** |
| Remaining with <2 distinct sources | **0** |
| `json_typeof = 'null'` rows | **0** (100 normalised to SQL NULL) |
| `entities` / `summary` untouched | 48,643 / 50,112 intact |

`framing_comparison IS NULL` now means what it says everywhere, so any future
query can trust it. The 7 post-fix fabrications from (d) were inside the
predicate and went with them — the question of how they were created is still
open, but the rows are gone.

### e) Repair the 199 stale-framing clusters — DONE (2026-09-03)

The purge did NOT cover these (their predicate is 2+ distinct sources, so they
sat in the 536 kept). Repaired by forced re-enrichment: **199 -> 0**, 0
failures, ~20 min, all on `claude-sonnet-5`.

**They could never have self-healed.** 197 of 199 had `ai_enriched = True`, so
the default selection (`entities IS NULL OR ai_enriched IS FALSE`) skipped them
permanently, and all predated the timer's `since_days=0.5` window. Targeted
force re-enrichment was the only route.

178 of them carried a *single* framing entry despite 2+ outlets. Worst cases
were the best stories — id 23914 (Trump/Lake Ontario) had 7 outlets including
Reuters, BBC, Al Jazeera and the Guardian, and framing for 2. After: 7/7.

**How it was run** (no deploy, no committed code): a one-off script piped into
the running container over `docker exec -i news_backend_prod python -u -`,
loading the 199 ids with the same
`selectinload(...).load_only(title, snippet, source_id)` the timer uses, so it
did not drag article bodies over metered Supabase egress. `enrich_cluster_with_ai`
commits per cluster, so a mid-run failure leaves completed work repaired.

**Re-enrichment regenerates headline and summary too**, not just framing. That
was accepted deliberately — the new values come from better cluster membership
than the originals had.

**The stale metric has a transient floor — do not chase it to zero.** Nine
clusters were flagged immediately after the run; all nine were created *during*
it, all `ai_enriched = False`, carrying rule-based baseline framing while
waiting for the next 20-min timer pass. Newly-created clusters always look
"stale" for up to ~20 minutes. Only clusters with `ai_enriched = True` and
`entries < distinct_source_count` are genuinely broken.

**Note on framing entry shape.** The AI path emits
`{"outlet", "headline_angle"}`; the older rule-based baseline also wrote
`headline`. Dropping it is safe and intended — the prompt's output spec has no
`headline`, the API schema is `Optional[Any]` passthrough, and the Android
`FramingItem.headline` is nullable and rendered nowhere (StoryDetailScreen
shows `outlet` + `headlineAngle` only, gated on `distinctOutletCount > 1`).

### d) Re-check the numbers over a full day — DONE (2026-09-03 02:52 UTC)

**Verdict: the clustering fix holds.** Re-measured over 922 articles.

**Measure against the deploy boundary, not a rolling 24h.** The container
restarted `2026-09-02 18:30:32 UTC`, so a "last 24h" window is ~two-thirds
pre-fix and makes the results look like a total failure — a naive 24h query
reports 3,274 single-source clusters carrying framing, of which only **7** are
actually post-fix. Cut every query at that timestamp.

Post-fix window (8.4h, 922 articles, 572 clusters created):

| Metric | Before | 2026-09-02 spot check | 2026-09-03 |
|---|---|---|---|
| Articles landing in multi-article clusters | ~1.4% | 10.4% (n=180) | **17.0%** (157/922) |
| Clusters with 2+ distinct sources | 0.9% | — | **4.4%** (25/572) |
| Largest cluster (guard: 60) | — | — | **7** — no blobs |
| Clusters enriched | — | — | 560/572 |

30-day rollups are still dominated by pre-fix history and will be until the
purge runs: 50,112 clusters, 98.3% singletons. The multi-source count has
climbed **418 -> 545**, which is the number to watch.

**Open: 7 post-fix fabrications.** Single-article, single-source, one framing
entry each, all created 18:37-22:10 UTC and none since. Ruled out: stale code
in the container (the gate is present at `enrichment.py:349`),
`distinct_source_count()` (correct), and a stale enrich path (the timer curls
the same container). Leading hypothesis: they had 2 distinct sources at
enrichment time and lost an article afterwards - all 7 have
`last_updated_at == first_seen_at`, i.e. enriched at creation, yet hold one
article now. 1.2% of the window; not the old failure mode.

**Watch:** the offline grid predicted a 91.9% singleton rate; production is
running 95.6%. Different denominators, so not alarming - but interrogate this
gap before spending effort on Phase 2 embeddings.

---

## 4. Remaining roadmap

Full plan: `~/.claude/plans/encapsulated-humming-firefly.md`

| Phase | State | Notes |
|---|---|---|
| 0 — eval harness | **DONE** | `scripts/eval_clustering.py` |
| 1 — clustering fix | **DONE, verified** | |
| 2 — embeddings | not started | **Recommend NOT doing this next (2026-09-03).** Recall IS the constraint now — the multi-source feed runs at 254 clusters/day against a predicted 350 — but the error analysis says embeddings are the wrong tool for it. Of 215 missed pairs, **zero** score below 0.05 jaccard; 85% are near-misses under the threshold. See `multi-source-feed-plan.md` §9, including the sampling caveat that keeps this from being a proof. |
| 2b — **recall retune** | **RECOMMENDED NEXT** | The grid already found it (see below): recall 0.542 → ~0.71 by changing two constants, for precision 0.955 → ~0.90. Gate on an `inspect` pass — both candidates sit at `max cluster` 55–59 against a cap of 60. |
| 3 — framing honesty | **DONE, verified** | |
| 4 — batch summaries | not started | Cost work. Less urgent now the timer cap stopped the bleeding. |
| 5 — curated front page | not started | **Recommended next.** This is what the user actually asked for: "trendy, debated, headliners upfront, very curated." Was impossible before (only 418 qualifying clusters); now viable. `entity_boost` is already computed and sitting unused (`poller.py`, never read by `/clusters`). |

### Smaller hygiene
- `news-enrich.timer` is monotonic-only (`OnBootSec` + `OnUnitActiveSec`), so
  any `systemctl stop` kills its schedule **permanently** — hit this on
  2026-09-02, recovered only by `systemctl start news-enrich.service` to
  re-anchor. Add `OnCalendar=*:0/20`. (`Persistent=true` is a no-op on
  monotonic timers.)
- Remove obsolete `version:` from `docker-compose.prod.yml`.
- Supabase pooler is **session mode, 15 clients, fully consumed**. No headroom
  for one-off tasks; a restart risks a reconnect stampede. Review `pool_size`
  in `app/database.py`.
- 4 pre-existing test failures unrelated to this work (crossword,
  daily_games, 2× enrichment). They fail identically at `0210e31`.

---

## 5. Gotchas that cost real time — read before debugging

**SQLAlchemy `JSON` columns and `None`.** Default `none_as_null=False`
persists Python `None` as the JSON *value* `null`, which is **NOT NULL** to
Postgres. This made a *working* framing gate look completely dead —
`WHERE framing_comparison IS NULL` returned 0 for 83 correctly-cleared rows.
Fixed with `none_as_null=True` on entities/topics/framing_comparison
(`b71c24e`). The API was never affected (JSON null → `None`), which is exactly
what makes it easy to miss.

**Run pyflakes before deploying.** A missing import in `poller.py` took
production ingestion down ~45 minutes. A `NameError` in a function body passes
import, passes `ast.parse`, and passes the unit tests. It also survived the
read-only dry run, because that script imported the constant from `dedup`
rather than exercising poller's reference to it.
```
python -m pyflakes app/ scripts/
```

**One bad source used to kill the whole poll cycle.** `poll_all_sources` read
`source.name` *outside* the per-source try; `session.rollback()` expires every
ORM object unconditionally, so the next iteration's attribute access did IO
and raised `MissingGreenlet`. Fixed in `911db4d` — the loop now iterates plain
`(id, name)` values and re-fetches inside the try.

**Never rank clustering configs on pairwise precision/recall alone.** 148 of
288 configs produced 100–725-article blob clusters *while scoring well*,
because labelled pairs are drawn from near neighbours. The first sweep's
"winner" built a 646-article cluster. Always cap on largest cluster size
(`MAX_PLAUSIBLE_CLUSTER = 60`) and eyeball real merges via `inspect` mode.

**Templated headlines defeat the lexical gate.** `shares_topic` scores two
different Yahoo Finance earnings transcripts at 0.5 and cannot tell them
apart. The defence is the **same-source guard**, which is structural, not
lexical — two *different* outlets publishing templated headlines would still
merge wrongly. The fix if that appears is `idf_overlap`, already implemented
and measured in the harness. Documented in `tests/test_dedup.py`.

**Deploys kill in-flight poll cycles.** The poll runs as a background task via
`POST /ingest/poll`; `up -d --build` mid-cycle silently aborts it. Cycles fire
at :07, :27, :47 UTC. Three cycles were lost to this on 2026-09-02.

**Server is UTC; user is IST (UTC+5:30).** Convert in anything user-facing.

**Migrations need a free connection.** The pooler ceiling means
`docker compose run --rm` fails with `EMAXCONNSESSION`. Stop the two
schedulers first:
```
docker stop news_poll_scheduler_prod news_crossword_scheduler_prod
docker compose -f docker-compose.prod.yml run --rm app python scripts/<script>.py
docker start news_poll_scheduler_prod news_crossword_scheduler_prod
```
Note `docker exec` runs the **old** image — code is baked in via `COPY . /app/`,
so `git pull` alone does not update a running container. Use
`build` + `run --rm` to test new code without swapping the live container.

---

## 6. Files and commits

| Commit | What |
|---|---|
| `99eea5b` | `cluster_tokens` model + migration + `eval_clustering.py` |
| `c63a76e` | The clustering fix (dedup + poller) |
| `f79ac02` | `validate_clustering.py` read-only dry run |
| `0ca7d07` | Framing gate, overwrite-bug fix, timer cap, purge script |
| `b71c24e` | `none_as_null=True` on JSON columns |
| `911db4d` | Missing import + poll-cycle containment |
| `2d89631` | Re-enrich on new outlet ← **deployed 2026-09-03** |

**Key scripts**
- `scripts/eval_clustering.py` — `fetch` / `pairs` / `label` / `grid` /
  `inspect` / `errors`. `inspect` and `errors` need no API credit and no DB;
  only `fetch` touches the pooler. `errors` explains WHERE recall is lost
  rather than how much (added 2026-09-03 — see `multi-source-feed-plan.md`
  §9). Labelling 1,020 pairs costs **$0.21** via Haiku Batch.
  Note `label` needs the `anthropic` package, which is in
  `requirements-dev.txt` and NOT in the production image — build the test
  stage for it: `docker build --target test -t news-eval .`
- `scripts/add_cluster_tokens_table.py` — migration + backfill (idempotent).
- `scripts/validate_clustering.py` — read-only dry run against live data.
- `scripts/purge_fabricated_framing.py` — dry run by default.

**Session artifacts** — NOT gone, and worth knowing where they are before
paying to rebuild them. On `newsapp`: `/root/evalrun/` holds `fixture.json`,
`pairs.json`, `labels.json` and `grid.out` from 2026-09-02/03. That
fixture+labels pair is the one that produced the 0.542 recall, so it is the
set to use for anything meant to compare against that number. Mount it read
into a container (`-v /root/evalrun:/eval`) rather than regenerating.

Anything written inside a `run --rm` container without a host mount is lost
on the next rebuild — that is how the first copy of these went missing.
Regenerating costs ~$0.21 plus a batch turnaround.

---

## 7. Cost

| | |
|---|---|
| Before | ~$255/mo, mostly fabricated framing on singletons |
| Burned 2026-09-02 by the uncapped timer | ~$20 (6,501 clusters) |
| Labelling job | $0.21 |
| Remaining unenriched backlog | ~6,500 clusters (~$20 if fully drained) |

The backlog is deliberately **not** being drained: `since_days=0.5` means only
the last 12 hours are enriched, and old clusters stay unenriched rather than
paid for. Widen the window only as a deliberate, attended decision.
