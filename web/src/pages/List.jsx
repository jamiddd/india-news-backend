import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { FollowBtn, PopHost, Ph, SRow, ago, slug } from "../components/home/shared.jsx";
import { useData, statusView } from "../components/Status.jsx";
import { loadList } from "../api.js";
import { LANGS, REGIONS, outletHash } from "./Sources.jsx";

const SHOWN = 8;

// Data access for this page: loadList (src/api.js) returns the publisher's, topic's or whole feed's stories.
function outletMap(S) {
  const outlets = {};
  S.forEach((s) => s.coverage.forEach((v) => { if (!outlets[slug(v.outlet)]) outlets[slug(v.outlet)] = v.outlet; }));
  return outlets;
}
function topicMap(S) {
  const topics = {};
  S.forEach((s) => (s.topics || []).forEach((t) => { if (!topics[slug(t.label)]) topics[slug(t.label)] = t; }));
  return topics;
}

function Meta({ x }) { return <div className="meta"><span className="cat">{x.category}</span><span>{ago(x.minutes_ago)} ago</span></div>; }
function Card({ x, outlet }) {
  return (
    <div className="c">
      <Ph r="3/2" /><Meta x={x} />
      <h3><Link to={"/story/" + x.id}>{x.headline}</Link></h3>
      {outlet ? <a className="ext" href="#" target="_blank" rel="noopener" onClick={(e) => e.preventDefault()}>Read on {outlet} &#8599;</a> : null}
      <SRow s={x} />
    </div>
  );
}

// One template for the two screens a story links to: a publisher's page (/source/<name>)
// and a topic's page (/topic/<name>).
export default function List() {
  const { id } = useParams();
  const kind = useLocation().pathname.split("/").filter(Boolean)[0];
  const key = decodeURIComponent(id || "");
  const q = useData(() => loadList(kind, key), [kind, key]), d = q.data;
  const [all, setAll] = useState(false);
  useEffect(() => { setAll(false); }, [kind, key]);

  let name = "", list = [], head = null, extra = null;
  if (d) {
    const S = d.stories;
    if (kind !== "source" && kind !== "topic") {
      // plain /list (no publisher or topic): every story
      name = "All stories";
      list = S;
      head = <div className="lh-top"><div><h1>{name}</h1><p className="lh-meta">{list.length} stories</p></div></div>;
    } else if (kind === "source") {
      const outlets = outletMap(S);
      name = outlets[key] || "";
      if (name) {
        list = S.filter((s) => s.coverage.some((v) => v.outlet === name));
        const h = outletHash(name), host = slug(name) + ".example";
        head = (
          <div className="lh-top"><span className="fv big" aria-hidden="true"></span>
            <div><h1>{name}</h1><p className="lh-meta">{REGIONS[h % 4]} &middot; {LANGS[h % 3]} &middot; {list.length} stories &middot; <a href="#" target="_blank" rel="noopener" onClick={(e) => e.preventDefault()}>Visit {host} &#8599;</a></p></div>
            <div className="lh-act"><FollowBtn k={"source:" + key} /></div></div>
        );
        const others = Object.keys(outlets).filter((k) => k !== key).slice(0, 4);
        extra = (
          <>
            <section className="sec"><h2 className="sec-h"><span>Similar sources</span></h2><p className="sty-sub">More publishers covering {list[0] ? list[0].category : "the news"}.</p>
              <div className="tiles four">{others.map((k) => {
                const h2 = outletHash(outlets[k]);
                return (
                  <div className="stile" key={k}><Link className="stile-l" to={"/source/" + k}><span className="fv big sm"></span><span><b>{outlets[k]}</b><small>{REGIONS[h2 % 4]} &middot; {LANGS[h2 % 3]}</small></span></Link><FollowBtn k={"source:" + k} /></div>
                );
              })}</div></section>
            <p className="foot-note">Stories open on the publisher&rsquo;s own site, so every read counts for them.</p>
          </>
        );
      }
    } else {
      const t0 = topicMap(S)[key];
      if (t0) {
        name = t0.label;
        list = S.filter((s) => (s.topics || []).some((t) => slug(t.label) === key));
        head = (
          <div className="lh-top"><div><span className="kind">{t0.kind}</span><h1>{name}</h1><p className="lh-meta">{list.length} stories</p></div><div className="lh-act"><FollowBtn k={"topic:" + key} /></div></div>
        );
        const seen = {}, rel = [];
        list.forEach((s) => s.topics.forEach((t) => { const k = slug(t.label); if (k !== key && !seen[k]) { seen[k] = 1; rel.push(t); } }));
        if (rel.length) extra = (
          <section className="sec"><h2 className="sec-h"><span>Related topics</span></h2>
            <div className="tiles four">{rel.slice(0, 8).map((t) => <Link className="tile" key={slug(t.label)} to={"/topic/" + slug(t.label)}><b>{t.label}</b><span>{t.kind}</span></Link>)}</div></section>
        );
      }
    }
  }
  useEffect(() => { if (name) document.title = name + " — Open Indian News"; }, [name]);

  const wait = statusView(q, "these stories", ({ children }) => <main className="wide" style={{ paddingBottom: 72 }}>{children}</main>);
  if (wait) return wait;
  if (!name) return <main className="wide" style={{ paddingBottom: 72 }}><p className="empty" style={{ paddingTop: 48 }}>No stories found for &ldquo;{key.replace(/-/g, " ")}&rdquo;. <Link to="/">Back to top stories</Link></p></main>;
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <p className="meta crumb" style={{ paddingTop: 28 }}><Link to="/">&larr; Top stories</Link></p>
      <header className="lh">{head}</header>
      <section className="sec" style={{ marginTop: 28 }}>
        <h2 className="sec-h"><span>{kind === "source" ? "Stories" : "Latest on this topic"}</span><span className="sec-n">{list.length} stories</span></h2>
        <div className="cards flat" id="grid">{list.slice(0, all ? list.length : SHOWN).map((x) => <Card key={x.id} x={x} outlet={kind === "source" ? name : ""} />)}</div>
        {list.length > SHOWN && !all ? <button type="button" className="more-btn" id="more" style={{ maxWidth: 360, margin: "20px auto 0" }} onClick={() => setAll(true)}>Show more stories</button> : null}
      </section>
      {extra}
      <PopHost />
    </main>
  );
}
