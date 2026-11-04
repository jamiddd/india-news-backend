// Which picture should lead? Publishers' thumbnails (a Guardian image delivered at width=140) look
// blurry stretched over a hero, while the same story usually also carries a 1200-1900px one.
// Without downloading anything, many image URLs state their size (width=140, width-1280,height-720,
// 1600x900, resize=1920,1440). Where they do, that matched the real file every time in a check of
// the cover story (22 of 22), so it is used to order images best-first. URLs that say nothing keep
// their place between known-good and known-poor. (The proper fix, measuring each image at ingest,
// is scripts/image_quality.py.)

// Width a URL says the image is, or null.
export function hintedWidth(url) {
  let u = String(url || "");
  try { u = decodeURIComponent(u); } catch (e) { /* keep as is */ }
  let m;
  if ((m = /[?&,/]-?(?:width|w)[=-](\d{2,4})(?!\d)/i.exec(u))) return +m[1];            // ?width=140  width-1280  &w=640
  if ((m = /resize=(\d{2,4})[,x]/i.exec(u))) return +m[1];                                // ?resize=1920,1440
  if ((m = /(?<!\d)(\d{3,4})x(\d{3,4})(?!\d)/.exec(u))) return +m[1];                    // /1600x900/  -400x225
  if ((m = /\/standard\/(\d{2,4})\//.exec(u)) || (m = /\/ws\/(\d{2,4})\//.exec(u))) return +m[1];   // BBC ichef
  if ((m = /newbucket\/(\d{2,4})_/.exec(u))) return +m[1];                                // India TV resize
  return null;
}

export const HIGH = 1000, MEDIUM = 600;   // CSS px the site can show sharply: a 760px hero, a card

// 3 known high, 2 unknown, 1 known medium, 0 known low.
export function tier(url) {
  const w = hintedWidth(url);
  if (w == null) return 2;
  return w >= HIGH ? 3 : w >= MEDIUM ? 1 : 0;
}

// Best first, keeping the original order within a tier. items: anything; urlOf: how to read its URL.
export function bestFirst(items, urlOf = (x) => x) {
  return items.map((x, i) => ({ x, i, t: tier(urlOf(x)) })).sort((a, b) => b.t - a.t || a.i - b.i).map((o) => o.x);
}
