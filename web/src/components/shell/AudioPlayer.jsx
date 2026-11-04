import { useLocation } from "react-router-dom";
import { fmt, useShell } from "./ShellContext.jsx";

const PLAY = "▶", PAUSE = "❚❚";

// Inline narration player (PROTO.player). Pressing play makes this item the site-wide
// current narration, which the persistent mini player (Mplay) then keeps going.
// id/kind/title/url identify the item; they default from the label and the current page.
export function AudioPlayer({ label, duration, id, kind, title, url, src }) {
  const sh = useShell(), loc = useLocation();
  const k = kind || loc.pathname.split("/").filter(Boolean)[0] || "timeline";
  const i = id != null ? id : loc.pathname.split("/")[2] || label;
  const mine = sh.isMine(i, k);
  const x = mine ? sh.position : 0;
  function press() {
    if (mine) sh.toggle();
    else sh.start({ id: i, kind: k, title: title || label, url: url || loc.pathname + loc.search, src, dur: duration });
  }
  return (
    <div className="ap" data-id={i} data-kind={k}>
      <button type="button" className="ap-btn" aria-label={mine && sh.player.playing ? "Pause" : "Play"} onClick={press}>{mine && sh.player.playing ? PAUSE : PLAY}</button>
      <div className="ap-mid"><div className="ap-label">{label}</div>
        <div className="ap-bar" role="progressbar" aria-label="Narration progress" aria-valuemin="0" aria-valuemax={duration} aria-valuenow={Math.floor(x)}
          onClick={(e) => { if (mine) { const r = e.currentTarget.getBoundingClientRect(); sh.seek((e.clientX - r.left) / r.width); } }}><i style={{ width: (x / duration * 100) + "%" }}></i></div></div>
      <span className="ap-time">{fmt(x)} / {fmt(duration)}</span>
    </div>
  );
}
