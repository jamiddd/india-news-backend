import { useEffect, useRef, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { brand, nav, footer } from "./brand.js";
import { toggleSaved } from "./components/home/shared.jsx";
import { useData } from "./components/Status.jsx";
import { loadAnnouncements } from "./api.js";
import { ShellProvider, useShell } from "./components/shell/ShellContext.jsx";
import SignIn, { AccountMenu } from "./components/shell/SignIn.jsx";
import Announcements from "./components/shell/Announcements.jsx";
import Cookie from "./components/shell/Cookie.jsx";
import Notify from "./components/shell/Notify.jsx";
import { AppBar, TabBar } from "./components/shell/AppChrome.jsx";
import OinMark from "./components/OinMark.jsx";
import { MediaFullscreen } from "./components/shell/MediaViewer.jsx";
import GlobalActions from "./components/shell/GlobalActions.jsx";

const THEME_KEY = "oin-theme", NOADS_KEY = "oin-noads-dismissed";

function NavLinks({ list }) {
  return list.map(([label, href]) =>
    href.startsWith("/") ? <Link key={label} to={href}>{label}</Link> : <a key={label} href={href}>{label}</a>
  );
}

function toggleTheme() {
  const root = document.documentElement;
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const cur = root.getAttribute("data-theme") || (dark ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
}

// The header's account button: opens the sign-in modal, or the account menu once signed in.
function UserButton() {
  const sh = useShell(), ref = useRef(null), [menu, setMenu] = useState(false), u = sh.session;
  useEffect(() => { if (!u) setMenu(false); }, [u]);
  return (
    <>
      <button ref={ref} className={"user-btn" + (u ? " on" : "")} id="user" aria-label={u ? "Account: " + u.name : "Sign in"} aria-haspopup={u ? "menu" : "dialog"} aria-expanded={u ? menu : undefined}
        onClick={() => { if (u) setMenu(!menu); else sh.openLogin(); }}>
        {u ? <span aria-hidden="true">{(u.name || u.email || "?").trim().charAt(0).toUpperCase()}</span> : (
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" /></svg>
        )}
      </button>
      {menu && u ? <AccountMenu anchor={ref.current} onClose={() => setMenu(false)} /> : null}
    </>
  );
}

function NoAds() {
  const [shown, setShown] = useState(() => { try { return !localStorage.getItem(NOADS_KEY); } catch (e) { return true; } });
  if (!shown) return null;
  return (
    <div className="signal-bar"><div className="wrap"><span className="tag">NO ADS</span><span>The only news app with no ads. Anywhere.</span>
      <button type="button" className="signal-x" aria-label="Dismiss" onClick={() => { try { localStorage.setItem(NOADS_KEY, "1"); } catch (e) {} setShown(false); }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button></div></div>
  );
}

function Shell() {
  const ann = useData(loadAnnouncements), sh = useShell();   // optional: a failure just shows none
  return (
    <>
      <Announcements items={ann.data} />
      <header className="masthead"><div className="wrap">
        <Link className="brand" to="/">
          <OinMark size={30} tone="dark" />
          <span className="brand-text">
            <span className="wordmark">{brand.name}<span className="stop">.</span></span>
          </span>
        </Link>
        <nav className="links"><NavLinks list={nav} /></nav>
        <Link className="btn btn-primary btn-sm get-app" to="/download"><span>Get the app</span></Link>
        <button className="theme-btn" id="theme" aria-label="Toggle dark mode" onClick={toggleTheme}>&#9680;</button>
        <UserButton />
      </div></header>
      <AppBar brand={brand} onTheme={toggleTheme} />
      <NoAds />
      <Outlet key={(sh.session ? sh.session.email : "") + ":" + sh.dataEpoch} />
      <footer><div className="wrap">
        <span>&copy; {new Date().getFullYear()} {brand.name}</span>
        <nav><NavLinks list={footer} /></nav>
      </div></footer>
      <TabBar />
      <SignIn />
      <Cookie />
      <Notify />
      <MediaFullscreen />
      <GlobalActions toggleSaved={toggleSaved} />
    </>
  );
}

export default function Layout() {
  useEffect(() => {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved) document.documentElement.setAttribute("data-theme", saved);
    } catch (e) {}
  }, []);
  return <ShellProvider><Shell /></ShellProvider>;
}
