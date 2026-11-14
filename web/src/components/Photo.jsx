import { useEffect, useState } from "react";
import { faviconFor, imageInfo } from "../api.js";
import OinMark from "./OinMark.jsx";

// Our mark, with the message under it. The message only appears in tiles wide enough for it.
export function Fallback({ msg, size = 40 }) {
  return <span className="ph-fb"><OinMark className="ph-mark" size={size} />{msg ? <span className="ph-msg">{msg}</span> : null}</span>;
}

// A picture that failed to show must not stay in the service worker's image cache.
const dropCached = (u) => { try { if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ evict: u }); } catch (e) { /* no worker */ } };

// The grey `.ph` box, filled with a real picture when there is a URL. A missing URL or a failed
// load leaves a placeholder in place, so layout never shifts: our own mark on a muted tile, or the
// label when one is given (e.g. "video 1" for a video with no thumbnail).
// msg: the line under the mark when the box is big enough to fit it (false for videos, where a
// missing thumbnail isn't an error).
export function Ph({ r, t, style, src, alt = "", round, msg = "Could not load the image", children, onClick, ...rest }) {
  const [tried, setTried] = useState(0);   // how many candidates have failed
  const [small, setSmall] = useState(false);
  useEffect(() => { setTried(0); setSmall(false); }, [src]);
  const info = src ? imageInfo(src) : null;
  const cands = src ? [src].concat(info ? info.alts : []) : [];
  const cur = cands[tried];
  const show = !!cur;
  // The credit and link belong to the picture actually shown, which may be a fallback.
  const ci = cur ? imageInfo(cur) || info : null;
  const link = ci && ci.link;
  const open = (e) => { e.preventDefault(); e.stopPropagation(); window.open(link, "_blank", "noopener,noreferrer"); };
  const click = onClick || (link ? (e) => { if (!e.currentTarget.closest("a,button")) window.open(link, "_blank", "noopener,noreferrer"); } : undefined);
  return (
    <div {...rest} onClick={click} className={"ph" + (show ? " has-img" : "") + (round ? " round" : "") + (rest.className ? " " + rest.className : "")}
      style={{ aspectRatio: r, ...(!onClick && link ? { cursor: "pointer" } : null), ...style }}>
      {show ? <img key={cur} className="ph-img" src={cur} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer"
        onLoad={(e) => setSmall(e.currentTarget.clientWidth < 150)}
        onError={() => { setTried((n) => n + 1); dropCached(cur); }} /> : (t ? t : <Fallback msg={msg} />)}
      {show && ci && ci.source && t !== "" && !small ? (
        <span className="ph-credit" role={link ? "link" : undefined} onClick={link ? open : undefined} title={link ? "Open " + ci.source : undefined}>{ci.source}</span>
      ) : null}
      {children}
    </div>
  );
}

// Round source logo: the publisher's favicon, or the plain grey disc when unknown/unreachable.
export function Fav({ name, className = "" }) {
  const [bad, setBad] = useState(false);
  const url = faviconFor(name);
  useEffect(() => setBad(false), [name]);
  return <i className={"fv" + (className ? " " + className : "")}>{url && !bad ? <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)} /> : null}</i>;
}
