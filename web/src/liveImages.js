import { useEffect, useMemo, useState } from "react";
import { imageInfo } from "./api.js";

// Which story pictures actually load. The feed only says a URL exists; publishers' links rot.
// Each story's lead picture is probed (then its alternates), and a story with none that loads
// comes back with image: null, so the front page deals its picture slot to the next-ranked
// story and shows the dead one as text only. Results are remembered for the session.
const known = new Map();        // url -> true | false
const pending = new Map();      // url -> Promise<boolean>
const PROBE_MS = 8000;

function probe(url) {
  if (known.has(url)) return Promise.resolve(known.get(url));
  if (!pending.has(url)) {
    pending.set(url, new Promise((done) => {
      const im = new Image();
      const end = (ok) => { clearTimeout(t); known.set(url, ok); pending.delete(url); done(ok); };
      const t = setTimeout(() => end(true), PROBE_MS);   // slow is not broken: leave it to <Ph>
      im.referrerPolicy = "no-referrer";
      im.onload = () => end(true);
      im.onerror = () => end(false);
      im.src = url;
    }));
  }
  return pending.get(url);
}

const candidates = (s) => (s.image ? [s.image].concat((imageInfo(s.image) || { alts: [] }).alts) : []);

// First candidate known to load, else null; undefined while still unknown.
function resolved(s) {
  const c = candidates(s);
  for (let i = 0; i < c.length; i++) {
    const k = known.get(c[i]);
    if (k === undefined) return undefined;
    if (k) return c[i];
  }
  return c.length ? null : null;
}

// stories -> the same stories with dead pictures dropped (a new object only where it changed).
export function useLiveImages(stories, limit = 60) {
  const [, bump] = useState(0);
  useEffect(() => {
    let live = true;
    (stories || []).slice(0, limit).forEach((s) => {
      const c = candidates(s);
      // Try in order, stopping at the first that loads.
      (async () => { for (let i = 0; i < c.length; i++) { if (await probe(c[i])) break; } if (live) bump((n) => n + 1); })();
    });
    return () => { live = false; };
  }, [stories, limit]);
  return (stories || []).map((s) => {
    if (!s.image) return s;
    const r = resolved(s);
    return r === undefined || r === s.image ? s : { ...s, image: r };
  });
}
