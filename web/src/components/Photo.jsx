import { useEffect, useState } from "react";
import { faviconFor } from "../api.js";

// The grey `.ph` box, filled with a real picture when there is a URL. A missing URL or a
// failed load leaves the placeholder (and its label) in place, so layout never shifts.
export function Ph({ r, t, style, src, alt = "", round, children, ...rest }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  const show = !!src && !bad;
  return (
    <div {...rest} className={"ph" + (show ? " has-img" : "") + (round ? " round" : "") + (rest.className ? " " + rest.className : "")}
      style={{ aspectRatio: r, ...style }}>
      {show ? <img className="ph-img" src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setBad(true)} /> : (t === undefined ? "image" : t)}
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
