import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router-dom";
import { Fav } from "../components/Photo.jsx";
import { AudioPlayer, Carousel, Ph, PopHost, SRow, Src, Tip, getFollows, openLogin, signedIn, slug } from "../components/home/shared.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadHome as fetchHome } from "../api.js";
import Search from "../components/home/Search.jsx";

// Data access for this page (src/api.js). Mapping for the real feed: left column = latest,
// middle = the one most-covered story, right = top / most covered; loadHome orders the
// stories so S[0] is the most covered in the active category and the rest run newest first.
export const loadHome = (category) => fetchHome({ category });

function ago(m) { return m < 60 ? m + "m" : Math.round(m / 60) + "h"; }
function Meta({ s }) { return <div className="meta"><span className="cat">{s.category}</span><span>{ago(s.minutes_ago)} ago</span></div>; }
function SLink({ s }) { return <Link to={"/story/" + s.id}>{s.headline}</Link>; }
function Card({ s }) { return <div className="c"><Ph r="3/2" src={s.image} /><Meta s={s} /><h3><SLink s={s} /></h3><SRow s={s} /></div>; }
function SmallHead({ s }) { return <Link to={"/story/" + s.id}><small>{s.category.toUpperCase()} &middot; {ago(s.minutes_ago)} ago</small>{s.headline}</Link>; }

function Img({ s }) { return <div className="side-card"><Ph r="3/2" src={s.image} /><Meta s={s} /><h3><SLink s={s} /></h3><SRow s={s} /></div>; }
function Txt({ s }) { return <div className="side-card txt hv"><Meta s={s} /><h3><SLink s={s} /></h3><p>{s.summary[0]}</p><Src s={s} /><Tip id={s.id} /></div>; }
function Thumb({ s }) { return <div className="side-card th hv"><div><Meta s={s} /><h3><SLink s={s} /></h3></div><Ph r="1/1" src={s.image} /><Src s={s} /><Tip id={s.id} /></div>; }
const O = (F, s) => (s ? <F s={s} /> : null);

// Lower-page compositions.
function G({ s, type, grow, r }) {
  const cls = (grow ? "grow " : "") + "g";
  if (type === "big") return <article className={cls + " g-big"}><Ph r="4/3" src={s.image} /><Meta s={s} /><h3><SLink s={s} /></h3><p>{s.dek}</p><SRow s={s} /></article>;
  if (type === "note") return <article className={cls + " g-txt hv"}><Meta s={s} /><h3><SLink s={s} /></h3><p>{s.summary[0]}</p><Src s={s} /><Tip id={s.id} /></article>;
  return <article className={cls}><Ph r={type === "sq" ? "1/1" : r || "3/2"} src={s.image} /><Meta s={s} /><h3><SLink s={s} /></h3><SRow s={s} /></article>;
}
// Spread stories over n columns, each with at least one image card; the last image card stretches.
function Columns({ L, types, n }) {
  const cols = [];
  for (let i = 0; i < n; i++) cols.push([]);
  L.forEach((s, i) => cols[i % n].push({ t: types[i], s }));
  return cols.filter((c) => c.length).map((c, ci) => {
    if (c.every((x) => x.t === "note")) c[0].t = "med";
    let last = -1;
    c.forEach((x, i) => { if (x.t !== "note") last = i; });
    return <div className="scol" key={ci}>{c.map((x, i) => <G key={x.s.id} s={x.s} type={x.t} grow={i === last} />)}</div>;
  });
}
function ListCol({ ALL, k, shown }) {
  const ids = {};
  shown.forEach((s) => { ids[s.id] = 1; });
  const pool = ALL.filter((s) => !ids[s.id] && s.category === k).concat(ALL.filter((s) => !ids[s.id] && s.category !== k)).slice(0, 5);
  return (
    <div className="scol list"><h4 className="lh">More headlines</h4>
      {pool.map((s) => <div className="hv" key={s.id}><Link to={"/story/" + s.id}><small>{s.category} &middot; {ago(s.minutes_ago)} ago</small>{s.headline}</Link><Tip id={s.id} /></div>)}
    </div>
  );
}
function Featured({ L, types, rev, k, ALL }) {
  return <div className={"scat" + (rev ? " rev" : "")}><div className="scol main"><G s={L[0]} type="big" grow /></div><Columns L={L.slice(1)} types={types} n={2} /><ListCol ALL={ALL} k={k} shown={L} /></div>;
}
const layouts = [
  (p) => <Featured {...p} types={["med", "sq", "note", "note"]} rev={false} />,
  (p) => <Featured {...p} types={["note", "sq", "med", "note"]} rev />,
  (p) => <div className="scat"><Columns L={p.L} types={["med", "note", "note", "sq", "med"]} n={3} /><ListCol ALL={p.ALL} k={p.k} shown={p.L} /></div>,
];
function CatSection({ k, ALL, at }) {
  const L = ALL.filter((s) => s.category === k).slice(0, 5);
  if (!L.length) return null;
  const Lay = layouts[at % 3];
  return <div className="sec"><h2 className="sec-h"><span>{k}</span><Link to={"/?c=" + encodeURIComponent(k)}>See all &rarr;</Link></h2><Lay L={L} k={k} ALL={ALL} /></div>;
}

function Matrix({ rows }) {
  return <div className="matrix">{rows.map((f, i) => <div className="mrow" key={i}><b><Fav name={f.outlet} /><span><Link className="so" to={"/source/" + slug(f.outlet)}>{f.outlet}</Link></span></b><span>{f.angle}</span><span className="t">{f.tag}</span></div>)}</div>;
}

function Band({ TLS, EXS }) {
  return (
    <div className="band sec" id="timelines">
      <div><h2 className="sec-h"><span>Timelines</span><Link to="/timelines">See all &rarr;</Link></h2>
        {TLS.map((t, i) => i ? (
          <div className="tl tl-plain" key={t.id}><div><div className="meta"><span>{t.updated}</span><span>{t.points.length} events</span></div><h3><Link to={"/timeline/" + t.id}>{t.title}</Link></h3><p>{t.dek || ""}</p></div></div>
        ) : (
          <div className="tl tl-top" key={t.id}><Ph r="3/2" src={t.image} /><h3 style={{ marginTop: 12 }}><Link to={"/timeline/" + t.id}>{t.title}</Link></h3><div className="meta"><span>{t.updated}</span></div>
            {t.audio ? <AudioPlayer id={t.id} kind="timeline" title={t.title} url={"/timeline/" + t.id} label={t.audio.label} duration={t.audio.duration} /> : null}
            <ol>{t.points.map((p, j) => <li key={j}><b>{p.date}</b><span>{p.text}</span></li>)}</ol></div>
        ))}
      </div>
      <div><h2 className="sec-h"><span>Explainers</span><Link to="/explainers">See all &rarr;</Link></h2>
        <div className="exs">{EXS.map((x, i) => (
          // The first explainer is the featured one: an excerpt and three key points.
          <div className="ex" key={x.id}><Ph r="3/2" src={x.image} /><div className="meta"><span className="cat">{x.category}</span><span>{x.read}</span></div><h3><Link to={"/explainer/" + x.id}>{x.title}</Link></h3><p>{x.dek}</p>
            {i ? null : <><p className="ex-body">{x.excerpt || ""}</p><div className="ex-pts"><b>In brief</b><ul>{(x.points || []).map((t, j) => <li key={j}>{t}</li>)}</ul></div><Link className="ex-more" to={"/explainer/" + x.id}>Read the explainer &rarr;</Link></>}
          </div>
        ))}</div>
      </div>
    </div>
  );
}

function toTop() { scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); }

// Phone: the feed only, as in the app. A hero image card then two thumbnail rows, repeating,
// twelve at a time, more as you near the bottom.
function PhoneFeed({ S }) {
  const PAGE = 12, [shown, setShown] = useState(PAGE);
  useEffect(() => {
    const check = () => { if (shown < S.length && document.documentElement.scrollHeight - scrollY - innerHeight < 900) return setTimeout(() => setShown((n) => Math.min(S.length, n + PAGE)), 250); };
    let t = check();
    const on = () => { clearTimeout(t); t = check(); };
    addEventListener("scroll", on, { passive: true }); addEventListener("resize", on);
    return () => { clearTimeout(t); removeEventListener("scroll", on); removeEventListener("resize", on); };
  }, [shown, S.length]);
  return (
    <>
      <div className="pfeed" id="pfeed">{S.slice(0, shown).map((x, i) => (i % 3 === 0 ? <Img key={x.id} s={x} /> : <Thumb key={x.id} s={x} />))}</div>
      <div className="endless" id="pend" aria-live="polite">
        {shown < S.length ? <div className="endless-load"><i className="spin"></i><span>Loading more{"…"}</span></div>
          : <div className="endless-end"><b>You{"’"}re all caught up</b><span>That was the whole feed, {S.length} stories.</span><button type="button" className="more-btn" onClick={toTop}>Back to top</button></div>}
      </div>
    </>
  );
}

// The All feed never really ends: near the bottom the next category's section is added.
function EndlessAll({ d, ALL, children }) {
  const more = d.moreCategories || [];
  const [loaded, setLoaded] = useState(0), [busy, setBusy] = useState(false);
  useEffect(() => {
    if (busy || loaded >= more.length) return;
    let t;
    const check = () => {
      if (document.documentElement.scrollHeight - scrollY - innerHeight < 900) {
        setBusy(true);
        t = setTimeout(() => { setLoaded((n) => n + 1); setBusy(false); }, 450);   // a beat of "loading", as a real request would take
      }
    };
    check();
    addEventListener("scroll", check, { passive: true }); addEventListener("resize", check);
    return () => { clearTimeout(t); removeEventListener("scroll", check); removeEventListener("resize", check); };
  }, [busy, loaded, more.length]);
  const base = d.categories.length - 1;
  return (
    <>
      {more.slice(0, loaded).map((k, i) => <CatSection key={k} k={k} ALL={ALL} at={base + i} />)}
      <div className="endless" aria-live="polite">
        {loaded < more.length ? <div className="endless-load"><i className="spin"></i><span>Loading {more[loaded]}{"…"}</span></div>
          : <div className="endless-end"><b>You{"’"}re all caught up</b><span>That was every category, {d.categories.length - 1 + more.length} in all.</span><button type="button" className="more-btn" onClick={toTop}>Back to top</button></div>}
      </div>
    </>
  );
}

function CatBar({ d, c, onSearch }) {
  const tabs = d.categories.slice();
  if (signedIn()) tabs.splice(1, 0, "For you");
  const more = d.moreCategories || [], moreOn = more.indexOf(c) > -1;
  const [menu, setMenu] = useState(null), btn = useRef(null), pop = useRef(null);
  useLayoutEffect(() => {
    if (!menu || !pop.current) return;
    pop.current.style.left = Math.max(12, Math.min(menu.left, innerWidth - pop.current.offsetWidth - 12)) + "px";
  }, [menu]);
  useEffect(() => {
    if (!menu) return;
    const click = (e) => { if (pop.current && !pop.current.contains(e.target)) setMenu(null); };
    const key = (e) => { if (e.key === "Escape") { setMenu(null); btn.current && btn.current.focus(); } };
    document.addEventListener("click", click); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("click", click); document.removeEventListener("keydown", key); };
  }, [menu]);
  function toggle(e) {
    e.stopPropagation();
    if (menu) { setMenu(null); return; }
    const r = btn.current.getBoundingClientRect();
    setMenu({ top: r.bottom + scrollY + 6, left: r.left + scrollX });
  }
  return (
    <nav className="catbar" aria-label="Categories">
      <div className="wide" id="cats" style={{ display: "flex", alignItems: "center", gap: 26, paddingTop: 0, paddingBottom: 0, overflowX: "auto", scrollbarWidth: "none" }}>
        {tabs.map((k) => <Link key={k} className={"chip" + (k === c ? " on" : "")} to={k === "All" ? "/" : "/?c=" + encodeURIComponent(k)}>{k}</Link>)}
        {more.length ? <span className="more-wrap"><button type="button" ref={btn} className={"chip" + (moreOn ? " on" : "")} id="more-open" aria-haspopup="true" aria-expanded={!!menu} onClick={toggle}>{moreOn ? c : "More"} &#9662;</button></span> : null}
        <button type="button" className="search-btn tune-btn" id="tune-open" aria-label="Settings" title="Settings"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></svg></button>
        <button type="button" className="search-btn" id="search-open" aria-label="Search" title="Search (Ctrl/Cmd+K)" onClick={onSearch}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg></button>
      </div>
      {menu ? createPortal(
        <div className="cat-pop" role="menu" ref={pop} style={{ top: menu.top, left: menu.left }}>
          {more.map((k) => <Link key={k} role="menuitem" className={k === c ? "on" : ""} to={"/?c=" + encodeURIComponent(k)} onClick={() => setMenu(null)}>{k}</Link>)}
        </div>, document.body) : null}
    </nav>
  );
}

export default function Home() {
  const [qs] = useSearchParams();
  const c = qs.get("c") || "All";
  const q = useData(() => loadHome(c), [c], { keep: true, cacheKey: "home:" + c }), d = q.data;
  const [phone, setPhone] = useState(() => matchMedia("(max-width:700px)").matches);
  const [search, setSearch] = useState(null);   // null | "open" | "closing"
  useEffect(() => {
    const mq = matchMedia("(max-width:700px)"), f = () => setPhone(mq.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, []);
  useEffect(() => { document.title = "Top stories — Open Indian News"; }, []);

  const openS = useCallback(() => setSearch("open"), []);
  const closeS = useCallback(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setSearch(null); return; }
    setSearch((s) => (s === "open" ? "closing" : s));
  }, []);
  useEffect(() => { if (search === "closing") { const t = setTimeout(() => setSearch(null), 420); return () => clearTimeout(t); } }, [search]);
  useEffect(() => { if (qs.get("s") === "1") openS(); }, []);   // arrives from the search icon on another page (mobile app bar)
  useEffect(() => {
    const key = (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearch((s) => (s === "open" ? "closing" : "open")); } };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  const wait = statusView(q, "top stories", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }} id="page">{children}</main>);
  if (wait) return wait;
  const ALL = d.stories;
  let S = ALL, forYouNote = "", gate = false;
  if (c === "For you") {
    if (!signedIn()) gate = true;
    else {
      const fl = getFollows();
      const mine = ALL.filter((x) => fl.some((f) => (f.indexOf("topic:") === 0 ? (x.topics || []).some((t) => slug(t.label) === f.slice(6)) : x.coverage.some((v) => slug(v.outlet) === f.slice(7)))));
      if (mine.length) S = mine;
      else {
        S = ALL.slice().sort((a, b) => b.outlet_count - a.outlet_count).slice(0, 30).sort((a, b) => a.minutes_ago - b.minutes_ago);
        forYouNote = "You aren’t following anything yet, so these are the top stories. Follow publishers and topics to shape this feed.";
      }
    }
  } else if (c !== "All") S = ALL.filter((x) => x.category === c);

  const bar = <CatBar d={d} c={c} onSearch={openS} />;
  const overlay = search ? <Search d={d} S={S} state={search} onClose={closeS} /> : null;
  const wrap = (inner) => <>{bar}<main className="wide" style={{ paddingBottom: 72 }} id="page">{inner}</main>{overlay}<PopHost /></>;

  if (gate) return wrap(<><div className="feed-h"><h1>For you</h1></div><div className="gate"><p>Sign in to get a feed built around the publishers and topics you follow.</p><button type="button" className="ab filled" id="gate-in" onClick={openLogin}><span>Sign in</span></button></div></>);
  if (!S.length) return wrap(<p className="empty" style={{ paddingTop: 48 }}>No stories in this feed yet. <Link to="/">Back to top stories</Link></p>);

  const lead = S[0];
  const byCov = S.slice().sort((a, b) => b.outlet_count - a.outlet_count);
  const head = c === "All" ? null : (
    <>
      <div className="feed-h"><h1>{c}</h1><p>{S.length} stories &middot; updated {ago(S[0].minutes_ago)} ago</p></div>
      {forYouNote ? <p className="feed-note">{forYouNote}</p> : null}
    </>
  );
  if (phone) return wrap(<>{head}<PhoneFeed S={S} /></>);

  const mid = (
    <div className="hero-s"><Carousel s={lead} /><Meta s={lead} /><h1><SLink s={lead} /></h1><p className="dek">{lead.dek}</p><SRow s={lead} />
      <ul className="bullets">{lead.summary.map((b, i) => <li key={i}>{b}</li>)}</ul>
      <h2 className="sec-h" style={{ margin: "24px 0 12px", borderTop: 0, paddingTop: 0 }}><span>Framing, at a glance</span></h2>
      <Matrix rows={lead.framing.slice(0, 5)} />
    </div>
  );
  const rank = (
    <div className="side-card"><h2 className="sec-h"><span>Most covered</span></h2>
      <ol className="rank">{byCov.slice(0, 6).map((s, i) => <li className="hv" key={s.id}><span>{i + 1}</span><SmallHead s={s} /><Tip id={s.id} /></li>)}</ol></div>
  );
  const latest = (
    <div className="latest"><h2 className="sec-h"><span>Latest</span></h2>
      {S.slice(1, 7).map((s) => <div className="hv" key={s.id}><SmallHead s={s} /><Tip id={s.id} /></div>)}</div>
  );
  // Only the top timeline is shown in full; the rest are headline-only.
  const TLS = (c === "All" ? d.timelines : d.timelines.filter((t) => t.category === c)).slice(0, 3);
  const EXS = (c === "All" ? d.explainers : d.explainers.filter((x) => x.category === c)).slice(0, 6);
  let sections;
  if (c === "All") {
    sections = d.categories.slice(1).map((k, i) => <CatSection key={k} k={k} ALL={ALL} at={i} />);
  } else {
    // A category feed: whatever the front page above did not use.
    const wide = S.slice(13).slice(0, 5);
    sections = wide.length ? (
      <div className="sec"><h2 className="sec-h"><span>{c === "For you" ? "More for you" : "More in " + c}</span></h2>
        {wide.length >= 5 ? layouts[1]({ L: wide, k: c, ALL }) : <div className="cards flat">{wide.map((s) => <Card key={s.id} s={s} />)}</div>}</div>
    ) : null;
  }
  return wrap(
    <>
      {head}
      <div className="front">
        <div className="colA">{latest}{O(Img, S[7])}{O(Thumb, S[8])}{O(Txt, S[13])}</div>
        <div className="colB">{mid}</div>
        <div className="colC">{O(Thumb, S[9])}{O(Img, S[10])}{rank}{O(Txt, S[11])}{O(Thumb, S[12])}</div>
      </div>
      {TLS.length && EXS.length >= 4 ? <Band TLS={TLS} EXS={EXS} /> : null}
      {sections}
      {c === "All" ? <EndlessAll d={d} ALL={ALL} /> : null}
    </>
  );
}
