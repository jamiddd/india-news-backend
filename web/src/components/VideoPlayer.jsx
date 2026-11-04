import { useEffect, useRef, useState } from "react";
import { resolveVideoUrl } from "../api.js";
import OinMark from "./OinMark.jsx";

// Plays whichever kind of video a story carries:
//   - YouTube embeds (youtube.com/embed/<id>)  -> iframe (privacy-enhanced domain)
//   - HLS manifests (.m3u8)                    -> native on Safari, hls.js elsewhere
//   - direct files (.mp4 ...)                  -> <video>
//   - "pending" videos (no URL yet)            -> resolved through the API by article id
// Fills its parent box. onEnded lets Watch move on to the next video.
const YT = /(?:youtube(?:-nocookie)?\.com\/embed\/|youtube\.com\/watch\?v=|youtu\.be\/)([A-Za-z0-9_-]{11})/;
export const youTubeId = (u) => { const m = u && YT.exec(u); return m ? m[1] : null; };
export const isHls = (u) => /\.m3u8(\?|$)/i.test(u || "");

// controls={false}: no player chrome (the Swipe feed, like the app's Shorts). cover: fill the box and crop
// (the Watch tab's pinned player), instead of fitting inside it.
export default function VideoPlayer({ src, articleId, poster, autoPlay = true, muted = false, loop = false, controls = true, cover = false, onEnded, label = "Video" }) {
  const [url, setUrl] = useState(src || null);
  const [state, setState] = useState(src ? "ready" : "resolving");   // resolving | ready | failed
  const ref = useRef(null);

  useEffect(() => {
    let live = true;
    setUrl(src || null);
    if (src) { setState("ready"); return undefined; }
    if (!articleId) { setState("failed"); return undefined; }
    setState("resolving");
    resolveVideoUrl(articleId).then((u) => { if (live) { setUrl(u); setState(u ? "ready" : "failed"); } }, () => { if (live) setState("failed"); });
    return () => { live = false; };
  }, [src, articleId]);

  const yt = youTubeId(url);
  useEffect(() => {
    const v = ref.current;
    if (!v || !url || yt) return undefined;
    let hls = null, dead = false;
    const fail = () => { if (!dead) setState("failed"); };
    const native = () => {
      if (v.canPlayType("application/vnd.apple.mpegurl")) { v.src = url; if (autoPlay) v.play().catch(() => {}); } else fail();
    };
    if (isHls(url)) {
      // hls.js first wherever MSE exists (browsers' own HLS handling is patchy, e.g. manifests with
      // separate subtitle groups); the native path covers iPhone Safari and as a last resort.
      import("hls.js").then(({ default: Hls }) => {
        if (dead) return;
        if (!Hls.isSupported()) { native(); return; }
        hls = new Hls({ enableWorker: true });
        hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) { console.warn("[video] hls fatal:", d.type, d.details, d.response && d.response.code); fail(); } });
        hls.loadSource(url); hls.attachMedia(v);
        if (autoPlay) v.play().catch(() => {});
      }, native);
    } else {
      v.src = url;
      if (autoPlay) v.play().catch(() => {});
    }
    return () => { dead = true; if (hls) hls.destroy(); };
  }, [url, yt]);

  const bg = poster ? { backgroundImage: "url(" + poster + ")" } : undefined;
  if (state === "failed") return <div className="vp vp-msg" style={bg}><span className="vp-fail"><OinMark tone="dark" className="ph-mark" size={28} />Could not load the video</span></div>;
  if (state === "resolving" || !url) return <div className="vp vp-msg" style={bg}><i className="spin"></i></div>;
  if (yt) {
    return <iframe className="vp" title={label} loading="lazy" allowFullScreen referrerPolicy="strict-origin-when-cross-origin"
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
      src={"https://www.youtube-nocookie.com/embed/" + yt + "?playsinline=1&rel=0&modestbranding=1" + (controls ? "" : "&controls=0&cc_load_policy=0") + (loop ? "&loop=1&playlist=" + yt : "") + (autoPlay ? "&autoplay=1" : "")} />;
  }
  return <video className={"vp" + (cover ? " vp-cover" : "")} ref={ref} poster={poster || undefined} controls={controls} playsInline muted={muted} loop={loop} preload="metadata" onEnded={onEnded} onError={(e) => { const er = e.currentTarget.error; console.warn("[video] element error:", er && er.code, er && er.message); setState("failed"); }} />;
}
