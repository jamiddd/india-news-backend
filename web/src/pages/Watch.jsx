import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ago, useSaved, toast, toggleSaved } from "../components/home/shared.jsx";
import { Fav } from "../components/Photo.jsx";
import VideoPlayer from "../components/VideoPlayer.jsx";
import { useData } from "../components/Status.jsx";
import { loadWatch, loadShorts } from "../api.js";

// Watch, laid out like the app's Watch tab (WatchTabScreen / SwipeShortsScreen). Always dark.
//
//  * The Watch | Swipe pill floats in a 48px strip at the top; every view starts below that strip,
//    and the strip carries a blurred copy of the current video's picture for the pill to sit on.
//  * Watch: a 16:9 player pinned under the strip, edge to edge, with the now-playing info and the
//    "Up next" list scrolling beneath it.
//  * Swipe: a vertical pager, one Short per screen. The settled Short plays by itself. Nothing is
//    drawn over the video: the caption and the Save / Read buttons sit in a strip beneath it, and
//    that strip keeps clear of the floating tab bar.
const CSS = `
body{background:#000}
.signal-bar{display:none}
header.masthead,.appbar{display:none !important}
.tabbar{--ink:#FFFFFF; --ink-2:rgba(255,255,255,.68); --bg:#0A0A0A; --line:rgba(255,255,255,.14); background:rgba(10,10,10,.72) !important; border-color:rgba(255,255,255,.14) !important}
.wt{--wt-strip:calc(48px + env(safe-area-inset-top,0px)); --wt-w:min(100vw,760px); --wt-nav:calc(84px + env(safe-area-inset-bottom,0px) + var(--mplay-h,0px));
  --wf:#fff; --wf2:rgba(255,255,255,.8); --wl:rgba(255,255,255,.14); color:var(--wf); background:#000; min-height:100vh; padding-bottom:calc(var(--wt-nav) + 12px); max-width:760px; margin:0 auto}
.wt a{color:inherit}
.wt-empty{padding:60px 24px; text-align:center; color:var(--wf2); font-size:15px}

/* blurred copy of the current picture (the app's HeaderBackdrop) */
.wt-bd{position:absolute; inset:0; overflow:hidden; background:#000}
.wt-bd i{position:absolute; inset:-60px; background-size:cover; background-position:center; filter:blur(48px)}
.wt-bd::after{content:""; position:absolute; inset:0; background:rgba(0,0,0,.6)}

/* the strip that holds the Watch | Swipe pill */
.wt-strip{position:relative; height:var(--wt-strip); padding-top:env(safe-area-inset-top,0px); box-sizing:border-box; display:flex; align-items:center; justify-content:center}
.wt-sw{position:relative; z-index:1; display:flex; padding:3px; border-radius:18px; background:rgba(0,0,0,.55); backdrop-filter:blur(21px); -webkit-backdrop-filter:blur(21px)}
.wt-sw i{position:absolute; top:3px; left:3px; width:72px; height:30px; border-radius:15px; background:#1976D2; transition:transform .25s cubic-bezier(.3,.8,.3,1)}
.wt-sw.swipe i{transform:translateX(72px)}
.wt-sw button{position:relative; width:72px; height:30px; border:0; background:none; border-radius:15px; color:rgba(255,255,255,.85); font:600 13px var(--sans,Inter,sans-serif); cursor:pointer}
.wt-sw button.on{color:#fff}

/* Watch: the pinned top part (strip + 16:9 player) and what scrolls beneath it */
.wt-head{position:sticky; top:0; z-index:10}
.wt-head > .wt-bd{z-index:0}
.wt-head > .wt-strip{z-index:1}
.wt-pl{position:relative; z-index:1; aspect-ratio:16/9; background:#000 center/cover no-repeat; cursor:pointer; overflow:hidden; display:grid; place-items:center}
.wt-play{width:64px; height:64px; border-radius:50%; border:0; display:grid; place-items:center; background:rgba(0,0,0,.55); color:#fff; backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px); cursor:pointer}
.wt-time{position:absolute; right:10px; bottom:10px; font:700 11px var(--mono); background:rgba(0,0,0,.78); padding:2px 6px; border-radius:5px}
.wt-now{position:relative; overflow:hidden}
.wt-nowin{position:relative; padding:14px 16px; display:flex; flex-direction:column; gap:8px}
.wt-nowin h1{margin:0; font-size:22px; line-height:27px; font-weight:600}
.wt-src{display:flex; align-items:center; gap:8px; font-size:14px; color:var(--wf2)}
.wt-src .fv{flex:0 0 auto; display:inline-block; width:20px; height:20px; border-radius:50%}
.wt-pills{display:flex; gap:8px; overflow-x:auto; scrollbar-width:none}
.wt-pill{flex:0 0 auto; display:inline-flex; align-items:center; gap:7px; height:36px; padding:0 14px; border-radius:18px; border:0; background:rgba(255,255,255,.16); color:#fff; font:600 14px var(--sans,Inter,sans-serif); cursor:pointer; text-decoration:none}
.wt-pill:hover{text-decoration:none; background:rgba(255,255,255,.24)}
.wt-pill.on svg{fill:currentColor}
.wt-h{position:sticky; top:calc(var(--wt-strip) + var(--wt-w) * 9 / 16); z-index:5; margin:0; padding:10px 16px; background:#0A0A0A; font-size:14px; font-weight:700}
.wt-row{display:flex; gap:12px; padding:7px 16px; cursor:pointer; text-align:left; width:100%; box-sizing:border-box; border:0; background:#0A0A0A; color:inherit; font:inherit}
.wt-row:hover{background:#15151A}
.wt-th{position:relative; flex:0 0 168px; aspect-ratio:16/9; border-radius:10px; background:#2a2e36 center/cover no-repeat; overflow:hidden}
.wt-th em{position:absolute; right:6px; bottom:6px; font:700 11px var(--mono); font-style:normal; background:rgba(0,0,0,.78); padding:2px 6px; border-radius:5px}
.wt-tx{min-width:0; display:flex; flex-direction:column; gap:4px}
.wt-tx b{font-size:16px; font-weight:600; line-height:20px; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden}
.wt-tx small{font-size:12px; color:var(--wf2); display:flex; align-items:center; gap:6px; min-width:0}
.wt-tx small .fv{flex:0 0 auto; display:inline-block; width:16px; height:16px; border-radius:50%}
.wt-tx small span{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.wt-rows{background:#0A0A0A}

/* Swipe: the page is exactly one screen; the strip on top, the pager below it */
.wt-swipe{height:100vh; height:100dvh; min-height:0; padding-bottom:0; display:flex; flex-direction:column; overflow:hidden}
.wt-swipe > .wt-strip{flex:0 0 auto}
.wt-feed{flex:1 1 auto; min-height:0; overflow-y:auto; overscroll-behavior:contain; scroll-snap-type:y mandatory; scrollbar-width:none}
.wt-feed::-webkit-scrollbar{display:none}
.wt-card{height:100%; scroll-snap-align:start; scroll-snap-stop:always; display:flex; flex-direction:column; background:#000}
.wt-vid{position:relative; flex:1 1 auto; min-height:0; background:#000 center/cover no-repeat}
.wt-vid .vp{background:transparent}
.wt-spin{position:absolute; left:50%; top:50%; width:36px; height:36px; margin:-18px 0 0 -18px; border-width:3px; border-color:rgba(255,255,255,.25); border-top-color:#1976D2}
.wt-cap{flex:0 0 auto; display:flex; align-items:center; padding:10px 4px calc(10px + var(--wt-nav)) 16px; background:#000}
.wt-cap .tx{flex:1 1 auto; min-width:0; display:flex; flex-direction:column; gap:4px}
.wt-cap .tx b{font-size:14px; font-weight:600; line-height:20px; color:#fff; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden}
.wt-cap .tx small{font-size:12px; color:rgba(255,255,255,.75); white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.wt-cap .acts{display:flex; flex:0 0 auto}
.wt-act{width:44px; height:44px; border-radius:50%; display:grid; place-items:center; color:#fff; background:none; border:0; cursor:pointer; text-decoration:none}
.wt-act svg{width:24px; height:24px}
.wt-act.on svg{fill:currentColor}

/* loading placeholders, shaped like the real thing */
.wt .sk{background:linear-gradient(100deg,rgba(255,255,255,.08) 30%,rgba(255,255,255,.15) 50%,rgba(255,255,255,.08) 70%); background-size:220% 100%}
.wt-skp{aspect-ratio:16/9; border-radius:0}
.wt-swipe > .wt-card{flex:1 1 auto; height:auto; min-height:0}
main.wt-swipe ~ footer{display:none}
@media (min-width:1001px){ .wt-swipe{height:calc(100vh - 24px)} }
`;

function mmss(n) { return Math.floor(n / 60) + ":" + ("0" + (n % 60)).slice(-2); }
const PLAY = <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>;
const BM = (s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4.5L6 21z" /></svg>;
const SH = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4" /></svg>;
const RD = (s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 4h14v16H5z" /><path d="M9 9h6M9 13h6M9 17h3" /></svg>;

// Data access for this page: loadWatch / loadShorts (src/api.js, GET /clusters/videos).
function videoStories(d) {
  return d.stories.filter((s) => s.media.some((m) => m.kind === "video")).map((s) => {
    const m = s.media.filter((x) => x.kind === "video")[0], vd = s.video || {};
    return {
      s, id: s.id, outlet: vd.outlet || (s.coverage[0] ? s.coverage[0].outlet : ""),
      dur: vd.duration || m.duration || null,
      src: vd.src || m.src || null, articleId: vd.article_id || m.article_id, poster: vd.poster || m.poster || s.image || null,
      short: !!m.short,                       // the backend's flag for a real vertical short
    };
  });
}

const bg = (u) => (u ? { backgroundImage: "url(" + u + ")" } : undefined);
const Backdrop = ({ src }) => <div className="wt-bd" aria-hidden="true">{src ? <i style={bg(src)}></i> : null}</div>;

function Switcher({ mode, onMode }) {
  return (
    <div className={"wt-sw" + (mode === "swipe" ? " swipe" : "")} role="tablist" aria-label="Watch or Swipe"><i></i>
      {["watch", "swipe"].map((m) => <button key={m} type="button" className={mode === m ? "on" : ""} role="tab" aria-selected={mode === m} onClick={() => onMode(m)}>{m === "watch" ? "Watch" : "Swipe"}</button>)}
    </div>
  );
}

function share(v) {
  const url = location.origin + "/story/" + v.id;
  if (navigator.share) { navigator.share({ title: v.s.headline, url }).catch(() => {}); return; }
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(() => toast("Link copied"), () => toast(url));
  else toast(url);
}

function Pills({ v }) {
  const on = useSaved(v.id);
  return (
    <div className="wt-pills">
      <button type="button" className={"wt-pill" + (on ? " on" : "")} aria-pressed={on} onClick={() => toggleSaved(v.id)}>{BM(18)}<span>{on ? "Saved" : "Save"}</span></button>
      <button type="button" className="wt-pill" onClick={() => share(v)}>{SH}<span>Share</span></button>
      <Link className="wt-pill" to={"/story/" + v.id}>{RD(18)}<span>Read story</span></Link>
    </div>
  );
}

function Src({ v }) { return <span className="wt-src"><Fav name={v.outlet} /><span>{[v.outlet, ago(v.s.minutes_ago) + " ago"].filter(Boolean).join(" · ")}</span></span>; }

export default function Watch() {
  // Reopens on the view the reader used last, as the app does.
  const [mode, setMode] = useState(() => { try { return localStorage.getItem("oin-watch-mode") === "swipe" ? "swipe" : "watch"; } catch (e) { return "watch"; } });
  const pick = (m) => { setMode(m); try { localStorage.setItem("oin-watch-mode", m); } catch (e) { /* private mode */ } };
  return (
    <main className={"wt wt-" + mode} id="page"><style>{CSS}</style>
      {mode === "swipe" ? <SwipeView mode={mode} onMode={pick} /> : <WatchView mode={mode} onMode={pick} />}
    </main>
  );
}

function WatchView({ mode, onMode }) {
  const q = useData(loadWatch, [], { cacheKey: "watch" });
  const [cur, setCur] = useState(0), [playing, setPlaying] = useState(false);
  const V = q.data ? videoStories(q.data) : [];
  const v = V[cur] || V[0];

  if (!q.data || !V.length) {
    return (
      <>
        <div className="wt-head"><Backdrop /><div className="wt-strip"><Switcher mode={mode} onMode={onMode} /></div>
          <div className="wt-pl" style={{ cursor: "default" }}>{!q.data && !q.error ? <div className="sk wt-skp" style={{ position: "absolute", inset: 0 }}></div> : null}</div></div>
        {!q.data && !q.error ? (
          <div className="wt-nowin"><div className="sk" style={{ height: 22, width: "92%" }}></div><div className="sk" style={{ height: 22, width: "60%" }}></div><div className="sk" style={{ height: 14, width: "40%", marginTop: 6 }}></div></div>
        ) : <p className="wt-empty">{q.error ? "Couldn’t load videos. Try again in a moment." : "No videos yet."}</p>}
      </>
    );
  }
  // When a video ends the list rolls on to the next one.
  const next = () => { if (V.length > 1) { setCur((c) => (c + 1) % V.length); setPlaying(true); } else setPlaying(false); };
  return (
    <>
      <div className="wt-head"><Backdrop src={v.poster} /><div className="wt-strip"><Switcher mode={mode} onMode={onMode} /></div>
        <div className="wt-pl" id="pl" style={bg(v.poster)}>
          {playing
            ? <VideoPlayer key={v.id} src={v.src} articleId={v.articleId} poster={v.poster} cover onEnded={next} />
            : <>
              <button type="button" className="wt-play" aria-label="Play" onClick={() => setPlaying(true)}>{PLAY}</button>
              {v.dur ? <span className="wt-time">{mmss(v.dur)}</span> : null}</>}
        </div>
      </div>
      <div className="wt-now"><Backdrop src={v.poster} /><div className="wt-nowin"><h1>{v.s.headline}</h1><Src v={v} /><Pills v={v} /></div></div>
      <h2 className="wt-h">Up next</h2>
      <div className="wt-rows">
        {V.map((x, i) => i === cur ? null : (
          <button type="button" className="wt-row" key={x.id} onClick={() => { setCur(i); setPlaying(true); window.scrollTo(0, 0); }}>
            <span className="wt-th" style={bg(x.poster)}>{x.dur ? <em>{mmss(x.dur)}</em> : null}</span>
            <span className="wt-tx"><b>{x.s.headline}</b><small><Fav name={x.outlet} /><span>{[x.outlet, ago(x.s.minutes_ago)].filter(Boolean).join(" · ")}</span></small></span>
          </button>
        ))}
      </div>
    </>
  );
}

// Swipe: vertical videos only (the backend's "shorts" list, as in the app). Horizontal videos belong to
// the Watch tab, so anything not flagged as a real short is left out.
function SwipeView({ mode, onMode }) {
  const q = useData(loadShorts, [], { cacheKey: "shorts" });
  const [cur, setCur] = useState(0);
  const V = q.data ? videoStories(q.data).filter((v) => v.short) : [];
  let body;
  if (!q.data && !q.error) {
    body = (
      <div className="wt-card"><div className="wt-vid"><div className="sk" style={{ position: "absolute", inset: 0, borderRadius: 0 }}></div></div>
        <div className="wt-cap"><div className="tx"><div className="sk" style={{ height: 14 }}></div><div className="sk" style={{ height: 14, width: "70%" }}></div><div className="sk" style={{ height: 11, width: "40%" }}></div></div></div></div>
    );
  } else if (!V.length) {
    body = <p className="wt-empty">{q.error ? "Couldn’t load videos. Try again in a moment." : "No shorts yet."}</p>;
  } else {
    body = <div className="wt-feed" id="feed">{V.map((v, i) => <SwipeCard key={v.id} v={v} onActive={() => setCur(i)} />)}</div>;
  }
  return (
    <>
      <div className="wt-strip"><Backdrop src={V[cur] && V[cur].poster} /><Switcher mode={mode} onMode={onMode} /></div>
      {body}
    </>
  );
}

function SwipeCard({ v, onActive }) {
  const [active, setActive] = useState(false), ref = useRef(null), saved = useSaved(v.id);
  const notify = useRef(onActive); notify.current = onActive;
  // The page that settles on screen plays by itself; the others show their picture.
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => { const a = e.intersectionRatio >= 0.6; setActive(a); if (a) notify.current(); }, { threshold: [0, 0.6, 1] });
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);
  return (
    <section ref={ref} className="wt-card">
      <div className="wt-vid" style={bg(v.poster)}>
        {active ? <i className="spin wt-spin" aria-hidden="true"></i> : null}
        {active ? <VideoPlayer key={v.id} src={v.src} articleId={v.articleId} poster={v.poster} loop controls={false} /> : null}
      </div>
      <div className="wt-cap">
        <div className="tx"><b>{v.s.headline}</b><small>{[v.outlet, ago(v.s.minutes_ago) + " ago"].filter(Boolean).join(" · ")}</small></div>
        <div className="acts">
          <button type="button" className={"wt-act" + (saved ? " on" : "")} aria-pressed={saved} aria-label={saved ? "Saved" : "Save"} onClick={() => toggleSaved(v.id)}>{BM(24)}</button>
          <Link className="wt-act" to={"/story/" + v.id} aria-label="Read story">{RD(24)}</Link>
        </div>
      </div>
    </section>
  );
}
