import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useShell } from "./ShellContext.jsx";
import Mplay, { useMplayRoot } from "./Mplay.jsx";

// Phone chrome after the Android app: a top app bar (logo, a title that swaps to today's date
// after five seconds, day/night toggle, search) that slides away on scroll down, and a floating
// pill of icon tabs at the bottom. Both are hidden by CSS above 700px.
const fsvg = (inner) => <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{inner}</svg>;
const svg = (inner) => <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{inner}</svg>;
const IC = {
  sun: svg(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>),
  moon: svg(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />),
  search: svg(<><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></>),
  home: fsvg(<><path fillRule="evenodd" d="M4.8 2.88H16.32L21.12 7.68V19.2Q21.12 21.12 19.2 21.12H4.8Q2.88 21.12 2.88 19.2V4.8Q2.88 2.88 4.8 2.88Z M5.91 5.8H10.43A.84.84 0 0 1 11.27 6.64V11.16A.84.84 0 0 1 10.43 12H5.91A.84.84 0 0 1 5.07 11.16V6.64A.84.84 0 0 1 5.91 5.8Z" /><path className="fold" d="M16.32 2.88L21.12 7.68H16.32Z" opacity=".55" /></>),
  watch: fsvg(<><path fillRule="evenodd" d="M5.5 6H18.5A3.5 3.5 0 0 1 22 9.5V17.5A3.5 3.5 0 0 1 18.5 21H5.5A3.5 3.5 0 0 1 2 17.5V9.5A3.5 3.5 0 0 1 5.5 6Z M10 10V17L16 13.5Z" /><path d="M12 6L8 2.8M12 6L16 2.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" fill="none" /></>),
  timelines: fsvg(<><rect x="2.88" y="3.84" width="8.4" height="16.8" rx="2.4" /><rect className="pg" x="12.72" y="3.84" width="8.4" height="16.8" rx="2.4" /></>),
  profile: fsvg(<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />),
};

function useDark() {
  const get = () => (document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const [dark, setDark] = useState(get);
  useEffect(() => {
    const sync = () => setDark(get());
    const mo = new MutationObserver(sync); mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = matchMedia("(prefers-color-scheme: dark)"); mq.addEventListener("change", sync);
    return () => { mo.disconnect(); mq.removeEventListener("change", sync); };
  }, []);
  return dark;
}
function usePhone() {
  const [p, setP] = useState(() => matchMedia("(max-width:700px)").matches);
  useEffect(() => { const mq = matchMedia("(max-width:700px)"); const f = () => setP(mq.matches); mq.addEventListener("change", f); return () => mq.removeEventListener("change", f); }, []);
  return p;
}

export function AppBar({ brand, onTheme }) {
  const [showDate, setShowDate] = useState(false), [hide, setHide] = useState(false);
  const dark = useDark(), nav = useNavigate();
  const now = new Date(), day = now.toLocaleDateString("en-IN", { weekday: "long" }), dm = now.toLocaleDateString("en-IN", { day: "numeric", month: "long" });
  useEffect(() => {
    const t = setTimeout(() => setShowDate(true), 5000);
    let last = scrollY;
    const onScroll = () => {
      const y = scrollY, down = y > last + 6, up = y < last - 6;
      if (down && y > 90) setHide(true); else if (up || y < 90) setHide(false);
      if (down || up) last = y;
    };
    addEventListener("scroll", onScroll, { passive: true });
    return () => { clearTimeout(t); removeEventListener("scroll", onScroll); };
  }, []);
  function search() { const so = document.getElementById("search-open"); if (so) so.click(); else nav("/?s=1"); }
  return (
    <header className={"appbar" + (showDate ? " show-date" : "") + (hide ? " hide" : "")}>
      <Link className="ab-brand" to="/" aria-label="Open Indian News, top of the feed"><span className="ph round ab-logo" aria-hidden="true"></span>
        <span className="ab-title"><span className="ab-t1"><b>{brand.name}</b><small>Multi-Outlet Clustered Feed &amp; Framing Intelligence</small></span>
          <span className="ab-t2"><b>{day}</b><small>{dm}</small></span></span></Link>
      <div className="ab-act">
        <button type="button" className="ab-i" id="ab-theme" aria-label={dark ? "Switch to day mode" : "Switch to night mode"} onClick={onTheme}>{dark ? IC.sun : IC.moon}</button>
        <button type="button" className="ab-i" id="ab-search" aria-label="Search" onClick={search}>{IC.search}</button>
      </div>
    </header>
  );
}

export function TabBar() {
  const { pathname } = useLocation(), sh = useShell(), nav = useNavigate(), phone = usePhone();
  const ctx = /^\/(context|timeline|explainer)(\/|$)/.test(pathname);
  const on = ctx ? "context" : pathname === "/" ? "home" : pathname === "/watch" ? "watch" : pathname === "/profile" ? "profile" : "";
  useMplayRoot(!!sh.player);
  const tab = (key, to, label, icon) => <Link className={"tb" + (on === key ? " on" : "")} data-tab={key} to={to} aria-label={label} aria-current={on === key ? "page" : undefined}>{icon}<span className="tb-l">{label}</span></Link>;
  const u = sh.session;
  return (
    <>
      <nav className="tabbar" aria-label="Main">
        {phone ? <Mplay /> : null}
        <div className="tb-row">
          {tab("home", "/", "Home", IC.home)}
          {tab("watch", "/watch", "Watch", IC.watch)}
          {tab("context", "/context", "Context", IC.timelines)}
          <button type="button" className={"tb" + (on === "profile" ? " on" : "") + (u ? " sess" : "")} data-tab="profile" id="tb-profile" aria-label={u ? "Profile: " + u.name : "Sign in"} aria-current={on === "profile" ? "page" : undefined} onClick={() => nav("/profile")}>
            {u ? <span className="tb-av" aria-hidden="true">{(u.name || u.email || "?").trim().charAt(0).toUpperCase()}</span> : IC.profile}<span className="tb-l">Profile</span></button>
        </div>
      </nav>
      {phone ? null : <Mplay />}
    </>
  );
}
