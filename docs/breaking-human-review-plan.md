# Breaking slot: human-review redesign

Status: planned, not started. Written 2026-09-09 after the Sep 7-8 cost
spike (see the daily-cost review that day — Sonnet 5 spend rose from
$4-8/day post-clustering-fix to $14.96 and $24.21 on the two days the
Breaking slot and story-timeline features went live).

## Why

`app.services.breaking.process_breaking_cycle` currently spends an LLM call
(`judge_and_extract_beats`, Sonnet 5, full cluster context, up to 16000
`max_tokens`) on *every* cluster that crosses the velocity gate, whether or
not it turns out to be genuinely developing — the "developing vs. echo"
judgement call the prompt makes is exactly the kind of subjective editorial
decision a human makes in one glance and an LLM has to reason its way to
(see `SYSTEM_PROMPT` in `app/services/breaking_narrative.py`: "the large
majority... are simply reporting the SAME fact... not adding anything
new"). Refresh calls have the same shape: every +5-source crossing pays for
an LLM call to find out whether the new sources added anything, even when
they're pure echo.

Moving that yes/no decision to a human (via the existing `admin_notify.py`
push pattern) eliminates the LLM call entirely for rejects and echo
refreshes — estimated ~50% reduction in steady-state spend on this feature
(see the cost-comparison discussion the same day). The refresh call itself
is already append-only and cheap (`REFRESH_SUFFIX` — new articles + existing
beats only, not the full cluster); this plan does not change that shape,
only what gates it firing.

## Current flow (for reference)

```
poller cycle (every 20 min)
  → expire_stale_breaking()               [SQL only]
  → detect_breaking_candidates()          [SQL only, up to MAX_ACTIVE_BREAKING=2]
      → judge_and_extract_beats(articles)  [LLM, full cluster]  ← fires unconditionally
      → write breaking_stories(status=active|rejected)
  → find_refresh_candidates()             [SQL only, +5 sources since last gen]
      → judge_and_extract_beats(new_articles, existing_beats=...)  [LLM]  ← fires unconditionally
      → append beats, update last_generated_source_count
```

## Proposed flow

```
poller cycle (every 20 min)
  → expire_stale_breaking()               [unchanged]
  → detect_breaking_candidates()          [unchanged, SQL only]
      → write breaking_stories(status=pending_review)   [NEW — no LLM call]
      → notify_admin_breaking_review()                  [NEW — FCM push]
  → find_refresh_candidates()             [unchanged trigger: +5 sources]
      → write breaking_refresh_reviews(status=pending)  [NEW — no LLM call]
      → notify_admin_breaking_review()                  [NEW — FCM push, debounced]

admin reviews in /admin/breaking (new page)
  → approve candidate  → judge_and_extract_beats(articles, mode="narrative_only")  [LLM, fires here]
                        → breaking_stories.status = active
  → reject candidate   → breaking_stories.status = rejected                        [no LLM]
  → approve refresh    → judge_and_extract_beats(new_articles, existing_beats=...) [LLM, fires here]
                        → append beats, advance last_generated_source_count
  → reject refresh     → advance last_reviewed_source_count only                   [no LLM]

unreviewed fallback (poller cycle, or a light periodic check)
  → candidate pending_review > EXPIRE_REVIEW_HOURS  → auto-expire to `rejected`
    (a missed real story degrades to "no badge", never to a wrong badge)
```

## Schema changes

`breaking_stories`:
- `status` gains a new value: `'pending_review'` — occupies a slot the same
  way `'active'` does (so `detect_breaking_candidates`'s free-slot count
  must include it), but has no `title`/`beats` yet.
- New column `reviewed_at` (nullable) — when an admin acted, distinct from
  `last_generated_at` (when the LLM last ran), same reasoning as the
  existing `last_beat_at` vs. `last_generated_at` split.
- New column `last_reviewed_source_count` (nullable Integer) — advances on
  *every* refresh review (approve or reject); `last_generated_source_count`
  only advances when the LLM actually runs. `find_refresh_candidates`'s
  `+5` comparison switches from `last_generated_source_count` to
  `last_reviewed_source_count`, so a rejected (echo) batch doesn't
  re-notify forever and an approved batch's LLM call still only sees
  genuinely-new articles.

New table `breaking_refresh_reviews` (one row per pending refresh decision,
not folded into `breaking_stories` since a story can have at most one
pending refresh at a time but the history of decisions is worth keeping for
tuning, same reasoning as `BreakingStory.sources_at_promotion` being kept
for offline threshold work):
- `id`, `cluster_id` (FK), `status` (`pending` / `approved` / `rejected`),
  `source_count_at_review` (Integer), `created_at`, `reviewed_at`.

## New / changed modules

**`app/services/breaking.py`**
- `detect_breaking_candidates`: unchanged query, but the row it writes is
  `status='pending_review'` with no `title`/`beats`/LLM call — pure SQL,
  same as today's non-LLM steps.
- `find_refresh_candidates`: switch the `+delta` comparison to
  `last_reviewed_source_count`; write a `breaking_refresh_reviews` row
  instead of calling the LLM inline.
- `process_breaking_cycle`: drop the `judge_and_extract_beats` calls from
  both loops; add the two `notify_admin_breaking_review` calls (candidate
  and refresh), and the pending-review expiry sweep.

**`app/services/breaking_narrative.py`**
- Add a `mode="narrative_only"` path (or a second, smaller
  `SYSTEM_PROMPT_NARRATIVE_ONLY`) that drops the "decide if developing"
  framing, since a human already made that call — keep the beat-extraction
  and citation-verification instructions unchanged. Not expected to shrink
  the token cost much (still reads the full cluster — see the token-cost
  discussion), but removes a redundant reasoning step.
- `judge_and_extract_beats` keeps its refresh path exactly as-is (already
  additive/append-only — no change needed there, confirmed against the
  existing `REFRESH_SUFFIX` behavior).

**`app/services/admin_notify.py`**
- New `notify_admin_breaking_review(session, kind, cluster_id, ...)`
  following the existing `notify_admin_reviews_ready` shape: best-effort,
  never raises, reuses `ADMIN_USER_EMAIL` / `DeviceToken` / `admin_alerts`
  channel. Needs its own debounce so a fast-moving story crossing +5
  sources three times in an hour doesn't send three separate pushes —
  collapse to "N breaking items awaiting review" the same way
  `notify_admin_reviews_ready` collapses poll+quiz into one push, or rely
  on a short per-cluster cooldown.

**New `app/admin_breaking.py`** (mirrors `app/admin_timelines.py`'s
session/CSRF/nav/login pattern)
- `GET /admin/breaking` — list `pending_review` candidates and `pending`
  refresh reviews. For each candidate, show the cluster's articles
  (headline + outlet + time, same shape as `format_articles_for_prompt`)
  so approving is a fast read, not a research task. For each refresh, show
  the existing beats alongside just the new batch's headlines (see the
  design discussion — this must stay a 10-second glance, not a re-read of
  the whole story).
- `POST /admin/breaking/candidate/{cluster_id}/approve` — runs the
  narrative-only LLM call, writes `active`.
- `POST /admin/breaking/candidate/{cluster_id}/reject` — writes `rejected`,
  no LLM call.
- `POST /admin/breaking/refresh/{review_id}/approve` — runs the refresh LLM
  call, appends beats, advances both source-count columns.
- `POST /admin/breaking/refresh/{review_id}/reject` — advances
  `last_reviewed_source_count` only.
- Wire into `main.py`'s router includes and the admin nav, same as
  `admin_timelines`.

**`app/config.py`**
- `EXPIRE_REVIEW_HOURS` (suggest 2-3h — long enough for a human to see a
  push, short enough that a real breaking story doesn't sit unbadged all
  day) alongside the existing `BREAKING_MIN_SOURCES` etc. constants.

## Migration

Alembic revision: add `breaking_stories.reviewed_at`,
`breaking_stories.last_reviewed_source_count`, the `pending_review` status
value (no DB-level enum today — it's a plain `String(16)`, so this is just
a new string, no type migration), and the new `breaking_refresh_reviews`
table. No backfill needed — existing `active`/`rejected`/`expired` rows are
unaffected; `last_reviewed_source_count` can default to
`last_generated_source_count` for any pre-existing `active` row so the
first post-migration refresh check doesn't immediately re-flag it.

## Rollout / validation

1. Ship schema + backend changes behind the existing manual-SSH deploy
   flow, both droplets (per [[newsapp-infra-scaleout]]).
2. Confirm `pending_review` candidates actually stop firing LLM calls —
   check the Anthropic Console daily-cost chart the day after deploy;
   expect the Breaking-slot contribution to drop to near zero between
   admin actions.
3. Watch the admin's real day-to-day: is the push frequency tolerable
   during an actual fast-developing story (potentially several refresh
   reviews in an hour)? If it's too noisy, the debounce window in
   `notify_admin_breaking_review` is the lever, not the underlying +5
   trigger.
4. Once a couple weeks of review decisions have accumulated, revisit the
   50% reject-rate / 50% echo-rate assumptions used in the cost estimate
   against real `breaking_stories.status='rejected'` and
   `breaking_refresh_reviews.status='rejected'` counts.

## Explicitly out of scope for this pass

- Changing `BREAKING_MIN_SOURCES`, `BREAKING_WINDOW_HOURS`,
  `REFRESH_SOURCE_DELTA`, or `MAX_ACTIVE_BREAKING` — those are the
  detection thresholds, validated separately against 30 days of production
  data; this plan only changes what happens *after* a candidate is
  detected.
- Reducing dev/testing spend from `scripts/dry_run_breaking_narrative.py`
  — that was the larger contributor to the Sep 7-8 spike and is a testing-
  discipline fix (e.g. a `--dry` flag that prints the prompt + token count
  without calling the API), independent of this redesign.
