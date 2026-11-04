import { useEffect } from "react";
import { Link } from "react-router-dom";
import { TimelineCard, PastTimelines } from "../components/ContextShared.jsx";

import { useData, statusView } from "../components/Status.jsx";
import { loadTimelines, loadPastTimelines } from "../api.js";

export default function Timelines() {
  const q = useData(loadTimelines, [], { cacheKey: "timelines" }), list = q.data;
  const past = useData(loadPastTimelines, [], { cacheKey: "past-timelines" }).data;   // optional: loads after, never blocks
  useEffect(() => { document.title = "Timelines — Open Indian News"; }, []);
  const wait = statusView(q, "timelines", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <p className="meta crumb" style={{ paddingTop: 28 }}><Link to="/">&larr; Top stories</Link></p>
      <div className="feed-h"><h1>Timelines</h1><p>{list.length} threads followed from start to now</p></div>
      <div className="ctx-grid" style={{ marginTop: 18 }}>{list.map((t) => <TimelineCard key={t.id} t={t} />)}</div>
      <PastTimelines list={past} />
    </main>
  );
}
