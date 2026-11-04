import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "./toast.js";

// First visit: explain notifications in-page, and only if the reader agrees show the
// browser's own prompt. Asked once; either answer is remembered in oin-notif-asked. Waits
// until the cookie choice has been made.
const KEY = "oin-notif-asked";
const seen = () => { try { return !!localStorage.getItem(KEY); } catch (e) { return false; } };
const mark = (v) => { try { localStorage.setItem(KEY, v); } catch (e) {} };
const BELL = <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0112 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.9 1.9 0 003.4 0" /></svg>;

export default function Notify() {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false);
  const opener = useRef(null), go = useRef(null);

  useEffect(() => {
    if (typeof Notification === "undefined") return undefined;
    let t;
    function tryOpen() {
      let decided = false; try { decided = !!localStorage.getItem("oin-cookie-consent"); } catch (e) { decided = true; }
      if (!decided) { t = setTimeout(tryOpen, 700); return; }
      show();
    }
    function show(force) { if (!force && seen()) return; opener.current = document.activeElement; setOpen(true); }
    // the bell on phones asks again on request, if the browser has not decided yet
    const again = () => { if (Notification.permission === "default") show(true); };
    addEventListener("oin:notifications", again);
    if (Notification.permission === "default" && !seen()) t = setTimeout(tryOpen, 1500);
    return () => { clearTimeout(t); removeEventListener("oin:notifications", again); };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    document.body.style.overflow = "hidden";
    go.current && go.current.focus();
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  if (!open) return null;
  function close(answer) { mark(answer); setOpen(false); setBusy(false); if (opener.current && opener.current.focus) opener.current.focus(); }
  function ask() {
    setBusy(true);
    const done = (perm) => {
      close(perm);
      if (perm === "granted") {
        toast("Notifications are on", 4200);
        try { new Notification("Open Indian News", { body: "You’ll get breaking news alerts and your daily digest here. No ads, ever." }); } catch (e) {}
      } else if (perm === "denied") toast("Notifications are off. You can turn them on in your browser’s site settings.", 4200);
    };
    try {
      const r = Notification.requestPermission(done);
      if (r && r.then) r.then(done, () => done("default"));
    } catch (e) { done("default"); }
  }
  return createPortal(
    <div className="np" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); close("later"); } }}>
      <div className="np-card" role="dialog" aria-modal="true" aria-labelledby="np-t">
        <div className="np-ic">{BELL}</div><h2 id="np-t">Never miss a breaking story</h2>
        <p className="np-sub">Turn on notifications and we&rsquo;ll tell you when it matters, and only then.</p>
        <ul className="np-l"><li><b>Breaking news</b><span>An alert the moment a major story breaks.</span></li>
          <li><b>Your daily digest</b><span>A summary at the times of day you choose.</span></li>
          <li><b>Stories you follow</b><span>A nudge when a story you follow moves.</span></li></ul>
        <button type="button" className="ab filled np-go" ref={go} disabled={busy} onClick={ask}>{busy ? <><i className="spin"></i><span>Waiting for your browser&hellip;</span></> : <span>Turn on notifications</span>}</button>
        <button type="button" className="auth-link np-no" onClick={() => close("later")}>Not now</button>
        <p className="np-fine">Your browser will ask you to confirm next. You can change this any time.</p>
      </div>
    </div>, document.body);
}
