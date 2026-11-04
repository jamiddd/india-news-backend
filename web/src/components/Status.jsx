import { useCallback, useEffect, useRef, useState } from "react";
import Skeleton from "./Skeleton.jsx";

// Runs a loader from src/api.js and tracks { data, error, loading, retry }.
// With keep: true a dependency change leaves the previous data on screen until
// the new data arrives (no flash back to the loading state, e.g. switching home
// tabs); otherwise it shows the loading state again. A late response from an
// older call is always ignored.
// With cacheKey, the last result for that key is shown immediately on the next visit
// (e.g. coming back to Home) while a fresh copy loads in the background.
const SWR = new Map();
export function useData(loader, deps = [], { keep = false, cacheKey = null } = {}) {
  const [state, setState] = useState({ key: null, data: null, error: null });
  const [n, setN] = useState(0);
  const seq = useRef(0);
  const key = JSON.stringify(deps) + "#" + n;
  useEffect(() => {
    const my = ++seq.current;
    Promise.resolve().then(loader).then(
      (data) => {
        if (cacheKey) { SWR.delete(cacheKey); SWR.set(cacheKey, data); if (SWR.size > 30) SWR.delete(SWR.keys().next().value); }
        if (my === seq.current) setState({ key, data, error: null });
      },
      (error) => { if (my === seq.current) setState({ key, data: null, error }); },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const retry = useCallback(() => setN((x) => x + 1), []);
  const fresh = state.key === key;
  const cached = !fresh && cacheKey && SWR.has(cacheKey) ? SWR.get(cacheKey) : null;
  return {
    data: fresh ? state.data : cached || (keep ? state.data : null),
    error: fresh ? state.error : null,
    loading: !fresh && !cached,
    retry,
  };
}

// Page-level loading is a shimmering skeleton; compact keeps the small inline spinner for
// loading that happens inside a page that is already on screen.
export function Loading({ what = "stories", compact = false }) {
  if (!compact) return <Skeleton what={what} />;
  return (
    <div className="endless" role="status" aria-live="polite" style={{ paddingTop: 64 }}>
      <div className="endless-load"><i className="spin"></i><span>Loading {what}{"…"}</span></div>
    </div>
  );
}

export function LoadError({ error, onRetry, what = "this page" }) {
  const offline = error && error.status === 0;
  return (
    <div className="empty" role="alert" style={{ textAlign: "center", paddingTop: 64 }}>
      <p style={{ margin: "0 0 6px", fontWeight: 600, color: "var(--ink)" }}>Couldn{"’"}t load {what}.</p>
      <p style={{ margin: "0 0 16px" }}>{offline ? "Check your connection and try again." : "The news service didn’t respond as expected" + (error && error.status ? " (" + error.status + ")" : "") + "."}</p>
      {onRetry ? <button type="button" className="more-btn" style={{ width: "auto", display: "inline-block", padding: "10px 22px" }} onClick={onRetry}>Try again</button> : null}
    </div>
  );
}

// Shorthand: the loading or error view inside a page's own <main>, or null when data is ready.
export function statusView(q, what, Wrap) {
  if (q.error && !q.data) return <Wrap><LoadError error={q.error} onRetry={q.retry} what={what} /></Wrap>;
  if (!q.data) return <Wrap><Loading what={what} /></Wrap>;
  return null;
}
