import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Ph } from "../Photo.jsx";
import { ago, slug } from "./shared.jsx";
import { useData } from "../Status.jsx";
import { loadWatch } from "../../api.js";
import { SHAPES, chunkBlocks, planFront } from "./frontPlan.js";

// Desktop front page: a ruled grid (1px borders between cells) with a Latest rail, after
// Bloomberg's home page. Stories are dealt out of the feed in order; each one is used once.

const PLAY = <svg width="42" height="42" viewBox="0 0 42 42" aria-hidden="true"><circle cx="21" cy="21" r="19.5" fill="none" stroke="currentColor" strokeWidth="2" /><path d="M17 13.5v15l12-7.5z" fill="currentColor" /></svg>;

function mins(m) { return m < 60 ? m + " min" : Math.round(m / 60) + " h"; }
function dur(s) { return s ? Math.floor(s / 60) + ":" + String(Math.round(s % 60)).padStart(2, "0") : ""; }
const href = (s) => "/story/" + s.id;

function Kick({ s, top }) {
  const hot = top && top === s && s.outlet_count >= 3;
  return <div className="rf-meta">{hot ? <b className="rf-red">Most covered</b> : <span className="cat">{s.category}</span>}<span>{ago(s.minutes_ago)} ago</span>{s.outlet_count > 1 ? <span>{s.outlet_count} outlets</span> : null}</div>;
}

function Related({ list }) {
  if (!list.length) return null;
  return <div className="rf-rel"><span>Related</span>{list.map((s) => <Link key={s.id} to={href(s)}>{s.headline}</Link>)}</div>;
}

function Cell({ s, side, r, big, top, rel, vid }) {
  if (!s) return <div className={"rf-cell " + side}></div>;
  const playable = vid && s.video;
  return (
    <article className={"rf-cell hv " + side}>
      <Link to={href(s)} className={"rf-img" + (playable ? " vid" : "")} tabIndex={-1} aria-hidden="true">
        <Ph r={r} src={playable ? s.video.poster || s.image : s.image} msg={false} />
        {playable ? <span className="rf-play">{PLAY}{s.video.duration ? <em>{dur(s.video.duration)}</em> : null}</span> : null}
      </Link>
      <Kick s={s} top={top} />
      <h2 className={"rf-hd" + (big ? " big" : "")}><Link to={href(s)}>{s.headline}</Link></h2>
      {s.dek ? <p className="rf-dek">{s.dek}</p> : null}
      <Related list={rel || []} />
    </article>
  );
}

function Package({ p }) {
  if (!p || !p.head) return null;
  const s = p.head;
  return (
    <section className="rf-pkg">
      <div className="rf-top"><Link to={"/?c=" + encodeURIComponent(p.name)} className="rf-topic">{p.name}</Link></div>
      <div className="rf-pg hv">
        <Link to={href(s)} className="rf-img" tabIndex={-1} aria-hidden="true"><Ph r="4/3" src={s.image} msg={false} /></Link>
        <div><Kick s={s} /><h2 className="rf-hd"><Link to={href(s)}>{s.headline}</Link></h2></div>
        <div className="rf-side">{p.links.slice(0, 2).map((l, i) => <Link key={l.id} to={href(l)} className={i ? "lab" : ""}>{l.headline}</Link>)}</div>
      </div>
    </section>
  );
}

function Videos({ list }) {
  const [i, setI] = useState(0);
  const s = list.length ? list[Math.min(i, list.length - 1)] : null;
  return (
    <section className="rf-box">
      <div className="rf-rh"><h2>Today{"’"}s videos</h2><Link className="rf-pill" to="/watch">Explore more</Link></div>
      <Link to="/watch" className="rf-vc">
        <span className={"rf-img" + (s ? " vid" : "")}><Ph r="16/10" src={s ? s.video.poster || s.image : null} msg={false} />{s ? <span className="rf-play">{PLAY}{s.video.duration ? <em>{dur(s.video.duration)}</em> : null}</span> : null}</span>
        <p>{s ? s.headline : "Today’s videos will show up here."}</p>
      </Link>
      {list.length > 1 ? (
        <div className="rf-nav">
          <div className="rf-dots">{list.map((x, j) => <button key={x.id} type="button" className={j === i ? "on" : ""} aria-label={"Video " + (j + 1)} onClick={() => setI(j)}></button>)}</div>
          <div className="rf-arr"><button type="button" aria-label="Previous video" disabled={i === 0} onClick={() => setI(i - 1)}>&lsaquo;</button><button type="button" aria-label="Next video" disabled={i >= list.length - 1} onClick={() => setI(i + 1)}>&rsaquo;</button></div>
        </div>
      ) : null}
    </section>
  );
}

function Latest({ S, forYou }) {
  const L = S.slice().sort((a, b) => a.minutes_ago - b.minutes_ago).slice(0, 8);
  return (
    <section className="rf-box rf-lt">
      <div className="rf-rh"><h2 className="red">Latest</h2><div className="rf-pills"><span className="rf-pill on">All</span><Link className="rf-pill" to={forYou}>For you</Link></div></div>
      {L.map((s) => <Link key={s.id} to={href(s)}><time className={s.minutes_ago < 30 ? "new" : ""}>{mins(s.minutes_ago)}</time><b>{s.headline}</b></Link>)}
      <Link className="rf-all" to="/?c=All">See all latest &rsaquo;</Link>
    </section>
  );
}

export default function RuledFront({ S, TLS, EXS, forYou }) {
  const p = planFront(S);
  // Today's videos come from the videos feed (the Watch tab's), not from the news feed. Fails quietly.
  const wq = useData(loadWatch, [], { cacheKey: "watch" });
  const W = ((wq.data && wq.data.stories) || []).filter((s) => s.video);
  const cellVideo = p.video || W[0] || p.vfill;
  const vids = W.filter((s) => s !== cellVideo).slice(0, 6);
  const tl = TLS[0];
  return (
    <div className="rf">
      <div className="rf-L">
        <Cell s={p.a} side="a" r="3/2" rel={p.relA} top={p.topCovered} />
        <Cell s={p.lead} side="b" r="16/9" big rel={p.relLead} top={p.topCovered} />
        <Package p={p.p1} />
        <Cell s={cellVideo} side="a" r="3/2" vid />
        <Cell s={p.b} side="b" r="16/10" big rel={p.relB} />
        <Package p={p.p2} />
        <Cell s={p.row5[0]} side="a" r="3/2" />
        <Cell s={p.row5[1]} side="b" r="16/10" big />
      </div>
      <aside className="rf-R">
        <Videos list={vids} />
        <Latest S={S} forYou={forYou} />
        {tl ? (
          <section className="rf-box rf-tl">
            <div className="rf-rh"><h2>Timeline</h2><Link to="/timelines">See all &rsaquo;</Link></div>
            <h3><Link to={"/timeline/" + tl.id}>{tl.title}</Link></h3>
            <ol>{tl.points.slice(0, 3).map((x, i) => <li key={i}><b>{x.date}</b><span>{x.text}</span></li>)}</ol>
          </section>
        ) : null}
        {EXS.length ? (
          <section className="rf-box rf-ex">
            <div className="rf-rh"><h2>Explainers</h2><Link to="/explainers">See all &rsaquo;</Link></div>
            {EXS.slice(0, 3).map((x) => <Link key={x.id} to={"/explainer/" + x.id}><span>{x.category} &middot; {x.read}</span>{x.title}</Link>)}
          </section>
        ) : null}
        <section className="rf-box rf-promo"><h3>Don{"’"}t miss a story.</h3><p>Get the daily brief and follow topics in the app.</p><Link to="/download">Get it now</Link></section>
      </aside>
    </div>
  );
}

// ---- category blocks lower on the page ---------------------------------------------------------
// Full width, no rail. Each block picks one of several Bloomberg-style shapes so the endless feed
// does not repeat itself; a shape needs a minimum number of stories, else the block falls back to
// the three-column one.

function Pic({ s, r }) { return <Link to={href(s)} className="rf-img" tabIndex={-1} aria-hidden="true"><Ph r={r} src={s.image} msg={false} /></Link>; }

// Small rounded card with a thumbnail, like Bloomberg's Newsletter / Watch / Listen cards.
function MiniCard({ s }) {
  const v = s.video;
  return (
    <Link to={href(s)} className="rf-mc">
      <span className="rf-mt"><Ph r="1/1" src={v ? v.poster || s.image : s.image} msg={false} />{v ? <i className="rf-mp"></i> : null}</span>
      <span className="rf-mx"><small>{v ? "Watch" + (v.duration ? " (" + dur(v.duration) + ")" : "") : s.category}</small><b>{s.headline}</b></span>
    </Link>
  );
}

// Four equal columns: a picture and a headline each.
function FourUp({ L }) {
  return (
    <div className="rf-blk rf-four">
      {L.map((s) => <Link key={s.id} to={href(s)} className="rf-fc"><span className="rf-img"><Ph r="3/2" src={s.image} msg={false} /></span><b>{s.headline}</b></Link>)}
    </div>
  );
}

// Image, headline, a column of headlines, and rounded cards on the right.
function BandCards({ L, name, to }) {
  const [h, l1, l2, c1, c2] = L;
  return (
    <div className="rf-blk">
      <div className="rf-pg4 hv">
        <Pic s={h} r="4/3" />
        <div><Kick s={h} /><h2 className="rf-hd"><Link to={href(h)}>{h.headline}</Link></h2>{h.dek ? <p className="rf-dek">{h.dek}</p> : null}</div>
        <div className="rf-side">{[l1, l2].filter(Boolean).map((l, i) => <Link key={l.id} to={href(l)} className={i ? "lab" : ""}>{l.headline}</Link>)}</div>
        <div className="rf-cards">{[c1, c2].filter(Boolean).map((c) => <MiniCard key={c.id} s={c} />)}</div>
      </div>
    </div>
  );
}

// Wide picture with headline and summary, a rounded "more from" box, then a row of headline + summary.
function Feature({ L, name }) {
  const [h, ...r] = L;
  const box = r.slice(0, 3), row = r.slice(3, 6);
  return (
    <>
      <div className="rf-blk rf-bw">
        <div className="rf-bw1 hv">
          <Pic s={h} r="3/2" />
          <div><Kick s={h} /><h2 className="rf-hd"><Link to={href(h)}>{h.headline}</Link></h2>{h.dek ? <p className="rf-dek">{h.dek}</p> : null}</div>
        </div>
        <div className="rf-more"><span>More in {name}:</span><ul>{box.map((s) => <li key={s.id}><Link to={href(s)}>{s.headline}</Link></li>)}</ul></div>
      </div>
      {row.length ? (
        <div className="rf-blk rf-bw2">
          {row.map((s, i) => (
            <div key={s.id} className={"rf-b2" + (i === 0 ? " lead" : "")}>
              {i === 0 ? <Pic s={s} r="4/3" /> : null}
              <div><Kick s={s} /><h3><Link to={href(s)}>{s.headline}</Link></h3>{s.dek ? <p>{s.dek}</p> : null}</div>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

// Picture on the left, big headline and summary on the right with a rounded card under them.
function BigTake({ L }) {
  const [h, c, ...r] = L;
  return (
    <>
      <div className="rf-blk rf-bt hv">
        <Pic s={h} r="3/2" />
        <div><Kick s={h} /><h2 className="rf-hd big"><Link to={href(h)}>{h.headline}</Link></h2>{h.dek ? <p className="rf-dek">{h.dek}</p> : null}{c ? <div style={{ marginTop: 20 }}><MiniCard s={c} /></div> : null}</div>
      </div>
      {r.length ? <FourUp L={r.slice(0, 4)} /> : null}
    </>
  );
}

// Three columns, then a topic band (the first shape).
const COLS = { 3: ["5fr 4fr 4fr", "4fr 5fr 4fr"], 2: ["7fr 5fr", "5fr 7fr"], 1: ["1fr", "1fr"] };
function ThreeCol({ L, title, rev, more }) {
  const cells = L.slice(0, 3), h = L[3];
  const n = cells.length, wide = rev ? 0 : n === 3 ? 1 : n - 1;   // which column is the wide one
  const pkg = h ? { name: more || "More in " + (title || h.category), head: h, links: L.slice(4, 7) } : null;
  return (
    <div className={"rf-L multi" + (pkg ? "" : " nopkg")} style={{ gridTemplateColumns: COLS[n][rev ? 0 : 1].split(" ").map((x) => "minmax(0," + x + ")").join(" ") }}>
      {cells.map((x, i) => <Cell key={x.id} s={x} side={i < n - 1 ? "a" : "b"} r={i === wide ? "16/10" : "3/2"} big={i === wide} />)}
      <Package p={pkg} />
    </div>
  );
}

// The most common topic labels among a block's stories, shown beside its name like Bloomberg's sub-links.
function subs(L) {
  const n = {};
  L.forEach((s) => (s.topics || []).forEach((t) => { n[t.label] = (n[t.label] || 0) + 1; }));
  return Object.keys(n).sort((a, b) => n[b] - n[a]).slice(0, 3);
}

export function RuledSection({ title, to, L, at = 0, rev, more, bare }) {
  if (!L.length) return null;
  let shape = SHAPES[at % SHAPES.length];
  if (L.length < shape[1]) shape = SHAPES[0];
  const k = shape[0], link = to || "/?c=" + encodeURIComponent(title);
  return (
    <section className="rf-sec">
      <div className="rf solo">
        {bare ? null : <div className="rf-head"><b>{title}</b>{subs(L).map((t) => <Link key={t} to={"/topic/" + slug(t)}>{t}</Link>)}{to ? <Link to={to} className="all">See all &rsaquo;</Link> : null}</div>}
        {k === "three" ? <ThreeCol L={L} title={title} rev={rev !== undefined ? rev : at % 2 === 1} more={more} />
          : k === "four" ? <FourUp L={L.slice(0, 4)} />
          : k === "band" ? <BandCards L={L} name={"More in " + title} to={link} />
          : k === "feature" ? <Feature L={L} name={title} />
          : <BigTake L={L} />}
      </div>
    </section>
  );
}

// A row of tall video cards from the videos feed, with a headline over each picture.
export function WatchStrip() {
  const q = useData(loadWatch, [], { cacheKey: "watch" });
  const V = ((q.data && q.data.stories) || []).filter((s) => s.video).slice(0, 10);
  const row = useRef(null);
  const go = (d) => { const el = row.current; if (el) el.scrollBy({ left: d * el.clientWidth * 0.9, behavior: "smooth" }); };
  const cards = V.length ? V : [null, null, null, null, null];
  return (
    <section className="rf-sec">
      <div className="rf rf-watch solo">
        <div className="rf-blk">
          <div className="rf-rh"><h2>Watch</h2><Link className="rf-pill" to="/watch">Explore more</Link></div>
          <div className="rf-vrow" ref={row}>
            {cards.map((s, i) => s ? (
              <Link key={s.id} to="/watch" className="rf-vcard">
                <Ph r="2/3" src={s.video.poster || s.image} msg={false} />
                <span className="rf-vt"><span className="rf-vp">{PLAY}{s.video.duration ? <em>{dur(s.video.duration)}</em> : null}</span><b>{s.headline}</b></span>
              </Link>
            ) : (
              <span key={i} className="rf-vcard"><Ph r="2/3" src={null} msg={false} />{i === 0 ? <span className="rf-vt"><b>Today{"’"}s videos will show up here.</b></span> : null}</span>
            ))}
          </div>
          {V.length > 4 ? <div className="rf-nav"><span></span><div className="rf-arr"><button type="button" aria-label="Scroll left" onClick={() => go(-1)}>&lsaquo;</button><button type="button" aria-label="Scroll right" onClick={() => go(1)}>&rsaquo;</button></div></div> : null}
        </div>
      </div>
    </section>
  );
}

// A category's own page: the same blocks as the bottomless feed, three at a time, more as you near the bottom.
export function RuledFeed({ S }) {
  const blocks = chunkBlocks(S), [shown, setShown] = useState(3);
  useEffect(() => {
    const check = () => { if (shown < blocks.length && document.documentElement.scrollHeight - scrollY - innerHeight < 900) return setTimeout(() => setShown((n) => Math.min(blocks.length, n + 2)), 250); };
    let t = check();
    const on = () => { clearTimeout(t); t = check(); };
    addEventListener("scroll", on, { passive: true }); addEventListener("resize", on);
    return () => { clearTimeout(t); removeEventListener("scroll", on); removeEventListener("resize", on); };
  }, [shown, blocks.length]);
  const toTop = () => scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  return (
    <>
      {blocks.slice(0, shown).map((b) => <RuledSection key={b.L[0].id} title="" L={b.L} at={b.at} bare />)}
      <div className="endless" aria-live="polite">
        {shown < blocks.length ? <div className="endless-load"><i className="spin"></i><span>Loading more{"…"}</span></div>
          : <div className="endless-end"><b>You{"’"}re all caught up</b><span>That was the whole feed, {S.length} stories.</span><button type="button" className="more-btn" onClick={toTop}>Back to top</button></div>}
      </div>
    </>
  );
}
