# Story graph mode — design log

Status: **experimental, faulty, in progress.** Not wired into any live
endpoint or UI. Lives entirely in `scripts/experiment_story_edges.py`
(read-only, no writes, no LLM calls). This doc is the record of how we got
here and what's still broken, so the next session doesn't have to re-derive
it.

## The goal

Let a user follow how a developing news story evolved over time — a
"story so far" timeline, distinct from the existing cluster-of-articles
view. Originally scoped in the "Feed ranking redesign" design doc as
story↔story continuation edges, never built. This is that build, started
from scratch as an offline experiment.

## How we got here

### Round 1 — flat pairwise scoring, no LLM

Hypothesis: cheap entity-overlap between clusters, no LLM confirmation,
might be strong enough on its own. First pass scored all (earlier, later)
cluster pairs sharing entities, weighted by `entity_stats.baseline_rate`.
Broke immediately: `entity_stats` only had ~700 rows against ~11k distinct
entity keys in a 60-day window, so almost everything fell back to a flat
default weight — no real rarity signal. Switched to **in-set IDF**
(document frequency within the loaded batch) instead, and added a
`min_shared >= 2` floor since a single shared entity (e.g. both stories
merely mention "Apple") produced massive false-positive fan-out (610k
candidate pairs from ~10k clusters, dropped to ~62k after the fix).

### Round 2 — chaining, drift, generic entities, bursts

Turned pairs into chains via connected components + "best predecessor"
per cluster. This let chains **drift**: each hop only had to match its
immediate neighbor, so a chain could wander off-topic one locally-plausible
link at a time (observed: "Nilgiris water stress" → "elephant deaths" →
"man-eating tiger" → "leopard poaching", each hop sharing only a location
entity with its neighbor, the chain as a whole not one story). Fixed by
**anchoring every candidate to the chain's ROOT** entity set instead of its
neighbor.

That surfaced a second problem: **generic entities** (India,
government_of_india, tamil_nadu, tamil_nadu_government, BJP, Congress)
still satisfied `min_shared >= 2` together and produced fake "topic bucket"
chains that were really just "India politics," not a story. IDF weighting
alone doesn't stop this since `min_shared` counts entities, not weight.
Added a document-frequency ceiling (`--max-df-ratio`) to prune generic
entities from matching entirely, same idea as stopword removal before
TF-IDF.

A third problem: a `--min-gap-hours` filter, meant to separate "same event,
multiple outlets, minutes apart" from "genuine days-later development,"
was gating **thread membership** on time-since-last-addition — which split
single bursty stories (e.g. Govinda/Sunita Ahuja divorce coverage) into
orphaned parallel chains, because the second same-hour article got
rejected from the first thread and spun off its own. Fixed by making
membership purely score-based and using the gap only to *tag* bursty hops
for display.

At this point the user asked directly: **are we overfitting?** Answer:
yes, structurally — every fix so far came from eyeballing the top-N of one
fixed 60-day snapshot and reacting to what looked wrong, with no
visibility into the false-negative rate, the tail of the ranking, or
whether the tuned constants generalize past this particular news cycle.
That's a known, unresolved gap in how this has been validated so far.

### Round 3 — redesigned from a decision tree, not more patches

Rather than keep patching a uniform scoring pass, the user proposed
designing this as an explicit node-by-node decision tree, discussed and
confirmed one node at a time before writing any code. Full derivation is
in the conversation history; the resulting tree:

1. **Node 1** — collect candidate stories sharing ≥1 entity (inverted index).
2. **Node 2** — pick the entity with the highest **within-match** frequency
   as the candidate "actor" (this alone already tends to pick the
   media-framed lead character — e.g. Govinda over his wife Sunita Ahuja
   — without needing a separate signal).
3. **Node 3** — is that actor generic? Checked against
   `entity_stats.baseline_rate` (the slow 75-day EMA — real long-run
   commonness), NOT `mention_count_decayed` (that's a fast/spiking signal,
   the opposite meaning). Entities `entity_stats` hasn't seen yet
   (still a young table, ~750 rows) fall back to in-set document frequency.
4. **Node 4** — if generic, try the next-highest-frequency entity in the
   match, loop back to Node 3.
5. **Node 5** — one confirmed-specific entity is enough to link two
   stories; no arbitrary shared-count threshold needed once genericness is
   checked directly (this replaces round 2's `min_shared >= 2` hack).
6. **Node 6** — every cluster mentioning that actor forms a "topic group."
7. **Node 6b** — subsumption: a topic group that's near-entirely contained
   in an already-kept larger group (Sunita Ahuja ⊂ Govinda) is dropped.
8. **Node 7** — within a topic group, sub-cluster **ignoring time**, scored
   only on entities *other than* the group's own actor (the actor is
   shared by every member by construction, so it's uninformative for
   telling sub-stories apart — e.g. Govinda's movie news vs. his divorce
   news need their own distinguishing entities).
9. **Node 8** — within each sub-cluster, flag members whose
   nearest-neighbor time gap is a large multiple of the sub-cluster's
   typical gap.
10. **Node 9** — those outliers become **branches** off the main
    chronological trunk, not dropped, not force-merged.
11. **Node 10/11** — rank chains by importance instead of hand-listing
    junk categories: per-hop "hotness" = `distinct_source_count` (coverage
    breadth) × the actor's reactivation ratio
    (`mention_count_decayed / baseline_rate` — is it spiking right now),
    rolled up across the trunk as an **EMA** (reusing
    `app/services/decay.py`'s `ema_update`, the same function
    `entity_stats` already uses) so recent, well-covered, spiking hops
    dominate a chain's rank and old/quiet ones fade. Rationale: routine
    content (a daily lottery result, a recurring gadget-spec leak) never
    spikes, so it sinks in ranking on its own — no dedicated "is this
    junk" rule needed, which was the trap round 2 was falling into
    (discovering and hand-coding one junk category at a time).

Script fully rewritten against this tree
(`scripts/experiment_story_edges.py`, commit `773053f`).

### Round 4 — found a real bug, fixed it, found the next layer of the same bug

First run against the tree: individual chains read as genuinely coherent
single stories (NHRC/Punjab CM's-wife bribery case, Jadavpur University
clash, Brydon Carse nightclub incident, TVK Tamil Nadu politics), and
Node 7's actor-exclusion correctly split a person's two *unrelated*
concurrent stories apart (Kareena Kapoor's film-casting news vs. her
pregnancy-rumor news stayed as two separate chains, correctly).

But: **the same real story kept surfacing multiple times under different
actor labels, undeduplicated.** Root cause: Node 6b's subsumption runs on
full topic groups *before* Node 7 splits them — two actors' full groups
can be mostly disjoint overall (Dhanush's group includes many unrelated
Dhanush stories) while the specific sub-cluster each narrows down to is
identical (the Bhansali-film-casting story, independently anchored by
`dhanush`, `kareena_kapoor`, `bollywood`, and `sanjay_leela_bhansali`).
Added a second subsumption pass (`dedup_sub_clusters`, commit `0506e56`)
run on the final sub-clusters instead of the pre-split groups. This fixed
the clean 2-cluster cases (the `national_test_agency_nta`/`jaipur`
duplicate went away).

It did **not** fully fix the harder cases, which surfaced on the next
run:

- **Hyderabad Aston Martin crash** split across 4 actors
  (`lingamaneni_sanjush`, `rajya_sabha`, `jana_sena`,
  `lingamaneni_ramesh`) with only partial pairwise member overlap between
  any two — never crossing the 80% subsumption threshold on any single
  pair, even though it's one real story.
- **Brydon Carse nightclub incident** split into two chains where the
  *worse* fragment outranked the *better* one: the `brydon_carse`-anchored
  chain correctly assembled 8 of the real developments but stopped short
  of the actual final development (`#10354`, "ECB Drops Brydon Carse");
  a coincidental `derby` topic group picked up 2 of those clusters *plus*
  the missing final one. The 8-member chain scored importance 1.05; the
  2-member fragment scored 1.98 — the opposite of what should surface
  first. This isn't a dedup problem (nothing to subsume, the member sets
  barely overlap) — Node 7 or Node 8 dropped `#10354` from
  `brydon_carse`'s own chain in the first place, and dedup can't recover a
  member a chain never had.

## Current hypothesis — actor TYPE, not just actor frequency/genericity

The user's read on both remaining failures, from eyeballing this and
earlier runs: the tree's actor-selection (Nodes 2-4) filters on
**frequency/genericity** but not on what *kind* of thing the entity is,
and that's the missing discriminator:

- **Bad actor candidates** (should probably be disqualified regardless of
  their frequency/genericity score): place names and place-like aliases,
  collective/industry labels (`bollywood`), source/publication names
  leaking in as entities (`hindustan_times_entertainment`, `livemint`,
  `gadgets_360`) — these describe a *backdrop* the story happens against,
  not something the story is *about*. `derby` in the Brydon Carse case is
  exactly this kind of false actor.
- **Good actor candidates**: a person, a group of people, or something
  that affects a group of people as a collective subject — the example
  given is a war/conflict. These are things a story can genuinely be
  *about*, as opposed to things a story merely *happens near*.

This isn't yet a designed tree node — it's an observation from two rounds
of manual review, not yet tested against a broader sample. Likely shape:
an entity-type/role classifier (or a curated/learned allow-list of
person/organization "agent" entities vs. "backdrop" entities layered on
top of the existing `person`/`organization`/`location` typing already in
`entity_graph.py`) sitting alongside Node 3's genericity check — an entity
could be non-generic (rare, specific) and still be a bad actor if it's
fundamentally a backdrop/label rather than a subject.

## What's confirmed working vs. still open

**Working / validated by repeated manual review:**
- In-set IDF > `entity_stats.baseline_rate`-only weighting (data maturity).
- Root-anchoring > best-predecessor chaining (drift).
- Explicit genericity check > relying on `min_shared` alone.
- Actor-exclusion in Node 7 correctly splits unrelated concurrent stories
  sharing one person (Kareena Kapoor's two unrelated stories).
- EMA-based importance ranking (recency + coverage) surfaces real breaking
  news near the top without hand-listing junk categories.

**Open / broken:**
- Multi-actor story fragmentation survives partial dedup — need either a
  lower subsumption threshold (risks false merges elsewhere, untested),
  or the actor-type refinement above, or both.
- Node 7/8 can silently drop a real member from the "best" chain while a
  worse fragment picks it up elsewhere and outranks the complete chain —
  root cause not yet traced (why did `#10354` not land in
  `brydon_carse`'s sub-cluster?).
- No quantitative validation at all yet — every judgment so far has been
  "does this look right," on the top-N of one 60-day window. No labeled
  sample, no precision number, no check against a second time period.
- `entity_stats` is still young (~750 rows as of this doc) — the Node 3
  genericity check is running mostly on the in-set-document-frequency
  fallback for now, not yet on mature `baseline_rate` data.

## Next steps (not started)

1. Design and test the actor-type refinement (person/group/collective-
   affecting-entity vs. place/source/label) — most likely to resolve both
   open bugs at once per the user's hypothesis, rather than chasing them
   individually.
2. Trace why Node 7/8 excluded `#10354` from `brydon_carse`'s own chain,
   to confirm whether the actor-type fix would also fix this or if it's a
   separate bug.
3. Build some form of quantitative validation (a hand-labeled sample, or
   a second time-window comparison) before trusting precision claims made
   from eyeballing alone.
