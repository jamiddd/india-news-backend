# Website roadmap

Future work on the public site (`app/static/`, served by the routes in
`app/main.py`). Captured 5 September 2026, immediately after the landing-page
rebuild went live. **None of this is scheduled** — this is a parking place so
the ideas are not lost, not a plan with dates.

Current state for reference: five static pages (`home`, `privacy`, `terms`,
`refunds`, `contact`) sharing `site.css` / `site.js`, with device screenshots
under `/static/img/` derived by `scripts/prep_shots.py` in the Android repo.
The whole site is static HTML — no templating engine, no build step, no
client-side framework.

---

## 1. App link and QR code

Blocked on the Play Store release. Once the listing is live:

- Replace the "Coming to Google Play" line in the hero with a real badge and
  link.
- A QR code beside it, so someone reading on a laptop can install without
  typing. Generate it once as an inline SVG and commit it — do not pull in a
  JS QR library or a third-party image endpoint for something that never
  changes.
- The "Availability" copy in the About section needs updating at the same
  time; it currently says the app is preparing for release.

## 2. Donation UI on the website

The backend already has most of this: `create_payment_link()` in
`app/services/donations.py` creates Razorpay payment links, and the webhook
that records a captured payment is live. A web donation flow would mostly be
a page that calls it and redirects.

Prerequisites, from the donations launch checklist:

- KYC completed, live keys and a live webhook configured.
- `pay_test_` rows cleared out.

Design constraints that must carry over from the app, because they are
compliance-relevant and not merely stylistic: a donation unlocks nothing, and
the page has to say so plainly, alongside a link to the refund policy. The
existing wording on `/` and `/refunds` was written for Razorpay's reviewer —
reuse it rather than paraphrasing.

Open question: whether accepting donations from the website (as opposed to
inside the app) changes anything about the Play Store's payments policy. Worth
checking before building, since the answer might be "keep it app-only".

## 3. More pages

Careers, roadmap, about me, investors, feature request, feedback.

These are cheap individually — each is one static file that picks up
`site.css` and the shared masthead and footer, the same way the legal pages
now do. Two things to decide before adding several at once:

- **Navigation.** The masthead currently holds three links and fits. Six or
  eight needs either a dropdown or a footer sitemap; the footer is the
  simpler answer and does not add JavaScript.
- **Feature request and feedback need a backend.** Everything else is prose.
  A form means an endpoint, spam handling, and somewhere for submissions to
  land. A `mailto:` link is the zero-infrastructure version and is probably
  right until volume justifies otherwise.

"About me" is worth writing properly rather than as a stub — the independent,
single-developer story is a genuine differentiator against the aggregators
this competes with, and the About section on `/` only gestures at it.

## 4. Dynamic hero: real clusters, swipeable

Replace the hard-coded hero story with the current top clusters, so a visitor
can arrow left/right through a few real stories and see actual framing
output. This is the most valuable item here and also the largest.

What it needs:

- A public, unauthenticated endpoint shaped for this — probably a small
  dedicated one rather than reusing `/api/v1/clusters`, so the payload is
  three or four stories with just headline, image, outlet count and framing
  rows, instead of the full feed response.
- **Rate limiting.** The default is 100 requests/minute per IP and the
  landing page is the most-hit route on the domain. The endpoint wants its
  own generous limit and a server-side cache, in the same spirit as the
  existing five-minute cache on the hot list endpoints.
- **Cache-busting.** `site.js` is served with a one-year immutable
  `Cache-Control`, so the `?v=` on every page's `<script>` tag must be bumped
  when the carousel starts fetching. This is the failure mode most likely to
  be forgotten.
- **A static fallback.** If the fetch fails the hero must still show
  something — keep the current hard-coded story as the initial render and
  replace it on success, rather than starting empty.
- **Editorial risk.** Whatever is top of the feed goes on the front page
  unreviewed. Today's hero was chosen by hand partly to avoid putting an
  identifiable, unconvicted individual in a criminal story on a marketing
  page — a live feed cannot make that judgement. Consider restricting the
  hero to business, science and sport categories, or gating on something
  similar.

## 5. Testimonials

Explicitly last, and correctly so: there is nothing to quote yet. Worth
revisiting once there are real users and real reviews on the Play listing —
real quotes with attribution, not invented ones.
