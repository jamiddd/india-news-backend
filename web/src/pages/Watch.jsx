import { useState } from "react";
import { Link } from "react-router-dom";
import { ago, useSaved, toast, toggleSaved } from "../components/home/shared.jsx";
import { Fav } from "../components/Photo.jsx";
import VideoPlayer from "../components/VideoPlayer.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadWatch } from "../api.js";

// Watch: always dark, like the app's Watch tab, whatever the site theme.
const CSS = `
body{background:#000}
.signal-bar{display:none}
header.masthead,.appbar{display:none !important}
.tabbar{--ink:#FFFFFF; --ink-2:rgba(255,255,255,.68); --bg:#0A0A0A; --line:rgba(255,255,255,.14); background:rgba(10,10,10,.72) !important; border-color:rgba(255,255,255,.14) !important}
.wt{--wb:#000; --wf:#fff; --wf2:rgba(255,255,255,.72); --wl:rgba(255,255,255,.14); color:var(--wf); background:var(--wb); min-height:100vh; padding-bottom:120px; max-width:760px; margin:0 auto}
.wt a{color:inherit}
.wt-top{display:flex; justify-content:center; padding:calc(12px + env(safe-area-inset-top)) 16px 4px}
.wt-sw{position:relative; display:flex; padding:3px; border-radius:18px; background:rgba(255,255,255,.12)}
.wt-sw i{position:absolute; top:3px; left:3px; width:72px; height:30px; border-radius:15px; background:#1976D2; transition:transform .25s cubic-bezier(.3,.8,.3,1)}
.wt-sw.swipe i{transform:translateX(72px)}
.wt-sw button{position:relative; width:72px; height:30px; border:0; background:none; border-radius:15px; color:var(--wf2); font:600 13px var(--sans,Inter,sans-serif); cursor:pointer}
.wt-sw button.on{color:#fff}
.wt-pl{background-size:cover !important; background-position:center !important; position:sticky; top:0; z-index:10; box-shadow:0 8px 20px -10px rgba(0,0,0,.8); aspect-ratio:16/9; margin-top:10px; background:linear-gradient(135deg,#23262d,#14161a); display:grid; place-items:center; cursor:pointer; overflow:hidden}
.wt-pl .lbl{position:absolute; left:12px; top:10px; font:600 11px var(--mono); letter-spacing:.08em; color:var(--wf2); text-transform:uppercase}
.wt-play{width:64px; height:64px; border-radius:50%; border:0; display:grid; place-items:center; background:rgba(0,0,0,.55); color:#fff; backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px); cursor:pointer}
.wt-bar{position:absolute; left:0; right:0; bottom:0; height:4px; background:rgba(255,255,255,.2)}
.wt-bar b{display:block; height:100%; width:0; background:#1976D2}
.wt-time{position:absolute; right:10px; bottom:12px; font:600 11px var(--mono); background:rgba(0,0,0,.7); padding:2px 6px; border-radius:5px}
.wt-now{padding:14px 16px}
.wt-now h1{margin:0 0 8px; font-size:21px; line-height:1.28; font-weight:600; letter-spacing:-.01em}
.wt-src{display:flex; align-items:center; gap:8px; font-size:14px; color:var(--wf2)}
.wt-src .fv,.wt-tx small .fv{flex:0 0 auto; display:inline-block; border-radius:50%}
.wt-src .fv{width:24px; height:24px}
.wt-tx small .fv{width:27px; height:27px}
.wt-pills{display:flex; gap:8px; margin-top:12px; overflow-x:auto; scrollbar-width:none}
.wt-pill{flex:0 0 auto; display:inline-flex; align-items:center; gap:7px; height:36px; padding:0 14px; border-radius:18px; border:0; background:rgba(255,255,255,.12); color:#fff; font:600 13px var(--sans,Inter,sans-serif); cursor:pointer; text-decoration:none}
.wt-pill:hover{text-decoration:none; background:rgba(255,255,255,.2)}
.wt-pill.on{color:#fff; background:#1976D2}
.wt-pill.on svg{fill:currentColor}
.wt-h{padding:8px 16px 10px; margin:6px 0 0; border-top:1px solid var(--wl); padding-top:16px; font-size:15px; font-weight:700}
.wt-row{display:flex; gap:12px; padding:8px 16px; cursor:pointer; text-align:left; width:100%; border:0; background:none; color:inherit; font:inherit}
.wt-row:hover{background:rgba(255,255,255,.05)}
.wt-th{background-size:cover; background-position:center; position:relative; flex:0 0 168px; aspect-ratio:16/9; border-radius:10px; background:linear-gradient(135deg,#2a2e36,#171a1f); overflow:hidden}
.wt-th em{position:absolute; right:6px; bottom:6px; font:700 11px var(--mono); font-style:normal; background:rgba(0,0,0,.78); padding:2px 6px; border-radius:5px}
.wt-row.cur .wt-th{outline:2px solid #1976D2; outline-offset:-2px}
.wt-tx{min-width:0; display:flex; flex-direction:column; gap:5px}
.wt-tx b{font-size:15px; font-weight:600; line-height:1.3; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden}
.wt-tx small{font-size:12.5px; color:var(--wf2); display:flex; align-items:center; gap:6px}
.wt-feed{height:calc(100dvh - 64px); overflow-y:auto; scroll-snap-type:y mandatory; scrollbar-width:none}
.wt-feed::-webkit-scrollbar{display:none}
.wt-card{background-size:cover; background-position:center; position:relative; height:100%; scroll-snap-align:start; scroll-snap-stop:always; background:linear-gradient(160deg,#2a2e36,#0d0f12); display:flex; flex-direction:column; justify-content:flex-end; padding:18px 76px 96px 16px; box-sizing:border-box}
.wt-card::after{content:""; position:absolute; inset:45% 0 0 0; z-index:1; background:linear-gradient(transparent,rgba(0,0,0,.85)); pointer-events:none}
.wt-card > *{position:relative; z-index:1}
.wt-card h2{margin:8px 0 0; font-size:21px; line-height:1.28; font-weight:700}
.wt-rail{position:absolute; right:10px; bottom:96px; z-index:2; display:flex; flex-direction:column; gap:14px; align-items:center}
.wt-rail .wt-pill{width:48px; height:48px; padding:0; justify-content:center; border-radius:50%; background:rgba(0,0,0,.45)}
.wt-card .wt-play{position:absolute; left:50%; top:40%; transform:translate(-50%,-50%); z-index:1}
.wt-card.playing .wt-play{opacity:0}
.wt-empty{padding:60px 20px; text-align:center; color:var(--wf2)}
@media (min-width:1001px){ .wt-feed{height:calc(100vh - 140px)} }
`;

function mmss(n) { return Math.floor(n / 60) + ":" + ("0" + (n % 60)).slice(-2); }
const PLAY = <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>;
const PAUSE = <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>;
const BM = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4.5L6 21z" /></svg>;
const SH = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4" /></svg>;
const RD = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 4h14v16H5z" /><path d="M9 9h6M9 13h6M9 17h3" /></svg>;

// Data access for this page: loadWatch (src/api.js, GET /clusters/videos). Real stories carry
// `video` (the playing outlet and its length); the sample has neither, so both fall back.
function videoStories(d) {
  return d.stories.filter((s) => s.media.some((m) => m.kind === "video")).map((s) => {
    const m = s.media.filter((x) => x.kind === "video")[0], vd = s.video || {};
    return {
      s, id: s.id, outlet: vd.outlet || (s.coverage[0] ? s.coverage[0].outlet : ""),
      dur: vd.duration || m.duration || null,
      src: vd.src || m.src || null, articleId: vd.article_id || m.article_id, poster: vd.poster || m.poster || s.image || null,
    };
  });
}

function Pills({ v, rail }) {
  const on = useSaved(v.id);
  const save = () => toggleSaved(v.id);
  function share() {
    const url = location.origin + "/story/" + v.id;
    if (navigator.share) { navigator.share({ title: v.s.headline, url }).catch(() => {}); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(() => toast("Link copied"), () => toast(url));
    else toast(url);
  }
  return (
    <div className={rail ? "wt-rail" : "wt-pills"}>
      <button type="button" className={"wt-pill" + (on ? " on" : "")} aria-pressed={on} aria-label="Save" onClick={save}>{BM}{rail ? null : <span>Save</span>}</button>
      <button type="button" className="wt-pill" aria-label="Share" onClick={share}>{SH}{rail ? null : <span>Share</span>}</button>
      <Link className="wt-pill" to={"/story/" + v.id} aria-label="Read story">{RD}{rail ? null : <span>Read story</span>}</Link>
    </div>
  );
}

function Src({ v }) { return <span className="wt-src"><Fav name={v.outlet} /><span>{v.outlet} &middot; {ago(v.s.minutes_ago)} ago</span></span>; }

export default function Watch() {
  const q = useData(loadWatch), d = q.data;
  const [cur, setCur] = useState(0), [playing, setPlaying] = useState(false), [mode, setMode] = useState("watch");
  const V = d ? videoStories(d) : [];

  // Moving on when a video ends (Watch mode plays the list in order).
  const next = () => { if (mode === "watch" && V.length > 1) { setCur((c) => (c + 1) % V.length); setPlaying(true); } else setPlaying(false); };

  const wait = statusView(q, "videos", ({ children }) => <main className="wt" id="page"><style>{CSS}</style>{children}</main>);
  if (wait) return wait;

  const switcher = (
    <div className="wt-top"><div className={"wt-sw" + (mode === "swipe" ? " swipe" : "")} role="tablist"><i></i>
      {["watch", "swipe"].map((m) => <button key={m} type="button" className={mode === m ? "on" : ""} role="tab" onClick={() => { setMode(m); setPlaying(false); }}>{m === "watch" ? "Watch" : "Swipe"}</button>)}
    </div></div>
  );
  let body;
  if (!V.length) body = <p className="wt-empty">No videos yet.</p>;
  else if (mode === "swipe") {
    body = (
      <div className="wt-feed" id="feed">{V.map((v, i) => <SwipeCard key={v.id} v={v} />)}</div>
    );
  } else {
    const v = V[cur];
    body = (
      <>
        <div className="wt-pl" id="pl" style={v.poster ? { backgroundImage: "url(" + v.poster + ")" } : undefined}>
          {playing
            ? <VideoPlayer key={v.id} src={v.src} articleId={v.articleId} poster={v.poster} onEnded={next} />
            : <><span className="lbl">Video</span>
              <button type="button" className="wt-play" aria-label="Play" onClick={() => setPlaying(true)}>{PLAY}</button>
              {v.dur ? <span className="wt-time">{mmss(v.dur)}</span> : null}</>}
        </div>
        <div className="wt-now"><h1>{v.s.headline}</h1><Src v={v} /><Pills v={v} /></div>
        <h2 className="wt-h">Up next</h2>
        {V.map((x, i) => i === cur ? null : (
          <button type="button" className="wt-row" key={x.id} onClick={() => { setCur(i); setPlaying(true); window.scrollTo(0, 0); }}>
            <span className="wt-th" style={x.poster ? { backgroundImage: "url(" + x.poster + ")" } : undefined}>{x.dur ? <em>{mmss(x.dur)}</em> : null}</span>
            <span className="wt-tx"><b>{x.s.headline}</b><small><Fav name={x.outlet} />{x.outlet} &middot; {ago(x.s.minutes_ago)}</small></span>
          </button>
        ))}
      </>
    );
  }
  return <main className="wt" id="page"><style>{CSS}</style>{switcher}{body}</main>;
}

function SwipeCard({ v }) {
  const [on, setOn] = useState(false);
  return (
    <section className={"wt-card" + (on ? " playing" : "")} style={v.poster ? { backgroundImage: "url(" + v.poster + ")" } : undefined}>
      {on ? <VideoPlayer src={v.src} articleId={v.articleId} poster={v.poster} /> : <button type="button" className="wt-play" aria-label="Play" onClick={() => setOn(true)}>{PLAY}</button>}
      <Src v={v} /><h2>{v.s.headline}</h2><Pills v={v} rail />
    </section>
  );
}
