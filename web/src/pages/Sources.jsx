import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FollowBtn, slug } from "../components/home/shared.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadSourcesPage } from "../api.js";

export function outletHash(name) { let h = 0; name.split("").forEach((c) => { h = (h * 31 + c.charCodeAt(0)) >>> 0; }); return h; }
export const REGIONS = ["National", "Northeast", "Business", "Regional"];
export const LANGS = ["EN", "EN", "HI"];

// Data access for this page: loadSourcesPage (src/api.js). With the real API, d.sources is the
// publisher list from GET /sources (real region and language) and the story count is how many
// stories in the current top feed each one covers; the sample has no d.sources, so it falls
// back to the outlets seen in the stories.
function outletList(d) {
  if (d.sources) {
    const n = {};
    (d.stories || []).forEach((s) => s.coverage.forEach((v) => { const k = slug(v.outlet); n[k] = (n[k] || 0) + 1; }));
    return d.sources.map((o) => ({ ...o, n: n[o.key] || 0, region: o.region || "—", lang: o.lang || "—" }))
      .sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
  }
  const outlets = {};
  d.stories.forEach((s) => s.coverage.forEach((v) => {
    const k = slug(v.outlet);
    (outlets[k] = outlets[k] || { name: v.outlet, n: 0 }).n++;
  }));
  return Object.keys(outlets).map((k) => {
    const o = outlets[k], h = outletHash(o.name);
    return { key: k, name: o.name, n: o.n, region: REGIONS[h % 4], lang: LANGS[h % 3] };
  }).sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
}

export default function Sources() {
  const ld = useData(loadSourcesPage), d = ld.data;
  const [q, setQ] = useState("");
  const all = useMemo(() => (d ? outletList(d) : []), [d]);
  const wait = statusView(ld, "publishers", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  const n = q.trim().toLowerCase();
  const l = all.filter((o) => !n || o.name.toLowerCase().indexOf(n) > -1);
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <p className="meta crumb" style={{ paddingTop: 28 }}><Link to="/">&larr; Top stories</Link></p>
      <div className="feed-h"><h1>Sources</h1><p>{all.length} publishers we read</p></div>
      <div className="pg-tools"><input id="sq" type="search" placeholder="Find a source" aria-label="Find a source" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="sg" id="sg">
        {l.length ? l.map((o) => (
          <div className="stile" key={o.key}>
            <Link className="stile-l" to={"/source/" + o.key}><span className="fv big sm"></span><span><b>{o.name}</b><small>{o.region} &middot; {o.lang} &middot; {o.n} stories</small></span></Link>
            <FollowBtn k={"source:" + o.key} />
          </div>
        )) : <p className="empty">No sources match.</p>}
      </div>
    </main>
  );
}
