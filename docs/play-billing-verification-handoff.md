# Play Billing server-side verification — handoff

**Status as of 2026-09-17: code done and pushed on both repos. Deployment to
the droplets is NOT done yet.** Purchases currently still work end-to-end
because the client fails open when the backend is unreachable/unconfigured —
no regression, just not yet actually verified.

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

## What's left — all manual, outside code

### 1. Google Cloud service account — DONE (as of this session)
Created in the `indian-news-open` GCP project (same project backing
Firebase), name `play-billing-verifier` (or whatever you named it), JSON key
downloaded to your machine. **This file is a secret — never commit it.**

Also confirm the **Google Play Android Developer API** is enabled on that
project (Cloud Console search bar → enable if not already).

### 2. Play Console permission grant — DONE (as of this session)
Play Console → Users and permissions → invited the service account's email
(`<id>@indian-news-open.iam.gserviceaccount.com`), granted **"View
financial data"** for this app.

Production runs on Podman + systemd quadlets on both droplets (Docker is
fully removed) — see `backend-services-podman` memory and
`docs/podman-migration-plan.md`. The quadlet unit is
`deploy/quadlets/app.container`, already updated (this session) with a
second `Volume=` line for the Play Billing key, alongside the existing
Firebase one:

```
Volume=<FIREBASE_HOST_PATH>:/run/secrets/firebase-service-account.json:ro
Volume=<PLAY_BILLING_HOST_PATH>:/run/secrets/play-billing-service-account.json:ro
```

`<PLAY_BILLING_HOST_PATH>` is a placeholder in the committed file (same
convention as `<FIREBASE_HOST_PATH>`) — the real absolute path only exists
in the installed copy at `/etc/containers/systemd/app.container` on each
droplet, never committed.

### 3. Get the JSON key onto both droplets — NOT DONE YET
```
scp /path/to/downloaded-key.json newsapp:/root/news-backend/secrets/play-billing-service-account.json
scp /path/to/downloaded-key.json newsapp-2:/root/news-backend/secrets/play-billing-service-account.json
```
(Match whatever directory `firebase-service-account.json` already lives in
on each droplet — put this next to it.)

### 4. Update both droplets' installed quadlet + .env — NOT DONE YET
On **each** droplet (`newsapp`, then `newsapp-2`):

```
ssh newsapp   # or newsapp-2
sudo nano /etc/containers/systemd/app.container
```
Replace `<PLAY_BILLING_HOST_PATH>` with the real absolute path to the file
you just scp'd (e.g. `/root/news-backend/secrets/play-billing-service-account.json`).

Then add one line to the backend's `.env` (same file with
`FIREBASE_CREDENTIALS_PATH`) — this is required because the quadlet's
`EnvironmentFile=.env` passes the file through as-is, unlike the old
compose file's per-service hardcoded env block:
```
nano ~/india-news-backend/.env
```
```
GOOGLE_PLAY_SERVICE_ACCOUNT_PATH=/run/secrets/play-billing-service-account.json
```
(`ANDROID_PACKAGE_NAME` defaults to `com.jamid.news` in code — no need to
set it unless that ever changes.)

### 5. Reload + restart on both droplets — NOT DONE YET
```
sudo systemctl daemon-reload
sudo systemctl restart app.service
sudo systemctl show app.service --property=ActiveEnterTimestamp   # confirm it actually restarted
```
Only `app.service` needs this — `contentworker`/`pollworker`/`narrator`
don't touch billing. Do this on **both** droplets.

### 6. Upload the new Android build — separate, already built
`app/build/outputs/bundle/release/app-release.aab`, versionCode 11, already
built locally with the client-side verification code. Upload to Play
Console when ready. Not blocking steps 1-5 — the client works with or
without the backend piece deployed (fail-open).

## How to verify it's actually working once deployed

1. Hit `POST https://<your-domain>/api/v1/billing/verify-purchase` with a
   garbage `purchase_token` — should get back `{"valid": false}`, not a 503
   (503 means the service account/env var isn't wired up right).
2. On the Pixel (license tester account), do a real test purchase — should
   still succeed, and now genuinely goes through the backend check.
3. Check backend logs for `"Rejected purchase verification: ..."` lines —
   absence of these on a real successful purchase, presence only on
   deliberately-bad tokens, confirms it's discriminating correctly.
