import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import css from "../components/ProfileStyles.js";
import { ago } from "../components/ContextShared.jsx";

import { Loading, LoadError, useData } from "../components/Status.jsx";
import { loadStoriesByIds } from "../api.js";

// Saved and history ids stay in localStorage (no signed-in web session); the newest of
// each are looked up by id, as there is no batch endpoint.
const loadStories = () => {
  const ids = ls("oin-saved").slice(-20).concat(ls("oin-read").slice(-20));
  return loadStoriesByIds(ids.filter((x, i) => ids.indexOf(x) === i), { max: 40 });
};

function sess() { try { return JSON.parse(localStorage.getItem("oin-session")); } catch (e) { return null; } }
function ls(k) { try { return JSON.parse(localStorage.getItem(k)) || []; } catch (e) { return []; } }
function label(k) { return k.split(":")[1].replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
function openLogin() { const b = document.getElementById("user"); if (b) b.click(); }

function Row({ st, kind, onUnsave }) {
  if (!st) return null;
  return (
    <div className="pr-rw"><div className="th" aria-hidden="true"></div><div className="tx"><Link to={"/story/" + st.id}>{st.headline}</Link><small>{st.category} &middot; {ago(st.minutes_ago)} ago</small></div>
      {kind === "saved" ? <button type="button" className="rm" aria-label="Remove from saved" onClick={() => onUnsave(st.id)}><svg width="20" height="20" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"><path d="M6 3h12v18l-6-4.5L6 21z" /></svg></button> : null}</div>
  );
}

export default function Profile() {
  const q = useData(loadStories), stories = q.data;
  const [, setTick] = useState(0);
  const redraw = useCallback(() => setTick((n) => n + 1), []);
  const [editing, setEditing] = useState(false);
  const [flash, setFlash] = useState(false);
  const [tab, setTab] = useState(0);
  const [name, setName] = useState("");
  const pg = useRef(null);
  const nameRef = useRef(null);
  const tabRef = useRef(0);
  tabRef.current = tab;

  useEffect(() => { document.title = "Profile — Open Indian News"; }, []);
  useEffect(() => {
    const ub = document.getElementById("user");
    if (!ub) return undefined;
    const mo = new MutationObserver(redraw);
    mo.observe(ub, { childList: true, attributes: true, attributeFilter: ["aria-label"] });
    return () => mo.disconnect();
  }, [redraw]);

  const fit = useCallback(() => {
    const el = pg.current; if (!el) return;
    const pane = el.children[tabRef.current]; if (pane) el.style.height = pane.scrollHeight + "px";
  }, []);
  useEffect(() => { addEventListener("resize", fit); return () => removeEventListener("resize", fit); }, [fit]);
  // after every render: keep the pager on the current tab and sized to its pane
  useLayoutEffect(() => {
    const el = pg.current; if (!el) return;
    el.scrollLeft = tabRef.current * el.clientWidth; fit();
  });

  function onScroll() {
    const el = pg.current;
    const n = el.scrollLeft > el.clientWidth / 2 ? 1 : 0;
    if (n !== tab) setTab(n);
  }
  function goTab(n) { pg.current.scrollTo({ left: n * pg.current.clientWidth, behavior: "smooth" }); }
  function unsave(id) {
    const l = ls("oin-saved").filter((x) => x !== id);
    try { localStorage.setItem("oin-saved", JSON.stringify(l)); } catch (x) {}
    redraw();
  }
  function clearHistory() { try { localStorage.removeItem("oin-read"); } catch (x) {} redraw(); }
  function signOut() { try { localStorage.removeItem("oin-session"); } catch (x) {} location.reload(); }
  function toggleEdit() { const u = sess(); setName((u && u.name) || ""); setEditing(!editing); setFlash(false); }
  function submit(e) {
    e.preventDefault();
    const u = sess(), n = name.trim();
    if (!u || !n) { if (nameRef.current) nameRef.current.focus(); return; }
    u.name = n;
    try {
      localStorage.setItem("oin-session", JSON.stringify(u));
      const a = JSON.parse(localStorage.getItem("oin-accounts")) || [];
      a.forEach((x) => { if (x.email === u.email) x.name = n; });
      localStorage.setItem("oin-accounts", JSON.stringify(a));
    } catch (x) {}
    setEditing(false); setFlash(true);
  }

  const u = sess();
  const style = <style>{css}</style>;
  if (!u) {
    return (
      <main className="wide pr" id="page">{style}
        <div className="pr-guest"><div className="pr-av guest" style={{ margin: "0 auto" }} aria-hidden="true"><svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" /></svg></div>
          <h1>Welcome, Reader</h1><p>Sign in to save stories, follow sources and sync across devices.</p><button type="button" className="btn btn-primary" id="pr-login" onClick={openLogin}><span>Sign in or create an account</span></button></div>
      </main>
    );
  }

  const savedList = ls("oin-saved"), hist = ls("oin-read"), fol = ls("oin-follow");
  const srcs = fol.filter((k) => k.indexOf("source:") === 0), tops = fol.filter((k) => k.indexOf("topic:") === 0);
  const byId = {}; (stories || []).forEach((s) => { byId[s.id] = s; });
  const rows = (ids, kind) => ids.slice().reverse().map((i) => <Row key={kind + i} st={byId[i]} kind={kind} onUnsave={unsave} />);
  const sv = rows(savedList, "saved"), hs = rows(hist, "hist");

  return (
    <main className="wide pr" id="page">{style}
      <div className="pr-hero"><div className="pr-av" aria-hidden="true">{(u.name || u.email || "?").trim().charAt(0).toUpperCase()}</div><div className="pr-who"><h1>{u.name || "Reader"}</h1><div className="em">{u.email || ""}</div><div className="jn">Joined {u.joined || "this month"}</div></div>
        <button type="button" className="btn btn-primary btn-sm" id="pr-edit" onClick={toggleEdit}><span>{editing ? "Cancel" : "Edit profile"}</span></button></div>
      {editing ? (
        <form className="pr-sec" id="pr-form" onSubmit={submit}><div className="pr-card"><div className="pr-f"><label htmlFor="pr-name">Display name</label><input id="pr-name" ref={nameRef} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} autoComplete="name" /></div>
          <div className="pr-f"><label htmlFor="pr-mail">Email</label><input id="pr-mail" value={u.email || ""} readOnly /><small>Your email is how you sign in, so it can&rsquo;t be changed here.</small></div>
          <div className="pr-act"><button type="submit" className="btn btn-primary btn-sm"><span>Save changes</span></button></div></div></form>
      ) : null}
      {flash ? <p className="pr-ok" role="status" style={{ margin: "12px 4px 0" }}>Profile updated</p> : null}
      {q.error ? <LoadError error={q.error} onRetry={q.retry} what="your stories" /> : !stories && (savedList.length || hist.length) ? <Loading what="your stories" /> : null}
      <div className={"pr-tabs" + (tab ? " h" : "")} role="tablist"><i></i><div>
        <button type="button" role="tab" className={tab ? "" : "on"} onClick={() => goTab(0)}>Saved</button>
        <button type="button" role="tab" className={tab ? "on" : ""} onClick={() => goTab(1)}>History</button></div></div>
      <div className="pr-pg" id="pr-pg" ref={pg} onScroll={onScroll}>
        <div className="pr-pane"><h2 className="pr-ph">Saved<span>{savedList.length}</span></h2>{sv.length ? sv : <p className="pr-none">Nothing saved yet. Tap the bookmark on any story to keep it here.</p>}</div>
        <div className="pr-pane"><h2 className="pr-ph">History<span>{hist.length}</span></h2>{hs.length ? <>{hs}<button type="button" className="pr-clear" id="pr-clear" onClick={clearHistory}>Clear history</button></> : <p className="pr-none">Stories you open will show up here.</p>}</div>
      </div>
      <div className="pr-stats"><Link className="pr-stat" to="/saved"><b>{savedList.length}</b><span>Saved</span></Link><Link className="pr-stat" to="/sources"><b>{srcs.length}</b><span>Sources</span></Link><Link className="pr-stat" to="/sources"><b>{tops.length}</b><span>Topics</span></Link></div>
      <section className="pr-sec"><h2>Following<Link to="/sources">Find sources &rarr;</Link></h2>
        {fol.length ? <div className="pr-chips">{fol.map((k) => { const s = k.split(":"); return <Link key={k} className="pr-chip" to={"/" + s[0] + "/" + s[1]}>{label(k)}<small>{s[0]}</small></Link>; })}</div>
          : <p className="pr-empty">You aren&rsquo;t following anything yet. Follow a source or topic and its stories will show up first.</p>}</section>
      <section className="pr-sec"><h2>Account</h2><div className="pr-links"><button type="button" className="pr-out" id="pr-out" onClick={signOut}>Sign out</button><Link className="pr-del" to="/delete-account">Delete account</Link></div></section>
    </main>
  );
}
