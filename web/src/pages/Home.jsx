import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router-dom";
import { Ph, PopHost, SRow, Src, Tip, getFollows, openLogin, signedIn, slug } from "../components/home/shared.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadHome as fetchHome } from "../api.js";
import Search from "../components/home/Search.jsx";
import RuledFront, { RuledFeed, RuledSection, WatchStrip } from "../components/home/RuledFront.jsx";

// Data access for this page (src/api.js). Mapping for the real feed: left column = latest,
// middle = the one most-covered story, right = top / most covered; loadHome orders the
// stories so S[0] is the most covered in the active category and the rest run newest first.
export const loadHome = (category) => fetchHome({ category });

function ago(m) { return m < 60 ? m + "m" : Math.round(m / 60) + "h"; }
function Meta({ s }) { return <div className="meta"><span className="cat">{s.category}</span><span>{ago(s.minutes_ago)} ago</span></div>; }
function SLink({ s }) { return <Link to={"/story/" + s.id}>{s.headline}</Link>; }
function Img({ s }) { return <div className="side-card"><Ph r="3/2" src={s.image} /><Meta s={s} /><h3><SLink s={s} /></h3><SRow s={s} /></div>; }
function Thumb({ s }) { return <div className="side-card th hv"><div><Meta s={s} /><h3><SLink s={s} /></h3></div><Ph r="1/1" src={s.image} /><Src s={s} /><Tip id={s.id} /></div>; }

function CatSection({ k, ALL, at }) {
  return <RuledSection title={k} to={"/?c=" + encodeURIComponent(k)} L={ALL.filter((s) => s.category === k).slice(0, 8)} at={at} />;
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
    // Starts at left:0 (see the style below) so it can never widen the page, then slides to sit
    // under the button, kept 12px inside the visible width.
    const vw = document.documentElement.clientWidth;
    pop.current.style.left = Math.max(12, Math.min(menu.left, vw - pop.current.offsetWidth - 12)) + "px";
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
        <div className="cat-pop" role="menu" ref={pop} style={{ top: menu.top, left: 0 }}>
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

  const head = c === "All" ? null : (
    <>
      <div className="feed-h"><h1>{c}</h1></div>
      {forYouNote ? <p className="feed-note">{forYouNote}</p> : null}
    </>
  );
  if (phone) return wrap(<>{head}<PhoneFeed S={S} /></>);

  // A category page is just the bottomless feed's blocks for that category: no front-page grid, no rail.
  if (c !== "All") return wrap(<>{head}<RuledFeed S={S} /></>);
  const TLS = d.timelines.slice(0, 3);
  const EXS = d.explainers.slice(0, 6);
  const sections = d.categories.slice(1).flatMap((k, i) => (i === 1 ? [<CatSection key={k} k={k} ALL={ALL} at={i} />, <WatchStrip key="watch" />] : [<CatSection key={k} k={k} ALL={ALL} at={i} />]));
  return wrap(
    <>
      <RuledFront S={S} TLS={TLS} EXS={EXS} forYou="/?c=For%20you" />
      {sections}
      <EndlessAll d={d} ALL={ALL} />
    </>
  );
}
