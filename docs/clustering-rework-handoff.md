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

Both droplets (`newsapp`, `newsapp-2`) are on **`911db4d`**.

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

### a) Deploy the pending commit — `2d89631`

Pushed but **NOT deployed**. Nothing is broken while it waits.

```
cd /root/india-news-backend && git pull
docker compose -f docker-compose.prod.yml up -d --build
```
Same on `newsapp-2`.

**What it fixes:** `enrich_clusters` selects only
`entities IS NULL OR ai_enriched IS FALSE`, so a cluster enriched while it was
a singleton is never revisited — it keeps its one-outlet framing however many
outlets it later gains. **202 clusters already had fewer framing entries than
they had sources**, including a 3-outlet story on the front page whose framing
listed one outlet. Not invented like the old bug, but identical to a user.

### b) Rotate the Anthropic API key — SECURITY

The live key was pasted into the 2026-09-02 session transcript. Treat as
compromised. Regenerate, update `.env` on **both** droplets, `up -d`.

### c) Purge historical fabrications

~42,000 rows. Dry run first:
```
docker compose -f docker-compose.prod.yml run --rm app python scripts/purge_fabricated_framing.py
# then --apply
```
Deferred deliberately overnight so clustering could first promote singletons
that now deserve real framing.

**Important:** the purge does NOT fix the 202 stale-framing clusters. Its
predicate is `<2 distinct sources`; those now legitimately have 2+. They are
repaired by re-enrichment (item a), not by purging. Two different problems
that look identical in the app.

### d) Re-check the numbers over a full day

Tonight's merge rate came from ~180 articles (~40 min). Re-measure over
several thousand. The 30-day multi-source count should climb off its 418
baseline visibly.

---

## 4. Remaining roadmap

Full plan: `~/.claude/plans/encapsulated-humming-firefly.md`

| Phase | State | Notes |
|---|---|---|
| 0 — eval harness | **DONE** | `scripts/eval_clustering.py` |
| 1 — clustering fix | **DONE, verified** | |
| 2 — embeddings | not started | Recall headroom (0.53 → higher). **Recommend waiting** — see whether 10.4% is actually the constraint before adding an ONNX model to a 3.8GB box. |
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
| `911db4d` | Missing import + poll-cycle containment ← **deployed** |
| `2d89631` | Re-enrich on new outlet ← **pushed, NOT deployed** |

**Key scripts**
- `scripts/eval_clustering.py` — `fetch` / `pairs` / `label` / `grid` /
  `inspect`. `inspect` needs no API credit and is the fastest way to sanity-
  check a config. Labelling 1,020 pairs costs **$0.21** via Haiku Batch.
- `scripts/add_cluster_tokens_table.py` — migration + backfill (idempotent).
- `scripts/validate_clustering.py` — read-only dry run against live data.
- `scripts/purge_fabricated_framing.py` — dry run by default.

**Session artifacts** (scratch, may be gone): fixture/pairs/labels/grid JSON
under the 2026-09-02 job scratchpad. Regenerate with `fetch` + `pairs` +
`label` for ~$0.21 if needed.

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
