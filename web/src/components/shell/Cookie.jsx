import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";

// Cookie notice: banner on first visit (Accept all / Essential only / Manage) and the full
// preferences dialog, also opened from the footer's "Cookie settings" (oin:cookie-prefs event).
// There is no switch for advertising cookies: there are none. Choice kept in oin-cookie-consent.
const KEY = "oin-cookie-consent";
function get() { try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; } }
function store(c) {
  c.essential = true; c.ts = Date.now();
  try { localStorage.setItem(KEY, JSON.stringify(c)); } catch (e) {}
  document.dispatchEvent(new CustomEvent("oin:consent", { detail: c }));
}

function Sw({ id, label, desc, on, locked, onChange }) {
  return (
    <div className="ckr"><div><b>{label}</b><span>{desc}</span></div>
      <label className={"ksw" + (locked ? " lock" : "")}><input type="checkbox" id={id} role="switch" checked={on} disabled={locked} aria-label={label} onChange={(e) => onChange && onChange(e.target.checked)} /><i></i></label></div>
  );
}

export default function Cookie() {
  const [banner, setBanner] = useState(false), [out, setOut] = useState(false), [prefs, setPrefs] = useState(false);
  const [pref, setPref] = useState(false), [ana, setAna] = useState(false);
  const opener = useRef(null), first = useRef(null);

  useEffect(() => {
    const t = setTimeout(() => { if (!get()) setBanner(true); }, 700);
    const open = () => { opener.current = document.activeElement; const c = get() || {}; setPref(!!c.preferences); setAna(!!c.analytics); setPrefs(true); };
    const link = (e) => { const a = e.target.closest && e.target.closest('a[href="#cookie-settings"]'); if (a) { e.preventDefault(); open(); } };
    addEventListener("oin:cookie-prefs", open); document.addEventListener("click", link);
    return () => { clearTimeout(t); removeEventListener("oin:cookie-prefs", open); document.removeEventListener("click", link); };
  }, []);

  useEffect(() => {
    if (!prefs) return undefined;
    document.body.style.overflow = "hidden";
    first.current && first.current.focus();
    const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); closePrefs(); } };
    document.addEventListener("keydown", esc, true);
    return () => { document.body.style.overflow = ""; document.removeEventListener("keydown", esc, true); };
  }, [prefs]);

  function closeBanner() { setOut(true); setTimeout(() => { setBanner(false); setOut(false); }, 260); }
  function closePrefs(saved) { setPrefs(false); if (!saved && opener.current && opener.current.focus) opener.current.focus(); }
  function pick(c) { store(c); closeBanner(); }
  function savePrefs(c) { store(c); closePrefs(true); if (banner) closeBanner(); }

  return (
    <>
      {banner ? (
        <section className={"ck" + (out ? " out" : "")} role="dialog" aria-label="Cookies">
          <h2 id="ck-t">Cookies, kept to the minimum</h2>
          <p>We use a few cookies and similar storage to keep you signed in and remember your choices. <b>No advertising cookies, ever.</b> <Link to="/privacy">Read how we use them</Link>.</p>
          <div className="ck-b"><button type="button" className="ab filled" onClick={() => pick({ preferences: true, analytics: true })}><span>Accept all</span></button>
            <button type="button" className="ab" onClick={() => pick({ preferences: false, analytics: false })}><span>Essential only</span></button>
            <button type="button" className="auth-link" onClick={() => { opener.current = document.activeElement; const c = get() || {}; setPref(!!c.preferences); setAna(!!c.analytics); setPrefs(true); }}>Manage</button></div>
        </section>
      ) : null}
      {prefs ? createPortal(
        <div className="ckp" onMouseDown={(e) => { if (e.target === e.currentTarget) closePrefs(); }}>
          <div className="ckp-card" role="dialog" aria-modal="true" aria-labelledby="ckp-t"><button type="button" className="auth-x" aria-label="Close" onClick={() => closePrefs()}>&times;</button>
            <h2 id="ckp-t">Cookie settings</h2><p className="ckp-sub">Choose what we may store in this browser. You can change this any time from the footer.</p>
            <Sw id="ck-ess" label="Essential" desc="Keeps you signed in and remembers this choice. Always on." on locked />
            <Sw id="ck-pref" label="Preferences" desc="Your theme, saved stories, what you follow and which notices you have dismissed." on={pref} onChange={setPref} />
            <Sw id="ck-ana" label="Analytics" desc="Anonymous counts of which pages are read, so we can improve the site. Never linked to you." on={ana} onChange={setAna} />
            <div className="ckr"><div><b>Advertising</b><span>We do not use any. There are no ads on this site, and no ad tracking.</span></div><span className="ck-never">Never</span></div>
            <div className="ckp-b"><button type="button" className="ab filled" ref={first} onClick={() => savePrefs({ preferences: pref, analytics: ana })}><span>Save my choices</span></button>
              <button type="button" className="ab" onClick={() => savePrefs({ preferences: true, analytics: true })}><span>Accept all</span></button></div>
          </div>
        </div>, document.body) : null}
    </>
  );
}
