import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { AiBadge, AudioPlayer } from "../components/ContextShared.jsx";

import { useData, statusView } from "../components/Status.jsx";
import { loadExplainerPage } from "../api.js";

// "Outlet A, Outlet B and N others", as the app attributes a multi-outlet story
function attribution(s) {
  const names = s.coverage.map((v) => v.outlet).filter((n, i, a) => a.indexOf(n) === i);
  if (names.length <= 1) return names[0] || "News Desk";
  const extra = s.outlet_count - 2;
  return names[0] + ", " + names[1] + (extra > 0 ? " and " + extra + " other" + (extra > 1 ? "s" : "") : "");
}

export default function Explainer() {
  const { id: rawId } = useParams();
  const id = +rawId;
  const q = useData(() => loadExplainerPage(id), [id]), d = q.data;
  const x = d && d.explainers.filter((e) => e.id === id)[0];
  useEffect(() => { if (x) document.title = x.title + " — Open Indian News"; }, [x]);
  const wait = statusView(q, "this explainer", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  if (!x) return <main className="wide" style={{ paddingBottom: 72 }}><p className="empty" style={{ paddingTop: 48 }}>Explainer not found. <Link to="/">Back to top stories</Link></p></main>;

  const byId = {}; d.stories.forEach((s) => { byId[s.id] = s; });
  const srcs = (x.sources || []).map((sid) => byId[sid]).filter(Boolean);
  const more = d.explainers.filter((e) => e.id !== x.id && e.category === x.category)
    .concat(d.explainers.filter((e) => e.id !== x.id && e.category !== x.category)).slice(0, 3);

  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <article className="sty"><div className="sty-main">
        <p className="meta crumb"><Link to="/">&larr; Top stories</Link><span className="cat" style={{ color: "var(--hue-context)" }}>Explainer</span><Link className="cat" to={"/?c=" + encodeURIComponent(x.category)}>{x.category}</Link><span>{x.updated || "Updated 4h ago"}</span><AiBadge /></p>
        <h1>{x.title}</h1>
        <div className="sty-media"><div className="ph mv" role="button" tabIndex={0} aria-label="Open media fullscreen" style={{ aspectRatio: "16/9" }}>image</div></div>
        <div style={{ marginTop: 14 }}>{x.audio || !("audio" in x) ? <AudioPlayer id={x.id} kind="explainer" title={x.title} url={"/explainer/" + x.id} label="Listen to this explainer" duration={(x.audio && x.audio.duration) || 150 + (x.id % 7) * 20} /> : null}</div>
        <div className="quick"><b>QUICK ANSWER</b><p>{x.quick || x.dek}</p></div>
        {(x.sections || []).map((sec, i) => (
          <section key={i} className="ex-sec"><h2>{sec.heading}</h2>{sec.body.map((p, j) => <p key={j}>{p}</p>)}</section>
        ))}
        {srcs.length ? (
          <section className="sty-sec"><h2 className="sec-h"><span>Sources</span><span className="sec-n">{srcs.length} real stor{srcs.length === 1 ? "y" : "ies"}</span></h2><div className="srcl">
            {srcs.map((s) => (
              <Link key={s.id} className="srcl-r" to={"/story/" + s.id}><span className="favs"><i className="fv"></i><i className="fv"></i></span><span className="srcl-t"><small>{attribution(s)}</small><b>{s.headline}</b></span></Link>
            ))}
          </div></section>
        ) : null}
        {more.length ? (
          <section className="sty-sec"><h2 className="sec-h"><span>More explainers</span></h2><div className="latest">
            {more.map((e) => <div key={e.id} className="hv"><Link to={"/explainer/" + e.id}><small>{e.category.toUpperCase()} &middot; {e.read}</small>{e.title}</Link></div>)}
          </div></section>
        ) : null}
      </div></article>
    </main>
  );
}
