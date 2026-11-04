import { useEffect } from "react";
import { Link } from "react-router-dom";
import { fmt, useShell } from "./ShellContext.jsx";

// Persistent mini narration player. Rendered by Layout inside the phone tab pill, or
// at the bottom of the page otherwise.
export function useMplayRoot(on) {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("has-mplay", on);
    root.style.setProperty("--mplay-h", on ? (matchMedia("(max-width:700px)").matches ? "52px" : "80px") : "0px");
    return () => { root.classList.remove("has-mplay"); root.style.setProperty("--mplay-h", "0px"); };
  }, [on]);
}

export default function Mplay() {
  const sh = useShell(), P = sh.player;
  if (!P) return null;
  const t = sh.position;
  return (
    <section className="mplay" role="region" aria-label="Now playing"><div className="mplay-in">
      <button type="button" className="mplay-pp" aria-label={P.playing ? "Pause" : "Play"} onClick={sh.toggle}>{P.playing ? "❚❚" : "▶"}</button>
      <Link className="mplay-t" to={P.url}><small>{P.kind} &middot; narration</small><b>{P.title}</b></Link>
      <div className="mplay-bar" role="progressbar" aria-label="Narration progress" aria-valuemin="0" aria-valuemax={P.dur} aria-valuenow={Math.floor(t)}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); sh.seek((e.clientX - r.left) / r.width); }}><i style={{ width: (t / P.dur * 100) + "%" }}></i></div>
      <span className="mplay-time">{fmt(t)} / {fmt(P.dur)}</span>
      <button type="button" className="mplay-sp" aria-label="Playback speed" onClick={sh.cycleSpeed}>{P.speed}&times;</button>
      <button type="button" className="mplay-x" aria-label="Close player" onClick={sh.closePlayer}>&times;</button>
    </div></section>
  );
}
