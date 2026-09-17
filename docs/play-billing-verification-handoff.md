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
- **Backend repo**, commits `23c692b` (endpoint) and `0fdd032` (compose
  wiring): `POST /api/v1/billing/verify-purchase` in `app/main.py`, logic in
  `app/services/play_billing.py`, schema in `app/schemas.py`, config in
  `app/config.py`.

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

### 3. Get the JSON key onto both droplets — NOT DONE YET
Per your infra setup, the app runs on **newsapp** and **newsapp-2** behind a
DO load balancer, and you deploy manually via SSH (not via a script). Raw
commands (run these yourself):

```
# From your local machine, copy the key to each droplet:
scp /path/to/downloaded-key.json root@newsapp:/root/news-backend/secrets/play-billing-service-account.json
scp /path/to/downloaded-key.json root@newsapp-2:/root/news-backend/secrets/play-billing-service-account.json
```

(Adjust `/root/news-backend/secrets/` if your actual secrets directory on
the droplets is named differently — check where `firebase-service-account.json`
already lives on each droplet and put this next to it.)

### 4. Set the env var on both droplets — NOT DONE YET
On each droplet, edit the backend's `.env` file (same one that has
`FIREBASE_CREDENTIALS_HOST_PATH`) and add:

```
GOOGLE_PLAY_SERVICE_ACCOUNT_HOST_PATH=/root/news-backend/secrets/play-billing-service-account.json
```

(`ANDROID_PACKAGE_NAME` defaults to `com.jamid.news` already — no need to
set it unless that ever changes.)

### 5. Deploy backend to both droplets — NOT DONE YET
Pull the new backend code and restart the `app` container (Podman, per your
current setup — see `backend-services-podman` memory for exact commands;
Docker was fully removed 2026-09-16). Only the `app` service needs the new
volume/env — `contentworker`/`pollworker`/`narrator` don't touch billing.

Do this on **both** newsapp and newsapp-2 — per your infra memory, deploys
must always hit both droplets.

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
