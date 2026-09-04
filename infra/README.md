# Infra config backups

Point-in-time copies of config that lives *outside* this repo, on the
droplet itself, because it's shared with an unrelated project
(`scalp8.xyz`) and hand-edited directly on the box (see
`india-news-app-handoff.md` §8 Phase 6c and §10). These are **not** synced
automatically — pull a fresh copy with:

```
ssh vps "cat /etc/caddy/Caddyfile" > backend/infra/Caddyfile
```

- `Caddyfile` — system-level Caddy config (`/etc/caddy/Caddyfile`), last
  pulled 2026-08-09, **now stale**: as of 2026-08-22 both `newsapp` and
  `newsapp-2` run a trivial `:80 { reverse_proxy 127.0.0.1:8080 }` instead
  (no more per-domain blocks, no more Caddy-managed TLS — see the load
  balancer entry below for why). The `scalp8.xyz` blocks in this backup
  belong to the other project that used to share the host and are unrelated
  to this app regardless.

Re-pull and commit whenever the box's Caddy config changes — closes
production-readiness-gaps.md gap #9 ("lives outside version control").
This is a backup, not a deploy target: don't wire this file into any
script that pushes it back to the droplet without deliberately deciding
to bring Caddy under real config management first.

- `news-poll.service` / `news-poll.timer` — systemd units on `newsapp`
  (`/etc/systemd/system/`) that call `POST /api/v1/ingest/poll` every 20
  minutes. There was previously **no automation at all** calling this
  endpoint — it existed only as a manually-triggered route, discovered
  2026-08-22 when the feed had gone ~18h without a new cluster. Deliberately
  installed on `newsapp` only, not `newsapp-2`, to avoid burning the
  endpoint's 5/hour rate limit twice; `poller.py`'s own advisory lock would
  make a duplicate trigger harmless anyway, but there's no reason to pay for
  it. If `newsapp` is ever decommissioned, move these units to whichever
  droplet becomes primary — same caveat as Caddyfile above, these are a
  backup for reference, not something a script re-applies automatically.

- `news-enrich.service` / `news-enrich.timer` — same pattern, added
  2026-08-22, calling `POST /api/v1/ingest/enrich?since_days=2` **hourly**
  (offset 5min into the cycle so it tends to run after a poll has
  landed new clusters, not concurrently).

  **Was every 20 minutes until 2026-09-04.** The poller now runs an
  enrichment cycle itself as soon as a story becomes corroborated
  (`multi-source-feed-plan.md` §5.D), so this timer is no longer what a
  story entering the feed waits on. It is the safety net: crossings whose
  event-driven run failed or was skipped on a held lease, plus the
  refinement passes at the 3rd/4th/5th outlet, which go to the Batch API
  and which nobody is waiting on. **If the event-driven trigger is ever
  reverted, put this back to 20min** — at hourly on its own, a corroborated
  story could sit up to 60 minutes showing its raw RSS headline.

  Deliberately **no** `force_all`
  on the recurring timer — it only enriches clusters missing `entities` or
  never successfully `ai_enriched`, so it doesn't re-bill the same
  already-enriched clusters every cycle. Also installed on `newsapp` only,
  same rate-limit reasoning as the poll timer. A one-off forced re-enrich of
  the 2-day backlog (e.g. right after a prompt/logic change) is a manual
  call: `curl -X POST ".../api/v1/ingest/enrich?since_days=2&force_all=true"`.

## Load balancer + TLS (added 2026-08-22)

`openindiannews.com` scaled from a single droplet to `newsapp` +
`newsapp-2` behind a DigitalOcean Load Balancer (`blr1-load-balancer-01`,
Bangalore/BLR1). Two non-obvious gotchas hit while setting this up:

**DNS is on Namecheap, not DigitalOcean** — the domain's nameservers are
Namecheap's (`registrar-servers.com`), so DO's "Let's Encrypt via the LB"
flow doesn't work: it insists on importing the domain into DO's own DNS
management first and fails with `failed to validate nameserver records: a
non DigitalOcean Name Server was found`. Worked around by getting the cert
manually with `certbot --standalone` on `newsapp` (briefly the sole LB
backend, so the HTTP-01 challenge can only land on the box actually running
certbot) and uploading it to the LB as "Bring your own certificate" instead.

**Two certbot chain pitfalls, both silent until an Android client hits them:**
1. Certbot's default key type is ECDSA, which as of late 2025 chains
   through Let's Encrypt's newer `ISRG Root X2` root. Plenty of Android
   trust stores don't have X2 yet, so the app fails with
   `java.security.cert.CertPathValidatorException: Trust anchor for
   certification path not found` even though the cert is completely valid
   and verifies fine on this machine (modern Android/curl/browsers already
   have X2). Fix: force `--key-type rsa` on issuance — the RSA hierarchy
   still chains through the long-trusted `ISRG Root X1`, which basically
   every Android version since 7.1.1 has. (Need `--cert-name
   openindiannews.com` alongside `--key-type rsa` on re-issuance, or
   certbot refuses the key-type change as a safety check.)
2. DO's "Bring your own certificate" form has a **"Certificate" field that
   accepts exactly one PEM block** (the leaf, `cert.pem`) — pasting
   `fullchain.pem` there is rejected outright ("leaf certificate must
   contain exactly one PEM block"). Intermediates go in the separate
   "Certificate chain" field (`chain.pem`). Getting this split wrong (or
   leaving the chain field blank) means the LB serves the leaf alone,
   which many real clients — Android chief among them — silently can't
   validate without fetching the missing intermediate themselves.

**How to actually verify the fix worked** — `curl`/most tooling report
`Verify return code: 0 (ok)` even when only the leaf is served, because
their local trust store or cache already has the intermediate cached.
Check the raw handshake instead:
```
openssl s_client -connect openindiannews.com:443 -servername openindiannews.com -showcerts
```
Look for **multiple** numbered blocks (` 0 s:.../i:...`, ` 1 s:.../i:...`,
etc.) ending at a root your target devices actually trust — one block
alone is the bug, even with `Verify return code: 0`.

Cert expires **2026-11-20**. It will *not* auto-renew on the LB — certbot's
timer on `newsapp` renews the on-disk file, but someone has to notice and
re-paste it into the DO LB's certificate before then.
