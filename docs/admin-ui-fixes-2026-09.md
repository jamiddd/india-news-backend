# Admin UI fixes (September 2026)

A round of fixes to `app/admin_session.py` — the shared layout/CSS every
`/admin/*` page renders through (`layout()`, `nav()`). Requested as four
issues; the header-logo and theme-toggle items turned into a longer chase
than expected, so the actual root causes are documented here in case
something in this area breaks again.

## 1. Header logo rendered as a plain navy circle

**Symptom:** the "OIN" mark in the top-left showed only its navy background
circle — no cream lettering — while the identical-looking mark on the public
site (`static/home.html`) rendered fine.

**Root cause:** the mark used `clip-path: url(#oin)` to keep the letter
paths inside the circle, on an SVG sized purely via CSS (`width`/`height`
set only in the stylesheet, no `width`/`height` attributes on the `<svg>`
itself — only `viewBox`). Safari has a known bug where that combination
computes the clip region as empty, silently dropping everything inside the
clipped `<g>`. A byte-for-byte diff against the homepage's copy of the same
markup confirmed the SVG itself was identical — the failure was Safari's
clip-path handling, not a typo.

**Fix:** dropped the `clip-path`/`<clipPath>`/`<defs>` entirely. The letter
paths only extend ~1.6% past the circle's edge at their extreme tips (the
"O"'s outer curve, the "N"'s right stroke) — invisible at the 28-32px this
renders at — so removing the clip changes nothing visually and removes the
dependency.

## 2. Dark-mode toggle button showed no sun icon

**Symptom:** clicking the toggle correctly flipped every color on the page
and hid the moon icon, but no sun icon ever appeared — the button rendered
as a blank circle in dark mode.

This took three attempts to actually fix:

- **First attempt:** assumed a CSS specificity bug in the
  `display:none`/`display:block` pairing keyed off `[data-theme=dark]` and
  moved the toggle to JS (`style.display` set directly in a `paint()`
  function). Didn't fix it.
- **Second attempt:** a Safari Private Browsing test session showed the
  fix hadn't landed, traced to a real but separate bug — see §3 below.
  Fixing that didn't resolve this one either, since the user was testing
  in a normal (non-private) tab where it reproduced regardless.
- **Actual cause:** the sun icon (`<circle>` + multi-ray `<path>`, both
  stroked via inherited `currentColor` through `button > svg > path`)
  simply painted nothing in Safari — a second, distinct SVG rendering
  failure in the same header, unrelated to the clip-path bug in §1. The
  moon icon (a single stroked `<path>`) rendered fine; the sun (circle +
  multi-subpath path) did not, despite structurally near-identical markup.
  No further root cause was found — screenshots confirmed the toggle logic,
  color switching, and moon rendering all worked correctly, isolating the
  failure to the sun `<svg>`'s content specifically.

**Fix:** replaced both icons with plain `☾`/`☀` Unicode text glyphs styled
via CSS `color`, instead of inline SVG. Text rendering has none of the
fill/stroke/currentColor-inheritance machinery either SVG bug traced back
to, so this sidesteps the bug class rather than chasing a third cause. The
toggle logic itself (which element gets `display:block`, driven by the
`data-theme` attribute) was already correct and is unchanged — only the
`<svg>` markup became `<span>`.

## 3. Theme attribute not set in Safari Private Browsing

**Found while debugging #2, not the original complaint.** The theme-init
script read `localStorage` and called
`document.documentElement.setAttribute('data-theme', …)` inside the same
`try` block. Safari Private Browsing throws on any `localStorage` access,
so the throw was silently skipping the `setAttribute` call too. The page
still *looked* dark (a separate plain `@media (prefers-color-scheme: dark)`
CSS rule handles that independent of the attribute), but anything keyed
off `data-theme` specifically — like the icon toggle — stayed stuck
believing it was in light mode.

**Fix:** isolated the `localStorage.getItem` call in its own `try`,
separate from `setAttribute`, so a storage-access throw can no longer
prevent the theme attribute from being set. `setAttribute` now always runs.

## 4. Missing favicon

The admin `<head>` had no `<link rel=icon>` at all — browsers showed a
generic icon in the tab instead of the brand mark. Added the same data-URI
favicon `home.html` uses.

## 5. Mobile: full-bleed content + horizontally scrollable tables

`.wrap` had fixed side/top padding and `main` had a border-radius and
border on all sides, so on mobile the content card floated in a visible
grey gutter (`--surface`) on top/left/right instead of using the full
viewport width. Tables in data-heavy admin pages (Polls, Users, etc.) also
overflowed the viewport horizontally with no way to scroll just the table.

**Fix:** added a `max-width:640px` breakpoint — `.wrap` padding drops to 0,
`main` loses its side/bottom border and radius (content now fills the full
width, header's own border-bottom still separates it visually), and
`table{display:block;overflow-x:auto;white-space:nowrap}` so a wide table
scrolls horizontally as its own row instead of blowing out the page. Also
added `initial-scale=1` to the viewport meta tag, which was missing.

## 6. Nav bar restyled as scrollable tabs

`nav.admin-nav` (the Daily review / Polls / Quiz / … links) was a
wrapping list of plain links. Restyled as a `flex-nowrap`, horizontally
scrollable strip with an accent-colored underline on the active page,
scrollbar hidden but still swipeable — reads as a tab bar instead of a
link list, and no longer wraps awkwardly on narrow screens.

## Where

All of the above is in `backend/app/admin_session.py` (`STYLE`, `MARK_SVG`,
`THEME_INIT_SCRIPT`, `THEME_TOGGLE_SCRIPT`, `THEME_TOGGLE_BTN`, `nav()`).
Every `/admin/*` route goes through this file's shared `layout()`, so no
other file needed changes.

Commits (GitHub `origin/main`): `cd7220e`, `d7059f2`, `b45295d`, `fc4d7bb`,
`ddfcee0`, `101e762`.
