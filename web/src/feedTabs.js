// The user's home-strip tabs, as in the Android app's Customize Feed: built-in categories (shown or
// not), publishers pinned as their own tab ("src:<slug>") and free-text topics ("q:<text>").
// Saved per browser: { order: [key], off: [key], user: [{ key, label }] }.
const KEY = "feed_tabs";
const DEFAULT_SHOWN = 6;
export const POPULAR = ["The Hindu", "Indian Express", "NDTV", "Hindustan Times", "Times of India (Top)", "India Today", "News18", "Livemint", "Moneycontrol", "Economic Times", "Business Today"];

export function readTabs() {
  try {
    const p = JSON.parse(localStorage.getItem(KEY));
    if (p && Array.isArray(p.order) && Array.isArray(p.off) && Array.isArray(p.user)) return p;
  } catch (e) {}
  return null;
}
export function writeTabs(p) {
  try { if (p) localStorage.setItem(KEY, JSON.stringify(p)); else localStorage.removeItem(KEY); } catch (e) {}
}
export const isUserKey = (k) => k.indexOf("src:") === 0 || k.indexOf("q:") === 0;
export function stripEmoji(s) { return String(s).replace(/[\u{1F000}-\u{1FFFF}\u{2190}-\u{2BFF}\u{FE0F}\u{200D}]/gu, "").replace(/\s+/g, " ").trim(); }

// The Android app's predefined tabs (DEFAULT_CATEGORIES): key = the API's `category` filter. "all" is the
// front page, always first here. Only DEFAULT_ON start on the strip; the rest are opt-in.
export const DEFAULT_CATEGORIES = [
  ["national", "National"], ["business", "Business & Markets"], ["northeast", "Northeast India"], ["tech", "Technology & AI"],
  ["sports", "Sports"], ["entertainment", "Entertainment"], ["world", "World News"], ["politics", "Politics & Policy"],
  ["health", "Health"], ["education", "Education"], ["crypto", "Crypto"], ["lifestyle", "Lifestyle"],
  ["regional_south", "South India"], ["regional_west", "West India"], ["regional_east", "East India"],
];
const DEFAULT_ON = ["national", "business", "northeast", "tech", "sports"];
const CAT_LABEL = {};
DEFAULT_CATEGORIES.forEach(([k, l]) => { CAT_LABEL[k] = l; });
export const isCategoryKey = (k) => !!CAT_LABEL[k];
// Old links use a category's display name ("Business", "Sports"): map them to the key.
export const categoryKey = (c) => (CAT_LABEL[c] ? c : (DEFAULT_CATEGORIES.filter(([k, l]) => k === String(c).toLowerCase() || l.toLowerCase() === String(c).toLowerCase())[0] || [null])[0]);

// All tab keys in order, which are on, and a label for each.
export function resolveTabs(p) {
  const cats = DEFAULT_CATEGORIES.map((x) => x[0]), user = p ? p.user : [], label = Object.assign({}, CAT_LABEL);
  user.forEach((u) => { label[u.key] = u.label; });
  const known = cats.concat(user.map((u) => u.key));
  const order = (p ? p.order.filter((k) => known.indexOf(k) > -1) : []).concat(known.filter((k) => !p || p.order.indexOf(k) < 0));
  const off = p ? p.off.filter((k) => cats.indexOf(k) > -1) : cats.filter((k) => DEFAULT_ON.indexOf(k) < 0);
  return { order, off, user, label, shown: order.filter((k) => off.indexOf(k) < 0), hidden: order.filter((k) => off.indexOf(k) > -1) };
}
export const tabTo = (k) => k === "All" ? "/" : "/?c=" + encodeURIComponent(k);
