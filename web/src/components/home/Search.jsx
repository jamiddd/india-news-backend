import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Ph } from "../Photo.jsx";
import { Link } from "react-router-dom";
import { searchStories } from "../../api.js";

const TABS = ["Stories", "Timelines", "Explainers", "Sources"], HK = "oin-search-history";
function history() { try { return JSON.parse(localStorage.getItem(HK)) || []; } catch (e) { return []; } }
function saveHist(h) { try { localStorage.setItem(HK, JSON.stringify(h.slice(0, 8))); } catch (e) {} }
function has(txt, n) { return txt.toLowerCase().indexOf(n) > -1; }
function Hl({ text, n }) {
  if (!n) return text;
  const out = [], low = text.toLowerCase();
  let i = 0, m;
  while ((m = low.indexOf(n, i)) > -1) { out.push(text.slice(i, m), <b key={m}>{text.slice(m, m + n.length)}</b>); i = m + n.length; }
  out.push(text.slice(i));
  return out;
}
function Snippet({ text, n }) {
  const w = text.split(/\s+/);
  let k = -1;
  for (let i = 0; i < w.length; i++) if (has(w[i], n)) { k = i; break; }
  if (k < 0) return null;
  return <div className="spot-sn">&ldquo;<Hl text={w.slice(Math.max(0, k - 5), k + 6).join(" ")} n={n} />&rdquo;</div>;
}

// Search overlay, modelled on the Android SearchScreen. `state` is "open" | "closing".
export default function Search({ d, S, state, onClose }) {
  const [q, setQ] = useState(""), [tab, setTab] = useState("Stories"), [hist, setHist] = useState(history);
  const inp = useRef(null), res = useRef(null);
  // Stories tab: GET /api/v1/search (debounced). Until it answers, or if it fails, or in
  // sample mode (searchStories resolves null), the loaded feed is filtered locally.
  const [remote, setRemote] = useState(null);
  useEffect(() => {
    const term = q.trim();
    setRemote(null);
    if (term.length < 2) return undefined;
    let on = true;
    const t = setTimeout(() => { searchStories(term).then((r) => { if (on && r) setRemote({ term, list: r }); }, () => {}); }, 250);
    return () => { on = false; clearTimeout(t); };
  }, [q]);
  useEffect(() => { document.body.style.overflow = "hidden"; inp.current && inp.current.focus(); return () => { document.body.style.overflow = ""; }; }, []);
  useEffect(() => {
    const key = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "Enter") { const f = res.current && res.current.querySelector("a[href]:not([href='#'])"); if (f && q.trim()) f.click(); }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [q, onClose]);

  const sources = {};
  S.forEach((s) => s.coverage.forEach((v) => { sources[v.outlet] = (sources[v.outlet] || 0) + 1; }));
  const sourceList = Object.keys(sources).sort().map((n) => ({ name: n, count: sources[n] }));
  const n = q.trim().toLowerCase();
  const R = n ? {
    Stories: remote && remote.term.toLowerCase() === n ? remote.list : S.filter((s) => has(s.headline + " " + s.dek + " " + s.summary.join(" "), n)),
    Timelines: d.timelines.filter((t) => has(t.title + " " + t.points.map((p) => p.text).join(" "), n)),
    Explainers: d.explainers.filter((x) => has(x.title + " " + x.dek, n)),
    Sources: sourceList.filter((s) => has(s.name, n)),
  } : null;

  function remember(id) {
    const s = S.concat(remote ? remote.list : []).filter((x) => x.id == id)[0];
    if (s) { const h = [{ id: s.id, headline: s.headline }].concat(history().filter((x) => x.id !== s.id)); saveHist(h); setHist(history()); }
  }
  let body;
  if (!n) {
    body = hist.length ? (
      <>
        <div className="spot-h">Recent</div>
        {hist.map((x) => (
          <div className="spot-hist" key={x.id}>
            <Link to={"/story/" + x.id} onClick={() => remember(x.id)}><Ph src={x.image} t="" style={{ width: 36, height: 36, padding: 0, borderRadius: 6 }} /><span>{x.headline}</span></Link>
            <button type="button" aria-label={"Remove " + x.headline + " from recent"} onClick={() => { saveHist(history().filter((h) => h.id != x.id)); setHist(history()); }}>&times;</button>
          </div>
        ))}
      </>
    ) : <div className="spot-none">Search stories, timelines, explainers and sources.</div>;
  } else {
    const L = R[tab];
    if (!L.length) body = <div className="spot-none">No {tab.toLowerCase()} match.</div>;
    else body = L.map((x, i) => {
      if (tab === "Stories") return (
        <Link className="spot-row" key={x.id} to={"/story/" + x.id} onClick={() => remember(x.id)}>
          <div><div className="spot-hd"><Hl text={x.headline} n={n} /></div><Snippet text={x.summary.join(" ")} n={n} /></div>
          <Ph src={x.image} style={{ width: 96, alignSelf: "stretch", minHeight: 56 }} />
        </Link>
      );
      if (tab === "Timelines") return <Link className="spot-card" key={x.id} to={"/timeline/" + x.id}><span className="spot-badge">TIMELINE</span><div className="spot-hd"><Hl text={x.title} n={n} /></div><div className="spot-sn">{x.points.length} events &middot; {x.updated}{x.audio ? " · Has audio" : ""}</div></Link>;
      if (tab === "Explainers") return <Link className="spot-card" key={x.id} to={"/explainer/" + x.id}><span className="spot-badge">EXPLAINER</span><div className="spot-hd"><Hl text={x.title} n={n} /></div><div className="spot-sn">{x.read}</div></Link>;
      return <Link className="spot-row src" key={x.name} to="/?c=All" onClick={onClose}><span className="ph round" style={{ width: 28, height: 28, padding: 0 }}></span><div className="spot-hd"><Hl text={x.name} n={n} /></div><div className="spot-sn" style={{ marginLeft: "auto" }}>{x.count} stories</div></Link>;
    });
  }
  return createPortal(
    <div className={"spot" + (state === "closing" ? " closing" : "")} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="spot-col" role="dialog" aria-modal="true" aria-label="Search">
        <div className="spot-top">
          <label className="spot-field"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
            <input className="spot-in" ref={inp} type="text" placeholder="Search stories, timelines, explainers, sources" aria-label="Search" autoComplete="off" value={q} onChange={(e) => setQ(e.target.value)} />
            <button type="button" className="spot-clear" aria-label="Clear search" hidden={!q} onClick={() => { setQ(""); inp.current.focus(); }}>&times;</button></label>
          <button type="button" className="spot-cancel" onClick={onClose}>Cancel</button>
        </div>
        <div className="spot-tabs" role="tablist">
          {TABS.map((t) => { const c = R && t !== "Stories" ? R[t].length : 0; return <button key={t} type="button" role="tab" aria-selected={t === tab} className={"spot-tab" + (t === tab ? " on" : "")} onClick={() => setTab(t)}>{t}{c ? <i>{c}</i> : null}</button>; })}
        </div>
        <div className="spot-res" ref={res}>{body}</div>
      </div>
    </div>, document.body);
}
