// Helpers shared by the Home, Story, List, Sources and Watch pages. These replace the
// document-level behaviours of prototype/static/shell.js (save/share, coverage popover,
// media carousel + viewer, narration player, follows) as React components.
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { Viewer } from "../shell/MediaViewer.jsx";
import { Ph, Fav } from "../Photo.jsx";
import VideoPlayer from "../VideoPlayer.jsx";
import { shareStory } from "../shell/toast.js";
import { push } from "../../userData.js";

export function ago(m) { return m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : Math.round(m / 1440) + "d"; }
export function slug(n) { return String(n).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""); }

// Data access lives in src/api.js (one loader per page) and components/Status.jsx (useData).

function load(k, def) { try { return JSON.parse(localStorage.getItem(k)) || def; } catch (e) { return def; } }
function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

import { toast } from "../shell/toast.js";
export { toast };

// Small localStorage-backed store so every copy of a story/follow flips together.
const subs = new Set();
function emit() { subs.forEach((f) => f()); }
function sub(f) { subs.add(f); return () => subs.delete(f); }
function snap(k) { try { return localStorage.getItem(k) || "[]"; } catch (e) { return "[]"; } }
addEventListener("oin:data", emit);   // a sync changed the lists
function useList(k) { return JSON.parse(useSyncExternalStore(sub, () => snap(k))); }

export function signedIn() { return !!load("oin-session", null); }
// Opens the sign-in modal owned by the shell (components/shell/SignIn.jsx).
export function openLogin() { dispatchEvent(new Event("oin:login")); }

// ---- follows -------------------------------------------------------------
const FK = "oin-follow";
// name: the display name (topics are sent to the server by name, e.g. "Monetary Policy").
export function FollowBtn({ k, label, name }) {
  const on = useList(FK).indexOf(k) > -1;
  function click() {
    if (!signedIn()) { openLogin(); return; }
    const l = load(FK, []), i = l.indexOf(k);
    if (i < 0) l.push(k); else l.splice(i, 1);
    save(FK, l); emit();
    push({ k: "follow", key: k, on: i < 0, name });
  }
  return <button type="button" className={"fbtn" + (on ? " on" : "")} aria-pressed={on} onClick={click}>{on ? "Following" : "Follow"}{label ? " " + label : ""}</button>;
}
export function getFollows() { return load(FK, []); }

// ---- save / share --------------------------------------------------------
const SV = "oin-saved";
export function useSaved(id) { return useList(SV).indexOf(+id) > -1; }
export function toggleSaved(id) {
  const list = load(SV, []), i = list.indexOf(+id), now = i < 0;
  if (now) list.push(+id); else list.splice(i, 1);
  save(SV, list); emit(); toast(now ? "Saved" : "Removed from saved");
  if (signedIn()) push({ k: "save", id: +id, on: now });
}
export function Acts({ id }) {
  const on = useSaved(id);
  function onSave(e) { e.preventDefault(); toggleSaved(id); }
  function onShare(e) {
    e.preventDefault();
    shareStory(id);
  }
  return (
    <div className="acts">
      <button type="button" className={"act save" + (on ? " on" : "")} aria-pressed={on} aria-label={on ? "Remove from saved" : "Save story"} title={on ? "Saved" : "Save"} onClick={onSave}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4.5L6 21z" /></svg>
      </button>
      <button type="button" className="act" aria-label="Share story" title="Share" onClick={onShare}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4" /></svg>
      </button>
    </div>
  );
}
export function Tip({ id }) { return <div className="tip" role="group" aria-label="Story actions"><Acts id={id} /></div>; }
export function Fv({ name }) { return <Fav name={name} />; }
export { Ph };

// ---- coverage popover ----------------------------------------------------
let pop = null; // { story, anchor }
const popSubs = new Set();
function setPop(p) { pop = p; popSubs.forEach((f) => f()); }
function usePop() { return useSyncExternalStore((f) => { popSubs.add(f); return () => popSubs.delete(f); }, () => pop); }

export function CoverageRows({ list, link = false }) {
  return list.map((v, i) => (
    <div key={i} className={"pop-r" + (i === list.length - 1 ? " last" : "")}>
      <span className="pop-t">{ago(v.minutes_ago)}</span><span className={"pop-dot" + (i === 0 ? " first" : "")}></span>
      <div><div className="pop-n"><Fav name={v.outlet} /><Link className="so" to={"/source/" + slug(v.outlet)}><b>{v.outlet}</b></Link>{i === 0 && <em>FIRST</em>}</div><div className="pop-s">{link ? <a href="#" target="_blank" rel="noopener" style={{ color: "inherit" }} onClick={(e) => e.preventDefault()}>{v.title}</a> : v.title}</div></div>
    </div>
  ));
}

export function PopHost() {
  const p = usePop();
  const el = useRef(null);
  useLayoutEffect(() => {
    if (!p || !el.current) return;
    const a = p.anchor, node = el.current, r = a.getBoundingClientRect(), w = node.offsetWidth, h = node.offsetHeight;
    const left = Math.max(12, Math.min(r.left, innerWidth - w - 12));
    let top = r.bottom + 8;
    if (top + h > innerHeight - 12 && r.top - h - 8 > 12) top = r.top - h - 8;
    node.style.left = left + scrollX + "px"; node.style.top = top + scrollY + "px";
    node.querySelector(".pop-x").focus();
  }, [p]);
  useEffect(() => {
    if (!p) return;
    const close = () => { p.anchor.setAttribute("aria-expanded", "false"); p.anchor.focus(); setPop(null); };
    const onClick = (e) => { if (el.current && !el.current.contains(e.target) && !e.target.closest("a.src[data-sid]")) setPop(null); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("click", onClick); document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("click", onClick); document.removeEventListener("keydown", onKey); };
  }, [p]);
  useEffect(() => () => setPop(null), []);
  if (!p) return null;
  const list = p.story.coverage.slice().sort((x, y) => y.minutes_ago - x.minutes_ago);
  return createPortal(
    <div className="pop" role="dialog" aria-label="Coverage of this story" ref={el}>
      <div className="pop-h"><b>Coverage</b><span>{list.length}{list.length === 1 ? " article" : " articles"}</span><button type="button" className="pop-x" aria-label="Close" onClick={() => setPop(null)}>&times;</button></div>
      <div className="pop-l"><CoverageRows list={list} /></div>
    </div>, document.body);
}

export function Src({ s }) {
  const first = (s.coverage[0] || {}).outlet || "News Desk", n = Math.max(0, s.outlet_count - 1);
  const outs = s.coverage.map((c) => c.outlet).filter((o, i, a) => a.indexOf(o) === i);
  function click(e) {
    e.preventDefault();
    const a = e.currentTarget;
    if (pop && pop.anchor === a) { setPop(null); return; }
    a.setAttribute("aria-expanded", "true");
    setPop({ story: s, anchor: a });
  }
  return (
    <a className="src" data-sid={s.id} href={"/story/" + s.id + "#coverage"} onClick={click}>
      <span className="favs"><Fav name={outs[0]} />{s.outlet_count > 1 && <Fav name={outs[1]} />}</span>
      <span>{first}{n ? " & " + n + " other" + (n > 1 ? "s" : "") : ""}</span>
    </a>
  );
}
export function SRow({ s }) { return <div className="srcrow"><Src s={s} /><Acts id={s.id} /></div>; }

// ---- media carousel + fullscreen viewer ---------------------------------
const PLAY_SVG = <svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z" /></svg>;
export function Carousel({ s }) {
  const m = s.media || [];
  const track = useRef(null), car = useRef(null), idle = useRef(null);
  const [i, setI] = useState(0), [moving, setMoving] = useState(false), [view, setView] = useState(null);
  const [vp, setVp] = useState(null);   // index of the slide whose video is playing inline
  useEffect(() => { if (vp !== null && vp !== i) setVp(null); }, [i]);
  const calm = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (m.length < 2) {
    const x = m[0], v = x && x.kind === "video", label = (x && x.label) || "image";
    return (
      <>
        <Ph className="mv" r="16/9" src={x ? (v ? x.poster : x.src) : null} msg={v ? false : undefined} role="button" tabIndex={0} aria-label={v ? "Play video" : "Open media fullscreen"}
          onClick={(e) => { e.preventDefault(); if (v) setVp(0); else setView(true); }} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (v) setVp(0); else setView(true); } }}>
          {v && vp !== 0 && <span className="car-play">{PLAY_SVG}</span>}
          {v && vp === 0 && <VideoPlayer src={x.src} articleId={x.article_id} poster={x.poster} />}
        </Ph>
        {view && <Viewer items={[{ video: false, src: x && x.src, label }]} start={0} onClose={() => setView(null)} />}
      </>
    );
  }
  const idx = () => { const t = track.current; return Math.round(t.scrollLeft / t.clientWidth); };
  function go(j) {
    j = Math.max(0, Math.min(m.length - 1, j));
    track.current.scrollTo({ left: j * track.current.clientWidth, behavior: calm ? "auto" : "smooth" });
    setI(j);
  }
  function wake() { setMoving(true); clearTimeout(idle.current); idle.current = setTimeout(() => setMoving(false), 1500); }
  const chev = (d) => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
  return (
    <>
      <div className={"car" + (moving ? " moving" : "")} ref={car} role="region" aria-roledescription="carousel" aria-label="Media for this story" tabIndex={0}
        onPointerMove={wake} onPointerDown={wake} onPointerOut={(e) => { if (!car.current.contains(e.relatedTarget)) { clearTimeout(idle.current); setMoving(false); } }}
        onKeyDown={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); go(idx() + (e.key === "ArrowRight" ? 1 : -1)); } }}>
        <div className="car-track" ref={track} onScroll={() => setI(idx())}>
          {m.map((x, j) => {
            const v = x.kind === "video";
            return (
              <Ph key={j} className="car-slide" r="16/9" src={v ? x.poster : x.src} msg={v ? false : undefined} role="group" aria-roledescription="slide" aria-label={(j + 1) + " of " + m.length}
                onClick={(e) => { e.preventDefault(); if (v) setVp(j); else setView(j); }}>
                {v && vp !== j && <span className="car-play">{PLAY_SVG}</span>}
                {v && vp === j && <VideoPlayer src={x.src} articleId={x.article_id} poster={x.poster} />}
              </Ph>
            );
          })}
        </div>
        <button type="button" className="car-btn prev" aria-label="Previous media" disabled={i <= 0} onClick={() => go(idx() - 1)}>{chev("M15 5l-7 7 7 7")}</button>
        <button type="button" className="car-btn next" aria-label="Next media" disabled={i >= m.length - 1} onClick={() => go(idx() + 1)}>{chev("M9 5l7 7-7 7")}</button>
        <div className="car-dots">{m.map((x, j) => <button key={j} type="button" className={"car-dot" + (j === i ? " on" : "")} aria-label={"Go to media " + (j + 1)} aria-current={j === i ? "true" : undefined} onClick={() => go(j)}></button>)}</div>
      </div>
      {view !== null && <Viewer items={m.map((x, j) => ({ video: x.kind === "video", src: x.src, articleId: x.article_id, poster: x.poster, label: x.label + " " + (j + 1) }))} start={view} onClose={() => setView(null)} />}
    </>
  );
}

// ---- narration player (inline) ---------------------------------------------
// Drives the site-wide mini player through the shell context.
export { AudioPlayer } from "../shell/AudioPlayer.jsx";
