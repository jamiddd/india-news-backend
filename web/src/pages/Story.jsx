import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Carousel, CoverageRows, PopHost, SRow, Tip, ago, slug } from "../components/home/shared.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadStory } from "../api.js";

const COLLAPSED = 5, TOPIC_SHOWN = 6;

// Data access for this page: loadStory (src/api.js) returns the story first, then its related stories.
function pickStory(d, id) {
  const S = d.stories, s = S.filter((x) => x.id === +id)[0];
  if (!s) return {};
  const related = S.filter((x) => x.id !== s.id && x.category === s.category)
    .concat(S.filter((x) => x.id !== s.id && x.category !== s.category)).slice(0, 3);
  return { s, related };
}

export default function Story() {
  const { id } = useParams();
  const q = useData(() => loadStory(id), [id]), d = q.data;
  const [covOpen, setCovOpen] = useState(false), [topOpen, setTopOpen] = useState(false);
  const { s, related } = d ? pickStory(d, id) : {};

  useEffect(() => {
    if (!s) return;
    document.title = s.headline + " — Open Indian News";
    // Reading history (shown on the profile's History tab).
    try {
      const l = (JSON.parse(localStorage.getItem("oin-read")) || []).filter((x) => x !== s.id);
      l.push(s.id); localStorage.setItem("oin-read", JSON.stringify(l.slice(-50)));
    } catch (e) {}
    if (location.hash) { try { const el = document.querySelector(location.hash); if (el) el.scrollIntoView(); } catch (e) {} }
  }, [s]);

  const wait = statusView(q, "this story", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  if (!s) return <main className="wide" style={{ paddingBottom: 72 }}><p className="empty" style={{ paddingTop: 48 }}>Story not found. <Link to="/">Back to top stories</Link></p></main>;

  // Coverage runs oldest first, as in the app; the first report is tagged FIRST.
  const chrono = s.coverage.slice().sort((a, b) => b.minutes_ago - a.minutes_ago);
  const topics = s.topics || [];
  return (
    <main className="wide" id="story" style={{ paddingBottom: 72 }}>
      <article className="sty"><div className="sty-main">
        <p className="meta crumb"><Link to="/">&larr; Top stories</Link><Link className="cat" to={"/?c=" + encodeURIComponent(s.category)}>{s.category}</Link><span>Updated {ago(s.minutes_ago)} ago</span></p>
        <h1>{s.headline}</h1><p className="dek">{s.dek}</p>
        <SRow s={s} />
        <div className="sty-media"><Carousel s={s} /></div>
        <section className="sty-sec" id="summary"><h2 className="sec-h"><span>Summary</span></h2><ul className="bullets">{s.summary.map((b, i) => <li key={i}>{b}</li>)}</ul></section>
        <section className="sty-sec" id="framing"><h2 className="sec-h"><span>Media framing</span></h2><p className="sty-sub">Comparing how {s.outlet_count} outlets are framing this story.</p>
          <div className="matrix">{s.framing.map((f, i) => <div className="mrow" key={i}><b><i className="fv"></i><span><Link className="so" to={"/source/" + slug(f.outlet)}>{f.outlet}</Link></span></b><span>{f.angle}</span><span className="t">{f.tag}</span></div>)}</div></section>
        <section className="sty-sec" id="coverage"><h2 className="sec-h"><span>Coverage</span><span className="sec-n">{chrono.length} articles</span></h2>
          <div className="cov-l" id="cov-l"><CoverageRows link list={covOpen ? chrono : chrono.slice(0, COLLAPSED)} /></div>
          {chrono.length > COLLAPSED ? <button type="button" className="more-btn" id="cov-more" onClick={() => setCovOpen(!covOpen)}>{covOpen ? "Show fewer" : "Show " + (chrono.length - COLLAPSED) + " more articles"}</button> : null}</section>
        {topics.length ? (
          <section className="sty-sec" id="topics"><h2 className="sec-h"><span>Related topics</span></h2>
            <div className="tiles" id="tiles">{(topOpen ? topics : topics.slice(0, TOPIC_SHOWN)).map((t) => <Link className="tile" key={slug(t.label)} to={"/topic/" + slug(t.label)}><b>{t.label}</b><span>{t.kind}</span></Link>)}</div>
            {topics.length > TOPIC_SHOWN ? <button type="button" className="more-btn" id="top-more" onClick={() => setTopOpen(!topOpen)}>{topOpen ? "Show fewer" : "All " + topics.length + " topics"}</button> : null}</section>
        ) : null}
        {related.length ? (
          <section className="sty-sec" id="related"><h2 className="sec-h"><span>Related stories</span></h2>
            <div className="latest">{related.map((x) => <div className="hv" key={x.id}><Link to={"/story/" + x.id}><small>{x.category.toUpperCase()} &middot; {ago(x.minutes_ago)} ago</small>{x.headline}</Link><Tip id={x.id} /></div>)}</div></section>
        ) : null}
      </div></article>
      <PopHost />
    </main>
  );
}
