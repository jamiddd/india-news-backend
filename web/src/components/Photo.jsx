import { useEffect, useState } from "react";
import { faviconFor } from "../api.js";
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
export function Ph({ r, t, style, src, alt = "", round, msg = "Could not load the image", children, ...rest }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  const show = !!src && !bad;
  return (
    <div {...rest} className={"ph" + (show ? " has-img" : "") + (round ? " round" : "") + (rest.className ? " " + rest.className : "")}
      style={{ aspectRatio: r, ...style }}>
      {show ? <img className="ph-img" src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => { setBad(true); dropCached(src); }} /> : (t ? t : <Fallback msg={msg} />)}
      {children}
    </div>
  );
}

// Round source logo: the publisher's favicon, or the plain grey disc when unknown/unreachable.
export function Fav({ name }) {
  const [bad, setBad] = useState(false);
  const url = faviconFor(name);
  useEffect(() => setBad(false), [name]);
  return <i className="fv">{url && !bad ? <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)} /> : null}</i>;
}
