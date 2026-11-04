import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import css from "../components/SettingsStyles.js";

// No story data here. Preferences stay in localStorage: the web has no signed-in API
// session, and PUT /api/v1/users/{id}/preferences needs a real user id.

const PK = "oin-prefs";
function sess() { try { return JSON.parse(localStorage.getItem("oin-session")); } catch (e) { return null; } }
function prefs() { try { return JSON.parse(localStorage.getItem(PK)) || {}; } catch (e) { return {}; } }
function setPref(k, v) { const p = prefs(); p[k] = v; try { localStorage.setItem(PK, JSON.stringify(p)); } catch (e) {} }
function theme() { return document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); }
function openLogin() { const b = document.getElementById("user"); if (b) b.click(); }

const ACC = [["Blue", "#1976D2"], ["Purple", "#7B1FA2"], ["Green", "#2E7D32"], ["Red", "#C62828"], ["Amber", "#E65100"], ["Teal", "#00838F"], ["Indigo", "#3949AB"], ["Pink", "#D81B60"], ["Orange", "#F57C00"], ["Navy", "#264653"], ["Slate", "#546E7A"], ["Black", "#171717"]];

function I({ children }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>;
}
const IC = {
  moon: <I><path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" /></I>,
  blur: <I><circle cx="12" cy="12" r="3" /><circle cx="12" cy="12" r="8" strokeDasharray="2 3" /></I>,
  pal: <I><path d="M12 3a9 9 0 100 18c1.5 0 2-1 1.5-2s0-2 1.5-2h2a3 3 0 003-3A9 9 0 0012 3z" /><circle cx="8" cy="11" r="1" /><circle cx="12" cy="7.5" r="1" /><circle cx="16" cy="11" r="1" /></I>,
  feed: <I><path d="M4 6h16M4 12h16M4 18h10" /></I>,
  lang: <I><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></I>,
  bell: <I><path d="M6 17V11a6 6 0 1112 0v6l2 2H4z" /><path d="M10 21h4" /></I>,
  trash: <I><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></I>,
  src: <I><path d="M4 5h16v14H4z" /><path d="M8 9h8M8 13h5" /></I>,
  bm: <I><path d="M6 3h12v18l-6-4.5L6 21z" /></I>,
  msg: <I><path d="M4 5h16v11H9l-5 4z" /></I>,
  play: <I><circle cx="12" cy="12" r="9" /><path d="M10 8.5v7l5.5-3.5z" /></I>,
  star: <I><path d="M12 3l2.8 5.8 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.3l1-6.2L3 9.7l6.2-.9z" /></I>,
  lock: <I><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 118 0v3" /></I>,
  info: <I><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></I>,
  book: <I><path d="M5 4h10a4 4 0 014 4v12H9a4 4 0 01-4-4z" /></I>,
  out: <I><path d="M10 4H5v16h5M15 8l4 4-4 4M19 12H9" /></I>,
  user: <I><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" /></I>,
};
const CH = <svg className="ch" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>;

function Row({ o }) {
  const inner = <><span className="ic">{o.ic}</span><span className="tx"><b>{o.t}</b>{o.s ? <small>{o.s}</small> : null}</span></>;
  if (o.sw) return <button type="button" className="pf-r" role="switch" aria-checked={!!o.on} onClick={o.onToggle}>{inner}<span className="pf-sw"></span></button>;
  if (o.sel) {
    return (
      <div className="pf-r" style={{ cursor: "default" }}>{inner}
        <select className="pf-sel" aria-label={o.t} value={o.val} onChange={o.onChange}>{o.opts.map((x) => <option key={x}>{x}</option>)}</select></div>
    );
  }
  if (o.href) return <Link className={"pf-r" + (o.danger ? " danger" : "")} to={o.href}>{inner}{CH}</Link>;
  return <button type="button" className={"pf-r" + (o.danger ? " danger" : "")} id={o.id} onClick={o.onClick}>{inner}{o.chev === false ? null : CH}</button>;
}

function Group({ t, rows, extra }) {
  return (
    <section className="pf-g"><h2>{t}</h2><div className="pf-card">
      {rows.map((o, i) => <Fragment key={i}><Row o={o} />{o.id === "pf-acc" ? extra : null}</Fragment>)}
    </div></section>
  );
}

export default function Settings() {
  const [, setTick] = useState(0);
  const redraw = () => setTick((n) => n + 1);
  useEffect(() => { document.title = "Settings — Open Indian News"; }, []);
  useEffect(() => {
    const ub = document.getElementById("user");
    if (!ub) return undefined;
    const mo = new MutationObserver(redraw);
    mo.observe(ub, { childList: true, attributes: true, attributeFilter: ["aria-label"] });
    return () => mo.disconnect();
  }, []);

  const u = sess(), p = prefs();
  let saved = 0;
  try { saved = (JSON.parse(localStorage.getItem("oin-saved")) || []).length; } catch (e) {}
  const accent = p.accent || "#1976D2";

  function togglePref(k) {
    const on = !(k === "notify" ? p.notify !== false : k === "dark" ? theme() === "dark" : !!p[k]);
    if (k === "dark") {
      const n = on ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", n);
      try { localStorage.setItem("oin-theme", n); } catch (x) {}
    } else setPref(k, on);
    redraw();
  }
  function signOut() { try { localStorage.removeItem("oin-session"); } catch (x) {} location.reload(); }

  const sw = (
    <div className="pf-sws" role="group" aria-label="Accent colour">
      {ACC.map((a) => <button key={a[1]} type="button" style={{ background: a[1] }} aria-label={a[0]} aria-pressed={a[1] === accent} onClick={() => { setPref("accent", a[1]); redraw(); }}></button>)}
    </div>
  );

  return (
    <main className="wide pf" id="page"><style>{css}</style>
      <div className="pf-grid"><aside className="pf-id">
        {u ? (
          <>
            <div className="pf-av" aria-hidden="true">{(u.name || u.email || "?").trim().charAt(0).toUpperCase()}</div><h1>{u.name || "Reader"}</h1><div className="em">{u.email || ""}</div><div className="jn">Joined {u.joined || "this month"}</div>
            <div className="pf-chips"><Link className="pf-chip" to="/saved">{saved} saved</Link><Link className="pf-chip" to="/sources">Following</Link></div>
            <button type="button" className="btn btn-primary btn-sm" id="pf-edit" onClick={openLogin}><span>Edit profile</span></button>
          </>
        ) : (
          <>
            <div className="pf-av guest" aria-hidden="true">{IC.user}</div><h1>Welcome, Reader</h1>
            <div className="em">Sign in to save stories and sync across devices</div><div className="pf-guest"><b>Sign in or create an account</b><span>It only takes a moment</span><button type="button" className="btn btn-primary btn-sm" id="pf-login" style={{ width: "100%" }} onClick={openLogin}><span>Sign in</span></button></div>
          </>
        )}
      </aside><div>
        <Group t="Appearance" extra={sw} rows={[
          { ic: IC.moon, t: "Dark mode", s: "Switch between day and night", sw: "dark", on: theme() === "dark", onToggle: () => togglePref("dark") },
          { ic: IC.blur, t: "Reduce blur", s: "Turn off blur effects to make the app smoother on older phones", sw: "noblur", on: p.noblur, onToggle: () => togglePref("noblur") },
          { ic: IC.pal, t: "Accent colour", s: "Choose your preferred accent colour", chev: false, id: "pf-acc" },
        ]} />
        <Group t="Your feed" rows={[
          { ic: IC.feed, t: "Customize feed", s: "Choose and arrange which topics appear in your feed", href: "/?c=For%20you" },
          { ic: IC.src, t: "Sources", s: "See every publisher this app aggregates news from", href: "/sources" },
          { ic: IC.bm, t: "Saved stories", s: saved + (saved === 1 ? " story" : " stories") + " saved", href: "/saved" },
          { ic: IC.lang, t: "News language", s: "Language preference for the news feed and search", sel: "lang", opts: ["English", "हिन्दी"], val: p.lang || "English", onChange: (e) => { setPref("lang", e.target.value); redraw(); } },
          { ic: IC.bell, t: "Notifications", s: "Breaking news and your daily digest", sw: "notify", on: p.notify !== false, onToggle: () => togglePref("notify") },
          { ic: IC.trash, t: "Auto-remove saved stories", s: "Clear a saved story after you have read it", sw: "autoremove", on: p.autoremove, onToggle: () => togglePref("autoremove") },
        ]} />
        <Group t="Help and about" rows={[
          { ic: IC.msg, t: "Send feedback", s: "Tell us what is broken, missing, or wrong", href: "/feedback" },
          { ic: IC.play, t: "Replay app tour", s: "A quick walkthrough of how Open Indian News works", href: "/about" },
          { ic: IC.star, t: "Rate Open Indian News", s: "Leave a review on the Play Store", href: "/download" },
          { ic: IC.lock, t: "Privacy and your data", href: "/privacy" },
          { ic: IC.info, t: "About Open Indian News", href: "/about" },
          { ic: IC.book, t: "Credits", s: "Sources, word lists, typefaces and open source software", href: "/credits" },
        ]} />
        {u ? <Group t="Account" rows={[
          { ic: IC.out, t: "Sign out", id: "pf-out", chev: false, onClick: signOut },
          { ic: IC.trash, t: "Delete account", s: "Permanently delete your account and all data stored about you", href: "/delete-account", danger: true },
        ]} /> : null}
        <p className="pf-ver">Open Indian News &middot; web prototype</p>
      </div></div>
    </main>
  );
}
