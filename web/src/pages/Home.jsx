import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Ph, PopHost, SRow, Src, Tip, getFollows, openLogin, signedIn, slug } from "../components/home/shared.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadHome as fetchHome, loadList } from "../api.js";
import { categoryKey, readTabs, resolveTabs, tabTo } from "../feedTabs.js";
import TuneTabs from "../components/home/TuneTabs.jsx";
import Search from "../components/home/Search.jsx";
import RuledFront, { RuledFeed, RuledSection, WatchStrip } from "../components/home/RuledFront.jsx";

// Data access for this page (src/api.js). Mapping for the real feed: left column = latest,
// middle = the one most-covered story, right = top / most covered; loadHome orders the
// stories so S[0] is the most covered in the active category and the rest run newest first.
export const loadHome = (category) => fetchHome({ category });
// A pinned publisher ("src:<slug>") or topic ("q:<text>") tab: the front-page data for the strip, plus that tab's own stories.
const loadTab = async (c) => {
  const list = c.indexOf("src:") === 0 ? loadList("source", c.slice(4)) : c.indexOf("q:") === 0 ? loadList("topic", c.slice(2)) : loadList("category", c);
  const [h, l] = await Promise.all([fetchHome({ category: "All" }), list]);
  return { ...h, tabStories: l.stories, tabMissing: c.indexOf("src:") === 0 && !l.source };
};

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
  const [prefs, setPrefs] = useState(readTabs), [tuning, setTuning] = useState(false);
  const view = resolveTabs(prefs);
  const tabs = ["All"].concat(view.shown);
  if (signedIn()) tabs.splice(1, 0, "For you");
  if (tabs.indexOf(c) < 0) tabs.push(c);   // a link to a tab you've removed still shows where you are
  const label = (k) => view.label[k] || k.replace(/^\w+:/, "");
  const close = useCallback(() => setTuning(false), []);
  return (
    <nav className="catbar" aria-label="Categories">
      <div className="wide" id="cats" style={{ display: "flex", alignItems: "center", gap: 26, paddingTop: 0, paddingBottom: 0, overflowX: "auto", scrollbarWidth: "none" }}>
        {tabs.map((k) => <Link key={k} className={"chip" + (k === c ? " on" : "")} to={tabTo(k)}>{label(k)}</Link>)}
        <button type="button" className="search-btn tune-btn" id="tune-open" aria-label="Customize feed" title="Customize feed" aria-haspopup="dialog" aria-expanded={tuning} onClick={() => setTuning(true)}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></svg></button>
        <button type="button" className="search-btn" id="search-open" aria-label="Search" title="Search (Ctrl/Cmd+K)" onClick={onSearch}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg></button>
      </div>
      {tuning ? <TuneTabs view={view} onChange={setPrefs} onClose={close} /> : null}
    </nav>
  );
}

export default function Home() {
  const [qs] = useSearchParams();
  const raw = qs.get("c") || "All", c = categoryKey(raw) || raw;
  const isCustom = c.indexOf("src:") === 0 || c.indexOf("q:") === 0 || categoryKey(c) === c;
  const q = useData(() => (isCustom ? loadTab(c) : loadHome(c)), [c], { keep: true, cacheKey: "home:" + c }), d = q.data;
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
  } else if (isCustom) S = d.tabStories;
  else if (c !== "All") S = ALL.filter((x) => x.category === c);

  const bar = <CatBar d={d} c={c} onSearch={openS} />;
  const overlay = search ? <Search d={d} S={S} state={search} onClose={closeS} /> : null;
  const wrap = (inner) => <>{bar}<main className="wide" style={{ paddingBottom: 72 }} id="page">{inner}</main>{overlay}<PopHost /></>;

  if (gate) return wrap(<><div className="feed-h"><h1>For you</h1></div><div className="gate"><p>Sign in to get a feed built around the publishers and topics you follow.</p><button type="button" className="ab filled" id="gate-in" onClick={openLogin}><span>Sign in</span></button></div></>);
  if (!S.length) return wrap(<p className="empty" style={{ paddingTop: 48 }}>No stories in this feed yet. <Link to="/">Back to top stories</Link></p>);

  const headLabel = isCustom ? resolveTabs(readTabs()).label[c] || c.replace(/^\w+:/, "") : c;
  const head = c === "All" ? null : (
    <>
      <div className="feed-h"><h1>{headLabel}</h1></div>
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
