import { useState } from "react";
import { shareStory } from "./shell/toast.js";
import { Link } from "react-router-dom";
import { Ph as PhBox, Fav } from "./Photo.jsx";
import { push } from "../userData.js";

export const ago = (m) => (m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : Math.round(m / 1440) + "d");

export function Ph({ ratio, src }) {
  return <PhBox r={ratio} src={src} />;
}

export function AiBadge() {
  return (
    <span className="ai"><svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 2l2.2 6.3L18.5 10l-6.3 2.2L10 18.5l-2.2-6.3L1.5 10l6.3-1.7zM19 13l1 2.6 2.6 1-2.6 1L19 21l-1-2.4-2.6-1 2.6-1z" /></svg>AI GENERATED</span>
  );
}

export function TimelineCard({ t }) {
  const past = !!t.archived, events = past ? t.story_count : t.points.length;
  return (
    <article className={"cx" + (past ? " cx-past" : "")}><Link className="cx-img" to={"/timeline/" + t.id} tabIndex={-1} aria-hidden="true"><Ph ratio="3/2" src={t.image} /></Link><div className="cx-b">
      <div className="meta"><span className="cat" style={past ? undefined : { color: "var(--hue-context)" }}>{t.category}</span><span>{past ? "No longer tracked" : t.updated}</span></div>
      <h3><Link to={"/timeline/" + t.id}>{t.title}</Link></h3><p>{t.dek || ""}</p>
      <div className="cx-f"><span>{events} events</span>{t.audio ? <span className="cx-au"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg> Audio</span> : null}</div>
    </div></article>
  );
}

export const PLAY_URL = "https://play.google.com/store/apps/details?id=com.jamid.news";

// Timelines and Explainers are Premium in the app. The website shows one of each as a taste and points
// to the app for the rest. full: the whole-page version for a timeline or explainer that isn't the taste.
export function GetTheApp({ full }) {
  return (
    <section className={"pnote" + (full ? " pnote-full" : "")} aria-labelledby="pnote-h">
      <span className="pnote-k">PREMIUM &middot; ANDROID APP</span>
      <h2 id="pnote-h">{full ? "This one is in the Android app" : "The full Context experience is on Android"}</h2>
      <p>Every tracked timeline and every explainer, with audio narration, are part of Premium in the Open Indian News app. Start with a free 7-day trial in the app.</p>
      <a className="btn btn-primary" href={PLAY_URL} target="_blank" rel="noopener"><span>Get it on Google Play</span></a>
      {full ? <Link className="pnote-back" to="/context">&larr; Back to Context</Link> : null}
    </section>
  );
}

export function ExplainerCard({ x }) {
  return (
    <article className="cx"><Link className="cx-img" to={"/explainer/" + x.id} tabIndex={-1} aria-hidden="true"><Ph ratio="3/2" src={x.image} /></Link><div className="cx-b">
      <div className="meta"><span className="cat" style={{ color: "var(--hue-context)" }}>{x.category}</span><span>{x.read}</span></div>
      <h3><Link to={"/explainer/" + x.id}>{x.title}</Link></h3><p>{x.dek}</p></div></article>
  );
}

// Narration player: shared with the home pages, wired to the site-wide mini player.
export { AudioPlayer } from "./shell/AudioPlayer.jsx";

const BM = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4.5L6 21z" /></svg>
);
const SH = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4" /></svg>
);

export function savedIds() { try { return JSON.parse(localStorage.getItem("oin-saved")) || []; } catch (e) { return []; } }

// Source row: outlet attribution plus save and share (PROTO.srow). onChange fires after a save toggle.
export function SrcRow({ s, onChange }) {
  const [on, setOn] = useState(() => savedIds().indexOf(s.id) > -1);
  const first = (s.coverage[0] || {}).outlet || "News Desk", n = Math.max(0, s.outlet_count - 1);
  function toggle() {
    const list = savedIds(), i = list.indexOf(s.id), next = i < 0;
    if (next) list.push(s.id); else list.splice(i, 1);
    try { localStorage.setItem("oin-saved", JSON.stringify(list)); } catch (x) {}
    setOn(next);
    try { if (JSON.parse(localStorage.getItem("oin-session"))) push({ k: "save", id: s.id, on: next }); } catch (x) { /* signed out */ }
    if (onChange) onChange();
  }
  function share() {
    shareStory(s.id, s.headline);
  }
  return (
    <div className="srcrow">
      <Link className="src" data-sid={s.id} to={"/story/" + s.id + "#coverage"}><span className="favs"><Fav name={(s.coverage[0] || {}).outlet} />{s.outlet_count > 1 ? <Fav name={(s.coverage[1] || {}).outlet} /> : null}</span><span>
        {first}{n ? " & " + n + " other" + (n > 1 ? "s" : "") : ""}</span></Link>
      <div className="acts"><button type="button" className={"act save" + (on ? " on" : "")} aria-pressed={on} aria-label={on ? "Remove from saved" : "Save story"} title={on ? "Saved" : "Save"} onClick={toggle}>{BM}</button>
        <button type="button" className="act" aria-label="Share story" title="Share" onClick={share}>{SH}</button></div>
    </div>
  );
}

