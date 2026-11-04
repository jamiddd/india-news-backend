import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Ph, SrcRow, ago, savedIds } from "../components/ContextShared.jsx";

import { useData, statusView } from "../components/Status.jsx";
import { loadStoriesByIds } from "../api.js";

// Saved ids stay in localStorage (no signed-in web session); each is looked up by id.
const loadStories = () => loadStoriesByIds(savedIds());

export default function Saved() {
  const q = useData(loadStories, [], { cacheKey: "saved-stories" }), stories = q.data;
  const [, bump] = useState(0);
  useEffect(() => { document.title = "Saved stories — Open Indian News"; }, []);
  const wait = statusView(q, "your saved stories", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  const ids = savedIds();
  const list = stories.filter((s) => ids.indexOf(s.id) > -1);
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <p className="meta crumb" style={{ paddingTop: 28 }}><Link to="/">&larr; Top stories</Link></p>
      <div className="feed-h"><h1>Saved</h1><p>{list.length} stor{list.length === 1 ? "y" : "ies"}</p></div>
      {list.length ? (
        <div className="cards flat" id="grid">
          {list.map((x) => (
            <div className="c" key={x.id}><Ph ratio="3/2" src={x.image} /><div className="meta"><span className="cat">{x.category}</span><span>{ago(x.minutes_ago)} ago</span></div>
              <h3><Link to={"/story/" + x.id}>{x.headline}</Link></h3><SrcRow s={x} onChange={() => bump((n) => n + 1)} /></div>
          ))}
        </div>
      ) : <p className="empty" style={{ paddingTop: 32 }}>Nothing saved yet. Tap the bookmark on any story to keep it here. <Link to="/">Browse top stories</Link></p>}
    </main>
  );
}
