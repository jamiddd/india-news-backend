// Shimmering stand-ins shown while a page loads. They fill the screen so the footer
// stays below the fold instead of floating halfway up an empty page.
const sk = (cls, style) => <div className={"sk " + cls} style={style}></div>;
const lines = (n, w = []) => Array.from({ length: n }, (_, i) => <div key={i} className="sk sk-line" style={{ width: (w[i] || 100) + "%" }}></div>);

function Card({ ratio = "3/2" }) {
  return (
    <div className="sk-card">
      {sk("sk-img", { aspectRatio: ratio })}
      <div className="sk sk-line" style={{ width: "38%", height: 10 }}></div>
      {lines(2, [96, 70])}
      <div className="sk-row"><div className="sk sk-dot"></div><div className="sk sk-line" style={{ width: "40%", height: 10 }}></div></div>
    </div>
  );
}

export function FeedSkeleton() {
  return (
    <>
      <div className="sk-hero">
        <div>{sk("sk-img", { aspectRatio: "16/9" })}<div className="sk sk-line" style={{ width: "30%", height: 10, marginTop: 16 }}></div>{[95, 88, 55].map((w, i) => <div key={i} className="sk sk-line sk-h1" style={{ width: w + "%" }}></div>)}{lines(2, [92, 70])}</div>
        <div className="sk-side">{[0, 1, 2].map((i) => <div key={i} className="sk-mini">{lines(3, [35, 95, 70])}</div>)}</div>
      </div>
      <div className="sk-grid">{[0, 1, 2, 3, 4, 5].map((i) => <Card key={i} ratio={i % 3 === 0 ? "4/3" : "3/2"} />)}</div>
    </>
  );
}

const crumb = <div className="sk sk-line" style={{ width: "34%", height: 10, marginBottom: 6 }}></div>;
const title = (w) => w.map((x, i) => <div key={i} className="sk sk-line sk-h1" style={{ width: x + "%" }}></div>);
const sec = (name) => <div className="sk-sec-h"><div className="sk sk-line" style={{ width: 90 + name * 12, height: 12, margin: 0 }}></div></div>;
const frame = (children) => <article className="sty"><div className="sty-main">{children}</div></article>;

export function StorySkeleton() {
  return frame(<>
    {crumb}{title([96, 80])}
    {lines(2, [94, 62])}
    <div className="sk-row"><div className="sk sk-dot"></div><div className="sk sk-line" style={{ width: "30%", height: 10, margin: 0 }}></div></div>
    {sk("sk-img", { aspectRatio: "16/9", marginTop: 16 })}
    {sec(0)}{lines(4, [100, 94, 98, 70])}
    {sec(1)}{[0, 1, 2, 3].map((i) => <div key={i} className="sk-frow"><div className="sk sk-dot"></div><div className="sk sk-line" style={{ width: "22%", margin: 0 }}></div><div className="sk sk-line" style={{ flex: 1, margin: 0 }}></div></div>)}
    {sec(2)}{[0, 1, 2].map((i) => <div key={i} className="sk-cov">{lines(2, [40, 90])}</div>)}
  </>);
}

export function TimelineSkeleton() {
  return frame(<>
    {crumb}{title([92, 58])}
    {sk("sk-img", { aspectRatio: "16/9", marginTop: 8 })}
    {lines(3, [100, 92, 60])}
    <div className="sk sk-pill"></div>
    {sec(1)}
    <div className="sk-beats">{[0, 1, 2, 3].map((i) => (
      <div key={i} className="sk-beat"><div className="sk sk-line" style={{ width: 70, margin: 0 }}></div><i className="sk sk-dot"></i><div>{lines(1, [55])}{lines(2, [96, 80])}</div></div>
    ))}</div>
  </>);
}

export function ExplainerSkeleton() {
  return frame(<>
    {crumb}{title([94, 70])}
    {sk("sk-img", { aspectRatio: "16/9", marginTop: 8 })}
    <div className="sk sk-pill"></div>
    <div className="sk-quick"><div className="sk sk-line" style={{ width: 110, height: 10, margin: 0 }}></div>{lines(3, [100, 96, 58])}</div>
    {[0, 1, 2].map((i) => <div key={i} className="sk-exsec"><div className="sk sk-line sk-h2" style={{ width: (46 + i * 9) + "%" }}></div>{lines(4, [100, 97, 99, 64])}</div>)}
  </>);
}

// "Related stories" block on the story page, which loads after the story itself.
export function RelatedSkeleton() {
  return (
    <section className="sty-sec" aria-label="Loading related stories">
      <div className="sk-sec-h"><div className="sk sk-line" style={{ width: 130, height: 12, margin: 0 }}></div></div>
      {[0, 1, 2].map((i) => <div key={i} className="sk-cov">{lines(2, [32, 88])}</div>)}
    </section>
  );
}

export function GridSkeleton() {
  return <div className="sk-grid sk-top">{[0, 1, 2, 3, 4, 5].map((i) => <Card key={i} />)}</div>;
}

export default function Skeleton({ what = "stories" }) {
  const body = what === "this story" ? <StorySkeleton />
    : what === "this timeline" ? <TimelineSkeleton />
    : what === "this explainer" ? <ExplainerSkeleton />
    : /top stories|these stories|saved/.test(what) ? <FeedSkeleton /> : <GridSkeleton />;
  return (
    <div className="skel" role="status" aria-live="polite" aria-label={"Loading " + what}>{body}</div>
  );
}
