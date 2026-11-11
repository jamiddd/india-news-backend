// Deals the feed into the ruled front page's slots (see RuledFront.jsx). Pure, so it is easy to test.
export function planFront(S) {
  const used = new Set();
  const take = (pred = () => true) => {
    const s = S.find((x) => !used.has(x.id) && pred(x));
    if (s) used.add(s.id);
    return s || null;
  };
  const img = (x) => !!x.image;
  const lead = take();
  const topCovered = S.reduce((b, x) => (!b || x.outlet_count > b.outlet_count ? x : b), null);
  const a = take(img) || take();
  const video = take((x) => x.video);   // reserved before the packages can swallow it
  const vfill = video ? null : take(img) || take();   // no video story: an ordinary one keeps the cell from sitting empty
  // A "package": the category with the most stories left, headed by its best story with an image.
  const pkg = (skip) => {
    const n = {};
    S.forEach((x) => { if (!used.has(x.id) && skip.indexOf(x.category) < 0) n[x.category] = (n[x.category] || 0) + 1; });
    const k = Object.keys(n).filter((c) => n[c] >= 2).sort((p, q) => n[q] - n[p])[0];
    if (!k) return null;
    const head = take((x) => x.category === k && img(x)) || take((x) => x.category === k);
    const links = [take((x) => x.category === k), take((x) => x.category === k), take((x) => x.category === k)].filter(Boolean);
    return { name: k, head, links };
  };
  const p1 = pkg([]);
  const b = take(img) || take();
  const p2 = pkg(p1 ? [p1.name] : []);
  const row5 = [take(img) || take(), take(img) || take()].filter(Boolean);
  const rel = (s, n) => (s ? [take((x) => x.category === s.category), take((x) => x.category === s.category)].filter(Boolean).slice(0, n) : []);
  return { lead, a, video, vfill, b, p1, p2, row5, topCovered, relLead: rel(lead, 2), relA: rel(a, 1), relB: rel(b, 1) };
}
