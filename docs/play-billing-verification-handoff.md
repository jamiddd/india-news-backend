# Play Billing server-side verification — handoff

**Status as of 2026-09-17: DONE — code pushed on both repos, deployed and
verified on both droplets.** `POST /api/v1/billing/verify-purchase` returns
`{"valid":false}` for a garbage token on both `newsapp` and `newsapp-2`,
confirming the service account is correctly wired up and actually calling
Google's Play Developer API, not just returning a config error. Remaining
work is just uploading the new Android build (see step 6) — not blocking,
since the client already works with or without this (fail-open).

## What this is

Premium (subscription + lifetime) used to be trusted purely from the local
Play Billing callback (`BillingManager.kt`), which a rooted/tampered device
can fake. Now every purchase (new or restored) is re-checked against
Google's Play Developer API from the backend before premium is granted.
Deliberately anonymous — no app login required, matches today's paywall,
and cross-device restore still works via Play's own account system
(`BillingManager.restorePurchases()`), independent of this backend.

## Code (done, pushed)

- **App repo**, commit `e2dad15`: `BillingManager.kt` calls
  `NewsApiClient.verifyPurchaseBackend()` for every purchase before granting
  `_isPremium`. Fails open (grants premium) only if the backend itself is
  unreachable; fails closed on an explicit "not valid" or unrecognized
  product id.
- **Backend repo**, commit `23c692b`: `POST /api/v1/billing/verify-purchase`
  in `app/main.py`, logic in `app/services/play_billing.py`, schema in
  `app/schemas.py`, config in `app/config.py`.
- **Backend repo**, commit `0fdd032`: wired the secret into
  `docker-compose.prod.yml` too, for parity — but that file is legacy
  backup material, **not** the real production deployment path (see below).
- **Backend repo**, this session: `deploy/quadlets/app.container` — the
  actual quadlet unit used in production — updated with a second `Volume=`
  line for the Play Billing key.

## Deployment — DONE on both droplets (2026-09-17)

Real secrets path used: `/root/india-news-backend/secrets/play-billing-service-account.json`
on both `newsapp` and `newsapp-2` (same directory Firebase's key lives in).
The installed quadlet at `/etc/containers/systemd/app.container` on each
droplet already has the real `Volume=` line filled in (no more placeholder).

**Two gotchas hit during this deploy, in case this ever needs redoing:**

1. **`GOOGLE_PLAY_SERVICE_ACCOUNT_HOST_PATH` vs `GOOGLE_PLAY_SERVICE_ACCOUNT_PATH`**
   — `.env` only had the `_HOST_PATH` variant (used to fill in the quadlet's
   `Volume=` line by hand), but `app/config.py` reads
   `GOOGLE_PLAY_SERVICE_ACCOUNT_PATH` (the container-internal path) as a
   *separate* line — same pattern as `FIREBASE_CREDENTIALS_PATH` existing
   independently of `FIREBASE_CREDENTIALS_HOST_PATH`. Missing that second
   line is why the endpoint returned `{"detail":"Purchase verification is
   not configured"}` even after the volume mount and secret file were
   correct. Fixed by appending
   `GOOGLE_PLAY_SERVICE_ACCOUNT_PATH=/run/secrets/play-billing-service-account.json`
   to `.env` on both droplets, then `systemctl restart app.service`
   (`.env`-only change, no rebuild needed).
2. **`newsapp-2` needed a full rebuild**, not just a restart — its running
   container still predated the new code (`{"detail":"Not Found"}` on the
   route), because only `newsapp` had been manually `git pull` + rebuilt
   earlier in this session. `git pull && podman build -t
   localhost/india-news-backend-app:latest . && systemctl restart
   app.service` fixed it.

Verified on both:
```
curl -s -X POST http://127.0.0.1:8080/api/v1/billing/verify-purchase -H "Content-Type: application/json" -d '{"product_id":"premium_monthly","purchase_token":"garbage","product_type":"subs"}'
# {"valid":false}  <- correct: a garbage token is genuinely rejected by Google, not by a config error
```

### 5. Reload + restart on both droplets — NOT DONE YET
```
sudo systemctl daemon-reload
sudo systemctl restart app.service
sudo systemctl show app.service --property=ActiveEnterTimestamp   # confirm it actually restarted
```
Only `app.service` needs this — `contentworker`/`pollworker`/`narrator`
don't touch billing. Do this on **both** droplets.

## What's left

### Upload the new Android build — not started
`app/build/outputs/bundle/release/app-release.aab`, versionCode 11, already
built locally with the client-side verification code. Upload to Play
Console when ready. Not urgent — the client already works fine without this
specific build (fail-open), this just makes the client-side check actually
active for real users instead of always failing open.

### Real-purchase confirmation — not done yet
The garbage-token test above proves the wiring is correct, but a genuine
end-to-end check (real test purchase on the Pixel, license tester account,
current build) hasn't been run since this deploy. Do that next:
1. On the Pixel, do a real test purchase (monthly or lifetime) — should
   succeed and show the Play "test purchase" banner as before.
2. Check backend logs (`journalctl -u app.service -f` on whichever droplet
   the app container round-robins to) for either silence (success, no
   rejection logged) or a `"Rejected purchase verification: ..."` line if
   something's off — that line only fires on `valid=false`.
