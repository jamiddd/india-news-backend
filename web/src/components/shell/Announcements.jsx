import { useEffect, useRef, useState } from "react";
import { load, save } from "./toast.js";

// Dark strip above the navbar, one announcement at a time, highest priority first; the next
// rises in from below while the old one leaves through the top. After AnnouncementBanner.
const KEY = "oin-ann-dismissed", ACC = { offer: "#FFB300", info: "#4FA3FF", event: "#A98BFF" };
const ICON = {
  offer: <><path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0L3 13V3h10l7.6 7.6a2 2 0 010 2.8z" /><circle cx="7.5" cy="7.5" r="1.2" /></>,
  event: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.5" /></>,
};

function Row({ a, out, enter, onDismiss }) {
  const c = ACC[a.kind] || ACC.info;
  return (
    <div className={"ann-row" + (a.action_type ? " clk" : "") + (out ? " out" : "") + (enter ? " in" : "")}
      onClick={(e) => { if (e.target.closest(".ann-x")) return; const cta = e.currentTarget.querySelector(".ann-cta"); if (cta && !e.target.closest(".ann-cta")) cta.click(); }}>
      <span className="ann-bar" style={{ background: c }}></span>
      <svg className="ann-ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON[a.kind] || ICON.info}</svg>
      <div className="ann-txt"><b>{a.title}</b>{a.body ? <span>{a.body}</span> : null}</div>
      {a.cta_label && a.action_type ? <a className="ann-cta" href={a.action_value || "#"} style={{ color: c, background: c + "2E" }}>{a.cta_label}</a> : null}
      <button type="button" className="ann-x" aria-label="Dismiss" onClick={onDismiss}>&times;</button>
    </div>
  );
}

export default function Announcements({ items }) {
  const [gone, setGone] = useState(() => load(KEY, []));
  const [leaving, setLeaving] = useState(null), [entering, setEntering] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const queue = (items || []).filter((a) => gone.indexOf(a.id) < 0).sort((a, b) => b.priority - a.priority);
  if (!queue.length && !leaving) return null;

  function dismiss(a) {
    const next = gone.concat(a.id);
    save(KEY, next);
    const nextItem = queue[1];
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) { setGone(next); return; }
    setLeaving(a); setEntering(nextItem ? nextItem.id : null); setGone(next);
    timer.current = setTimeout(() => { setLeaving(null); setEntering(null); }, 320);
  }
  return (
    <section className="ann" aria-label="Announcements"><div className="ann-in"><div className="ann-stage">
      {leaving ? <Row key={"o" + leaving.id} a={leaving} out onDismiss={() => {}} /> : null}
      {queue[0] ? <Row key={queue[0].id} a={queue[0]} enter={entering === queue[0].id} onDismiss={() => dismiss(queue[0])} /> : null}
    </div></div></section>
  );
}
