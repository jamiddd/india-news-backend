import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ExplainerCard } from "../components/ContextShared.jsx";

import { useData, statusView } from "../components/Status.jsx";
import { loadExplainers } from "../api.js";

export default function Explainers() {
  const q = useData(loadExplainers), list = q.data;
  useEffect(() => { document.title = "Explainers — Open Indian News"; }, []);
  const wait = statusView(q, "explainers", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <p className="meta crumb" style={{ paddingTop: 28 }}><Link to="/">&larr; Top stories</Link></p>
      <div className="feed-h"><h1>Explainers</h1><p>{list.length} plain-language answers</p></div>
      <div className="ctx-grid" style={{ marginTop: 18 }}>{list.map((x) => <ExplainerCard key={x.id} x={x} />)}</div>
    </main>
  );
}
