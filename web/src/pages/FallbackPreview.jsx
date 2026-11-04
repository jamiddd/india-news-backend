import { Ph } from "../components/Photo.jsx";

// Dev-only gallery of the fallback picture (route /fallback-preview, not in production builds).
// Use the theme button in the header to see it in light and dark.
const DEAD = "https://static.toiimg.com/photo/msid-134584451,imgsize-20064.cms";   // a real link that now returns 410
const cell = (title, note, node, w) => (
  <figure style={{ margin: 0, width: w || "100%" }}>
    {node}
    <figcaption style={{ marginTop: 8, fontSize: 13 }}><b>{title}</b><br /><span style={{ color: "var(--ink-3)" }}>{note}</span></figcaption>
  </figure>
);

export default function FallbackPreview() {
  return (
    <main className="wide" style={{ paddingBottom: 72 }}>
      <div className="feed-h"><h1>Fallback picture</h1><p>our mark, shown when an image is missing or fails to load</p></div>
      <h2 className="sec-h" style={{ marginTop: 32 }}><span>Cards</span></h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))", gap: 24 }}>
        {cell("No image URL", "3:2 card (feed, timelines, explainers)", <Ph r="3/2" src={null} />)}
        {cell("Broken link", "a publisher URL that returns 410: the real browser failure path", <Ph r="3/2" src={DEAD} alt="" />)}
        {cell("Square", "1:1 thumbnail (feed side column)", <Ph r="1/1" src={null} />)}
        {cell("Wide", "16:9 (story and explainer hero)", <Ph r="16/9" src={DEAD} />)}
      </div>
      <h2 className="sec-h" style={{ marginTop: 40 }}><span>Small</span></h2>
      <div style={{ display: "flex", gap: 28, alignItems: "flex-end", flexWrap: "wrap" }}>
        {cell("64px", "history thumbnail", <Ph className="hr-pic" r="1/1" t="" src={null} style={{ width: 64, height: 64 }} />, 64)}
        {cell("104px", "saved row", <Ph r="3/2" t="" src={null} style={{ width: 104 }} />, 104)}
        {cell("36px", "search result", <Ph t="" src={null} style={{ width: 36, height: 36, padding: 0, borderRadius: 6 }} />, 60)}
      </div>
      <h2 className="sec-h" style={{ marginTop: 40 }}><span>With a label</span></h2>
      <div style={{ maxWidth: 360 }}>{cell("Video with no thumbnail", "keeps its label instead of the mark", <Ph r="16/9" t="video 1" src={null} />)}</div>
    </main>
  );
}
