import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

// Scroll behaviour of an app: opening a page (link, tab, new search) starts at the top; going back
// or forward returns to where that page was. The browser can't do the second part by itself
// because pages fill in after the data arrives, so the position is saved per history entry and
// re-applied once the page is tall enough. Kept in sessionStorage so a reload keeps it too.
const KEY = "oin-scroll", GRACE_MS = 250, GIVE_UP_MS = 2500;
const read = () => { try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch (e) { return {}; } };
const write = (m) => {
  const keys = Object.keys(m);
  if (keys.length > 80) keys.slice(0, keys.length - 80).forEach((k) => { delete m[k]; });   // newest 80 entries
  try { sessionStorage.setItem(KEY, JSON.stringify(m)); } catch (e) { /* private mode */ }
};

export default function ScrollManager() {
  const loc = useLocation(), type = useNavigationType();
  const positions = useRef(null);
  if (positions.current === null) positions.current = read();

  useEffect(() => { if ("scrollRestoration" in history) history.scrollRestoration = "manual"; }, []);

  // Where to land on this entry, before the browser paints it.
  useLayoutEffect(() => {
    const target = type === "POP" ? positions.current[loc.key] : undefined;
    if (loc.hash) return undefined;                       // #coverage etc.: the page scrolls to it itself
    if (!target) { scrollTo(0, 0); return undefined; }
    // Pages fill in after their data arrives: keep trying until tall enough, unless the reader
    // takes over by scrolling.
    let stop = false;
    const t0 = performance.now();
    const cancel = () => { stop = true; };
    addEventListener("wheel", cancel, { passive: true }); addEventListener("touchstart", cancel, { passive: true }); addEventListener("keydown", cancel);
    const step = () => {
      if (stop) return;
      const max = document.documentElement.scrollHeight - innerHeight;
      scrollTo(0, Math.min(target, Math.max(0, max)));
      if (Math.abs(scrollY - target) < 2 || performance.now() - t0 > GIVE_UP_MS) return;
      requestAnimationFrame(step);
    };
    step();
    return () => { stop = true; removeEventListener("wheel", cancel); removeEventListener("touchstart", cancel); removeEventListener("keydown", cancel); };
  }, [loc.key]);

  // Remember how far each entry was scrolled. Events right after a navigation are ignored: the
  // old page vanishing makes the browser clamp the scroll, which isn't the reader's position.
  useEffect(() => {
    const born = performance.now();
    let timer;
    const save = () => {
      if (performance.now() - born < GRACE_MS) return;
      clearTimeout(timer);
      timer = setTimeout(() => { positions.current[loc.key] = Math.round(scrollY); write(positions.current); }, 60);
    };
    addEventListener("scroll", save, { passive: true });
    return () => { removeEventListener("scroll", save); clearTimeout(timer); };
  }, [loc.key]);

  return null;
}
