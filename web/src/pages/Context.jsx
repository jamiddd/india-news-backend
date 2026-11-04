import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { TimelineCard, ExplainerCard } from "../components/ContextShared.jsx";

import { LoadError, useData } from "../components/Status.jsx";
import { ContextSkeleton } from "../components/Skeleton.jsx";
import { loadContext } from "../api.js";

// Context: the timelines and explainers behind the stories.
export default function Context() {
  const q = useData(loadContext), data = q.data;
  const [qs] = useSearchParams();
  useEffect(() => { document.title = "Context — Open Indian News"; }, []);
  const tab = qs.get("t") === "explainers" ? "explainers" : "timelines";
  const list = data ? data[tab] : [];
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <div className="feed-h ctx-h"><h1>Context</h1><p>The story behind the story</p></div>
      <div className="ctx-tabs" role="tablist" aria-label="Context sections">
        {["timelines", "explainers"].map((t) => (
          <Link key={t} role="tab" aria-selected={t === tab} className={"ctx-tab" + (t === tab ? " on" : "")} to={"/context?t=" + t}>{t === "timelines" ? "Timelines" : "Explainers"}</Link>
        ))}
      </div>
      {q.error ? <LoadError error={q.error} onRetry={q.retry} what={tab} /> : !data ? <div className="skel sk-in" role="status" aria-label={"Loading " + tab}><ContextSkeleton /></div> : list.length ? (
        <div className="ctx-grid">{list.map((x) => (tab === "timelines" ? <TimelineCard key={x.id} t={x} /> : <ExplainerCard key={x.id} x={x} />))}</div>
      ) : <p className="empty">No {tab} yet.</p>}
    </main>
  );
}
