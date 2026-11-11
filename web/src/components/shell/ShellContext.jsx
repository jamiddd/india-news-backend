// Site-wide shell state, replacing the globals of prototype/static/shell.js (PROTO.player,
// PROTO.openLogin, PROTO.signedIn). Pages use useShell() to start narration or open sign-in.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { load, save } from "./toast.js";
import { syncNow, clearLocal } from "../../userData.js";

const PKEY = "oin-player", SESSION = "oin-session", SPEEDS = [1, 1.25, 1.5, 2];
const Ctx = createContext(null);

export function fmt(n) { n = Math.max(0, Math.floor(n)); return Math.floor(n / 60) + ":" + ("0" + (n % 60)).slice(-2); }
function readPlayer() {
  const p = load(PKEY, null);
  if (p && p.playing && posOf(p) >= p.dur) { p.playing = false; p.pos = 0; p.at = Date.now(); }
  // A page load can't resume real audio on its own (autoplay rules): come back paused where it was.
  else if (p && p.playing && p.src) { p.pos = posOf(p); p.playing = false; p.at = Date.now(); }
  return p;
}
function posOf(P) { return !P ? 0 : P.playing ? Math.min(P.dur, P.pos + (Date.now() - P.at) / 1000 * P.speed) : P.pos; }

export function ShellProvider({ children }) {
  const [P, setP] = useState(readPlayer);
  const [, tick] = useState(0);
  const [session, setSession] = useState(() => load(SESSION, null));
  const [loginOpen, setLoginOpen] = useState(false);
  const [epoch, setEpoch] = useState(0);   // bumps when a sync changed saved/followed lists, to refresh the page
  // Pull the account's saved stories and follows; tell listeners and refresh if anything changed.
  const runSync = useCallback(() => {
    syncNow().then((changed) => { if (changed) { dispatchEvent(new Event("oin:data")); setEpoch((n) => n + 1); } });
  }, []);
  const pRef = useRef(P);
  pRef.current = P;
  const audio = useRef(null);

  const commit = useCallback((next) => {
    pRef.current = next; setP(next);
    try { if (next) localStorage.setItem(PKEY, JSON.stringify(next)); else localStorage.removeItem(PKEY); } catch (e) {}
  }, []);

  // The position is worked out from a timestamp, so it carries on across page loads.
  useEffect(() => {
    if (!P) return undefined;
    const t = setInterval(() => {
      const c = pRef.current;
      if (c && c.playing && posOf(c) >= c.dur) commit({ ...c, playing: false, pos: 0, at: Date.now() });
      else tick((n) => n + 1);
    }, 250);
    return () => clearInterval(t);
  }, [!!P, commit]);

  // Real audio: one <audio> element lives here, so narration keeps playing across pages.
  // Items without a source (no audio file yet) fall back to the timestamp clock above.
  const src = P && P.src ? P.src : "";
  useEffect(() => {
    if (!src) { if (audio.current) { audio.current.pause(); audio.current.removeAttribute("src"); } return undefined; }
    if (!audio.current) audio.current = new Audio();
    const a = audio.current;
    a.preload = "metadata";
    const onEnd = () => { const c = pRef.current; if (c) commit({ ...c, playing: false, pos: 0, at: Date.now() }); };
    const onMeta = () => { const c = pRef.current; if (c && isFinite(a.duration) && a.duration > 0 && Math.abs(a.duration - c.dur) > 1) commit({ ...c, dur: a.duration }); };
    // Keep the clock anchored to the real playhead (buffering, seeking, tab throttling).
    const onSync = () => { const c = pRef.current; if (c && c.playing) pRef.current = { ...c, pos: a.currentTime, at: Date.now() }; };
    const onFail = () => { const c = pRef.current; if (c && c.playing) commit({ ...c, pos: posOf(c), playing: false, at: Date.now() }); };
    a.addEventListener("ended", onEnd); a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("timeupdate", onSync); a.addEventListener("error", onFail);
    if (a.getAttribute("src") !== src) {
      a.src = src;
      const c = pRef.current;
      if (c && c.pos) a.addEventListener("loadedmetadata", () => { try { a.currentTime = c.pos; } catch (e) {} }, { once: true });
    }
    return () => {
      a.removeEventListener("ended", onEnd); a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("timeupdate", onSync); a.removeEventListener("error", onFail);
    };
  }, [src, commit]);
  const playing = !!(P && P.playing), speed = P ? P.speed : 1;
  useEffect(() => {
    const a = audio.current;
    if (!a || !src) return;
    a.playbackRate = speed;
    if (playing) {
      const r = a.play();
      if (r && r.catch) r.catch(() => { const c = pRef.current; if (c && c.playing) commit({ ...c, pos: posOf(c), playing: false, at: Date.now() }); });
    } else a.pause();
  }, [playing, speed, src, commit]);

  // A saved session is only trusted while Firebase still has the user: if they signed out
  // elsewhere (or the session was a leftover), clear it. Firebase loads lazily, only here.
  useEffect(() => {
    if (!load(SESSION, null)) return;
    import("../../auth.js").then((m) => m.restoredUser()).then((u) => {
      if (!u) { try { localStorage.removeItem(SESSION); } catch (e) {} clearLocal(); setSession(null); }
      else runSync();
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === PKEY) setP(readPlayer());
      if (e.key === SESSION) setSession(load(SESSION, null));
    };
    const onLogin = () => setLoginOpen(true);
    addEventListener("storage", onStorage); addEventListener("oin:login", onLogin);
    return () => { removeEventListener("storage", onStorage); removeEventListener("oin:login", onLogin); };
  }, []);

  const value = useMemo(() => {
    const isMine = (id, kind) => !!P && String(P.id) === String(id) && P.kind === kind;
    return {
      player: P,
      position: posOf(P),
      isMine,
      start(item) {
        commit({ id: item.id, kind: item.kind, title: item.title, url: item.url, src: item.src || null, dur: item.dur, pos: 0, playing: true, at: Date.now(), speed: P ? P.speed : 1 });
      },
      toggle() { const c = pRef.current; if (c) commit({ ...c, pos: posOf(c), playing: !c.playing, at: Date.now() }); },
      seek(frac) {
        const c = pRef.current; if (!c) return;
        const pos = Math.max(0, Math.min(1, frac)) * c.dur;
        if (c.src && audio.current) { try { audio.current.currentTime = pos; } catch (e) {} }
        commit({ ...c, pos, at: Date.now() });
      },
      cycleSpeed() { const c = pRef.current; if (c) commit({ ...c, pos: posOf(c), at: Date.now(), speed: SPEEDS[(SPEEDS.indexOf(c.speed) + 1) % SPEEDS.length] }); },
      closePlayer() { if (audio.current) audio.current.pause(); commit(null); },
      session,
      signedIn: !!session,
      dataEpoch: epoch,
      setSession(u) {
        if (u) { save(SESSION, u); runSync(); }
        else { try { localStorage.removeItem(SESSION); } catch (e) {} clearLocal(); import("../../push.js").then((m) => m.disablePush()).catch(() => {}).then(() => import("../../auth.js")).then((m) => m.signOutEverywhere()).catch(() => {}); }
        setSession(u);
      },
      loginOpen,
      openLogin() { setLoginOpen(true); },
      closeLogin() { setLoginOpen(false); },
    };
  }, [P, session, loginOpen, epoch, runSync, commit, P && P.playing ? Date.now() : 0]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

const FALLBACK = {
  player: null, position: 0, isMine: () => false, start() {}, toggle() {}, seek() {}, cycleSpeed() {}, closePlayer() {},
  session: null, signedIn: false, dataEpoch: 0, setSession() {}, loginOpen: false, openLogin() { dispatchEvent(new Event("oin:login")); }, closeLogin() {},
};
export function useShell() { return useContext(Ctx) || FALLBACK; }
