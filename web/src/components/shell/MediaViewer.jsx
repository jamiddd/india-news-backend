import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Fullscreen media viewer (previous/next, counter, close, arrows + Esc).
export function Viewer({ items, start, onClose }) {
  const [at, setAt] = useState(start), n = items.length, it = items[at];
  const x = useRef(null), opener = useRef(document.activeElement);
  useEffect(() => {
    x.current && x.current.focus();
    document.body.style.overflow = "hidden";
    const key = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
      else if (e.key === "ArrowLeft") setAt((a) => Math.max(0, a - 1));
      else if (e.key === "ArrowRight") setAt((a) => Math.min(n - 1, a + 1));
    };
    document.addEventListener("keydown", key, true);
    return () => {
      document.body.style.overflow = ""; document.removeEventListener("keydown", key, true);
      const o = opener.current; if (o && o.focus) o.focus();
    };
  }, []);
  const arrow = (d) => <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
  return createPortal(
    <div className="mvw" role="dialog" aria-modal="true" aria-label="Media viewer" onClick={(e) => { if (e.target.classList.contains("mvw") || e.target.classList.contains("mvw-stage")) onClose(); }}>
      <div className="mvw-top"><span className="mvw-count" hidden={n < 2}>{at + 1} / {n}</span><span className="mvw-cap">{it.video ? "Video" : "Image"}</span><button type="button" className="mvw-x" aria-label="Close" ref={x} onClick={onClose}>&times;</button></div>
      <button type="button" className="mvw-nav mvw-prev" aria-label="Previous" hidden={n < 2 || at === 0} onClick={() => setAt(at - 1)}>{arrow("M15 5l-7 7 7 7")}</button>
      <div className="mvw-stage"><div className="mvw-media ph">{it.video && <span className="car-play big"><svg width="40" height="40" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z" /></svg></span>}{it.label}</div></div>
      <button type="button" className="mvw-nav mvw-next" aria-label="Next" hidden={n < 2 || at === n - 1} onClick={() => setAt(at + 1)}>{arrow("M9 5l7 7-7 7")}</button>
    </div>, document.body);
}

// Global opener for media boxes that have no handler of their own (the `.mv` placeholders
// on Timeline/Explainer pages). Components that handle their own click call preventDefault.
export function MediaFullscreen() {
  const [view, setView] = useState(null);
  useEffect(() => {
    function collect(from) {
      const car = from.closest(".car");
      if (car) {
        const slides = [].slice.call(car.querySelectorAll(".car-slide"));
        return { items: slides.map((sl) => ({ video: !!sl.querySelector(".car-play"), label: sl.textContent.trim() })), at: slides.indexOf(from.closest(".car-slide")) };
      }
      return { items: [{ video: !!from.querySelector(".car-play"), label: from.textContent.trim() }], at: 0 };
    }
    const click = (e) => {
      if (e.defaultPrevented || e.target.closest(".mvw, .car-btn, .car-dot")) return;
      const t = e.target.closest(".car-slide, .mv");
      if (t) setView(collect(t));
    };
    const key = (e) => {
      if (e.defaultPrevented || (e.key !== "Enter" && e.key !== " ")) return;
      if (e.target.classList && e.target.classList.contains("mv")) { e.preventDefault(); setView(collect(e.target)); }
    };
    document.addEventListener("click", click); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("click", click); document.removeEventListener("keydown", key); };
  }, []);
  return view ? <Viewer items={view.items} start={view.at} onClose={() => setView(null)} /> : null;
}
