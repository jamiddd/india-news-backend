import { useEffect } from "react";
import { Ph } from "../components/Photo.jsx";
import { Link, useParams } from "react-router-dom";
import { AiBadge, AudioPlayer, GetTheApp, ago } from "../components/ContextShared.jsx";

import { useData, statusView } from "../components/Status.jsx";
import { loadTimelinePage } from "../api.js";

export default function Timeline() {
  const { id: rawId } = useParams();
  const id = +rawId;
  const q = useData(() => loadTimelinePage(id), [id]), d = q.data;
  const t = d && d.timelines.filter((x) => x.id === id)[0];
  useEffect(() => { if (t) document.title = t.title + " — Open Indian News"; }, [t]);
  const wait = statusView(q, "this timeline", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  if (d && d.locked) return <main className="wide" style={{ paddingBottom: 72, minHeight: "70vh" }}><GetTheApp full /></main>;
  if (!t) return <main className="wide" style={{ paddingBottom: 72 }}><p className="empty" style={{ paddingTop: 48 }}>Timeline not found. <Link to="/">Back to top stories</Link></p></main>;

  const byId = {}; d.stories.forEach((s) => { byId[s.id] = s; });
  const beats = t.beats || [];
  const more = d.timelines.filter((x) => x.id !== t.id && x.category === t.category)
    .concat(d.timelines.filter((x) => x.id !== t.id && x.category !== t.category)).slice(0, 3);

  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <article className="sty"><div className="sty-main">
        <p className="meta crumb"><Link to="/">&larr; Top stories</Link><span className="cat" style={{ color: "var(--hue-context)" }}>Timeline</span><Link className="cat" to={"/?c=" + encodeURIComponent(t.category)}>{t.category}</Link><span>{t.updated}</span><AiBadge /></p>
        <h1>{t.title}</h1>
        <div className="sty-media"><Ph className="mv" r="16/9" src={t.image} role="button" tabIndex={0} aria-label="Open media fullscreen" /></div>
        <p className="dek" style={{ marginTop: 14 }}>{t.context || t.dek || ""}</p>
        {t.audio ? <AudioPlayer id={t.id} kind="timeline" title={t.title} url={"/timeline/" + t.id} label={t.audio.label} duration={t.audio.duration} src={t.audio.src} /> : null}
        <section className="sty-sec"><h2 className="sec-h"><span>The story so far</span><span className="sec-n">{beats.length} entries</span></h2><ol className="beats">
          {beats.map((b, i) => {
            const last = i === beats.length - 1;
            return (
              <li key={i} className={"beat" + (last ? " last" : "")}><span className="beat-d">{b.date}</span><span className={"beat-dot" + (last ? " now" : "")}></span><div className="beat-c">
                {last ? <em className="beat-now">LATEST</em> : null}<h3>{b.label}</h3><p>{b.body}</p>
                {(b.stories || []).map((sid) => {
                  const s = byId[sid]; if (!s) return null;
                  return <Link key={sid} className="beat-story" to={"/story/" + s.id}><small>{s.category.toUpperCase()} &middot; {ago(s.minutes_ago)} ago &middot; {s.outlet_count} outlets</small><span>{s.headline}</span></Link>;
                })}
              </div></li>
            );
          })}
        </ol></section>
        {more.length ? (
          <section className="sty-sec"><h2 className="sec-h"><span>More timelines</span></h2><div className="latest">
            {more.map((x) => <div key={x.id} className="hv"><Link to={"/timeline/" + x.id}><small>{x.points.length} events &middot; {x.updated}</small>{x.title}</Link></div>)}
          </div></section>
        ) : null}
      </div></article>
    </main>
  );
}
