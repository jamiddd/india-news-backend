// Site-wide shell state, replacing the globals of prototype/static/shell.js (PROTO.player,
// PROTO.openLogin, PROTO.signedIn). Pages use useShell() to start narration or open sign-in.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { load, save } from "./toast.js";

const PKEY = "oin-player", SESSION = "oin-session", SPEEDS = [1, 1.25, 1.5, 2];
const Ctx = createContext(null);

export function fmt(n) { n = Math.max(0, Math.floor(n)); return Math.floor(n / 60) + ":" + ("0" + (n % 60)).slice(-2); }
function readPlayer() {
  const p = load(PKEY, null);
  if (p && p.playing && posOf(p) >= p.dur) { p.playing = false; p.pos = 0; p.at = Date.now(); }
  return p;
}
function posOf(P) { return !P ? 0 : P.playing ? Math.min(P.dur, P.pos + (Date.now() - P.at) / 1000 * P.speed) : P.pos; }

export function ShellProvider({ children }) {
  const [P, setP] = useState(readPlayer);
  const [, tick] = useState(0);
  const [session, setSession] = useState(() => load(SESSION, null));
  const [loginOpen, setLoginOpen] = useState(false);
  const pRef = useRef(P);
  pRef.current = P;

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
        commit({ id: item.id, kind: item.kind, title: item.title, url: item.url, dur: item.dur, pos: 0, playing: true, at: Date.now(), speed: P ? P.speed : 1 });
      },
      toggle() { const c = pRef.current; if (c) commit({ ...c, pos: posOf(c), playing: !c.playing, at: Date.now() }); },
      seek(frac) { const c = pRef.current; if (c) commit({ ...c, pos: Math.max(0, Math.min(1, frac)) * c.dur, at: Date.now() }); },
      cycleSpeed() { const c = pRef.current; if (c) commit({ ...c, pos: posOf(c), at: Date.now(), speed: SPEEDS[(SPEEDS.indexOf(c.speed) + 1) % SPEEDS.length] }); },
      closePlayer() { commit(null); },
      session,
      signedIn: !!session,
      setSession(u) { if (u) save(SESSION, u); else { try { localStorage.removeItem(SESSION); } catch (e) {} } setSession(u); },
      loginOpen,
      openLogin() { setLoginOpen(true); },
      closeLogin() { setLoginOpen(false); },
    };
  }, [P, session, loginOpen, commit, P && P.playing ? Date.now() : 0]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

const FALLBACK = {
  player: null, position: 0, isMine: () => false, start() {}, toggle() {}, seek() {}, cycleSpeed() {}, closePlayer() {},
  session: null, signedIn: false, setSession() {}, loginOpen: false, openLogin() { dispatchEvent(new Event("oin:login")); }, closeLogin() {},
};
export function useShell() { return useContext(Ctx) || FALLBACK; }
