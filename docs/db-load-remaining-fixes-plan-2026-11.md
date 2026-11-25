# DB load: remaining fixes plan

2026-11-25 · Living copy: https://claude.ai/artifact/CBadCJtnE6GxgL8KKDV4gM

## Where things stand

The database is stable again, and since this afternoon the website serves only its static pages. The one load we could not fix in code, a crawler running about 39 searches a minute, now has nothing to hit. The Android app and its API are untouched.

The outage was self-inflicted, not traffic. On a Micro instance (1 GB RAM, 11 MB/s disk once the burst budget is spent) background jobs and unindexed queries drained the Disk IO budget until every query timed out.

| Commit | What it fixed | Effect |
| --- | --- | --- |
| `873acca`, `83a8bb4` | Score rewrite touched ~17k rows every 20 min and ~350k daily, each write hitting 11 indexes | Only rows that moved > 5% (sweep: > 10%, < 10 days old) are rewritten; WAL per cycle ~85 MB to ~10.5 MB |
| `a79320d`, `bb7e8bc` | `/search` ILIKE scanned all 390k clusters, ~230 MB per call | `cluster_search` side table with trigram indexes; ~13,000 to ~350 blocks per call |
| `1c29b18` | Notify job loaded ~104k full article rows every 15 min per droplet | Per-source `LIMIT 1` lookup; ~115k blocks to ~300 |
| `85007b0` | `/sources` uncached, ~45 calls/min, ~3 GB/day egress | Redis cache, 5 min |
| `f4c5104` | Duplicate-image check read ~1,500 blocks per new article | Partial index; 5 buffers |
| `15d09f9` | Crawler on `/topic/*` driving `/search` | robots.txt + 10 min cache; did not reduce the rate (each query is unique) |
| `.env` on both droplets | Same crawler | `WEB_DIST_DIR=/app/web/dist-disabled`: web app off, static pages only |

Disk IO Balance recovered 17% (10:30 IST) to 27% (13:52 IST); demand ran ~3.4 to 6 MB/s against the 11 MB/s baseline. All five services are running on newsapp; app, pollworker and contentworker on newsapp-2.

## Problem 1: the search crawler

The crawler must be stopped at Cloudflare, before it reaches the droplets. Caching and robots.txt cannot stop it, and the static-site switch only hides it.

**What it is.** Headless Chrome/155 on Linux from about 10 IPs, loading `/topic/<slug>` pages. Each page runs `GET /api/v1/search?limit=50`. At 15:08 IST it was still ~39 searches a minute, the same as before `15d09f9`.

**Why the first fix failed.**

- Every query is different, so the 10-minute Redis cache never hits.
- It ignores robots.txt. Cloudflare's managed robots.txt also prepends an `Allow: /` group above ours.
- At ~350 blocks a call it is cheap now, but still ~14k blocks a minute for no real reader.

**Fix, in order.**

1. Cloudflare WAF custom rule: managed challenge (or block) when the URI path starts with `/api/v1/search` or `/topic/` AND the user agent contains `HeadlessChrome` or `Chrome/155` AND the platform is Linux. A real browser passes a challenge; a headless crawler usually does not.
2. Cloudflare rate limit on `/api/v1/search`: about 20 requests a minute per IP, action challenge. This is a backstop; 10 IPs at ~4 a minute each slip under any per-IP limit.
3. Server-side guard in the app (backup if Cloudflare is not enough): refuse `/search` with 403 for the same user-agent pattern before the query runs, and cap `limit` at 20 for web callers. Costs nothing per request; needs a deploy to both droplets.
4. Longer term: the website should not need a full-text search per topic page. A topic page can read a precomputed list (topic to cluster ids, refreshed each poll) from Redis, so a crawler costs a cache read, not a query.

**How to tell it worked.** `pg_stat_statements` calls for the search statement over 60 s, and `journalctl -u app.service | grep -c v1/search` on each droplet. Target: under 5 a minute from real users.

## Problem 2: bringing the website back

Turn the web app back on only after the crawler is blocked at Cloudflare and the Disk IO Balance is climbing. Otherwise the same load returns the moment the pages do.

**What is off now.** The React web app (`/topic/*`, `/saved`, feeds, search) returns 404, and its scripts are not served. Still up: `/`, `/privacy`, `/terms`, `/contact`, `/refunds`, `/about`, `/pricing`, `/donate`, `/credits`, `/delete-account`, `/feedback`, robots.txt and `/.well-known/assetlinks.json`. Shared `/story`, `/timelines` and `/explainers` links show their older plain pages, one row read each.

**Before re-enabling, all of these should be true.**

- [ ] Cloudflare rule from Problem 1 live, and its event log shows the crawler being challenged
- [ ] Disk IO Balance rising for a full day with all workers running
- [ ] Topic pages read a precomputed list, or `/search` has the server-side guard
- [ ] Every API call the web app makes on page load is cached in Redis (list each one and check)

**How to re-enable.** On each droplet remove the `WEB_DIST_DIR=/app/web/dist-disabled` line from `/root/india-news-backend/.env` (or restore `.env.bak-2026-11-25`), then `systemctl restart app.service`. Check `/topic/politics` returns 200 on both. Then watch the search rate for an hour.

## Problem 3: egress

The biggest known egress leaks are fixed. The next one is what each app restart pulls, and we have no measured numbers yet.

**Fixed.** `/sources` (~3 GB/day) and the notify job (~117 MB per call, every 15 min per droplet) were the two large ones. Search egress fell with its block count.

**Still open.**

- On every `app.service` start, each droplet reloads the related-stories graph and the full `entity_stats` table from the database. Each of today's deploys restarted the app on both droplets, so every deploy repeated this pull twice.
- Fix: build both once in a worker, store them in Redis (or a compact snapshot table), and have the app load that on start. A restart then costs one Redis read, not a full table pull.

**What is missing.** The Supabase Usage page egress numbers (today, and month to date against the Pro quota). Without them we are ranking egress fixes by estimates from `pg_stats.avg_width` × rows, not by measurement.

- [ ] Read the Usage page egress figures and record them here
- [ ] Measure one app start's egress (bytes from `pg_stat_statements` before and after a restart)

## Problem 4: smaller leftovers

None of these caused the outage. Each is cheap to fix and worth doing before the next traffic rise.

| Item | Risk | Fix |
| --- | --- | --- |
| Admin search in `admin_api.py` and `admin_timelines.py` still ILIKEs `story_clusters` | Each use is a ~230 MB full scan; rare, but one careless loop would drain the budget again | Point both at `cluster_search`, like `search_clusters()` |
| Memory on Micro: 1 GB RAM, commit was 2.28 GB at the outage, 1.25 GB after | A pool or worker spike pushes Postgres into swap, which shows up as IO | Keep pool sizes small per droplet; alert when commit passes ~1.5 GB |
| `headline_score` leads two indexes, so every score write is non-HOT | Score writes stay costly even after the 5% gate | Check if those two indexes need the score first; if not, reorder or drop |
| 8 failing tests (enrichment batch, launcher feed teaser, feedback rate limit and others) | Pre-existing; they hide new regressions | Fix or mark them, so a green run means something |
| No alert on Disk IO Balance | We learned of the outage from timeouts | Alert below 50% and below 20% |

## Compute upgrade: when it is the answer

Not yet. A bigger instance would have hidden today's waste, not removed it, and the waste would have grown into the new limit.

- **Upgrade when** real-user load, measured after the fixes above, keeps disk reads above ~7 MB/s (most of the 11 MB/s baseline) or memory commit above 1 GB for days.
- **The first step up is mostly memory.** More RAM means more of the hot tables stay cached, which cuts disk reads directly. That helps more than extra CPU, which sat idle once IO wait cleared.
- **Do not upgrade** to make a crawler, a full scan or an unbounded job affordable. Fix the query instead.

## Order of work

Block the crawler first, then fix what restarts and admins can trigger, and turn the website back on last.

1. Cloudflare WAF rule and `/search` rate limit (Problem 1, steps 1 and 2). Owner: you, in the Cloudflare dashboard.
2. Record Disk IO Balance and the Usage-page egress figures; recheck the next day.
3. Server-side `/search` guard and admin search moved to `cluster_search` (one deploy to both droplets).
4. Related-stories graph and `entity_stats` loaded from Redis on start (Problem 3).
5. Topic pages read a precomputed list instead of running a search.
6. Re-enable the web app (Problem 2 checklist), then watch the search rate for an hour.
7. Disk IO Balance alert, the index-order check on `headline_score`, and the 8 failing tests.

**Undo today's static-site switch.** On newsapp and newsapp-2: remove `WEB_DIST_DIR=/app/web/dist-disabled` from `/root/india-news-backend/.env` (backup at `.env.bak-2026-11-25`), then `systemctl restart app.service`. No code or database change was made for it.
