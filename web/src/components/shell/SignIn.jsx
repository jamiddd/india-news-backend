import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useShell } from "./ShellContext.jsx";
import { load, save, toast } from "./toast.js";

// Sign-in modal, after the Android LoginScreen. Pages: choose (Google / email) -> email
// (sign in or create account) -> verify. Accounts live in localStorage (oin-accounts) and
// the session in oin-session; nothing is sent anywhere. "taken@example.com" is always taken.
const AK = "oin-accounts";
const FRESH = { page: "choose", signUp: false, name: "", email: "", pw: "", showPw: false, err: null, busy: "", resend: 0 };
const validEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

const G = (
  <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" /><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" /><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" /><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" /></svg>
);
const mail = (s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></svg>;
const mmss = (n) => "0:" + ("0" + n).slice(-2);

export default function SignIn() {
  const sh = useShell();
  const [st, setSt] = useState(FRESH);
  const card = useRef(null), opener = useRef(null), alive = useRef(false);
  const set = (p) => setSt((s) => ({ ...s, ...p }));

  useEffect(() => {
    if (!sh.loginOpen) return undefined;
    opener.current = document.activeElement; alive.current = true;
    setSt(FRESH); document.body.style.overflow = "hidden";
    const esc = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", esc);
    return () => { alive.current = false; document.body.style.overflow = ""; document.removeEventListener("keydown", esc); };
  }, [sh.loginOpen]);

  // keep focus sensible after each screen/error change
  useEffect(() => {
    if (!sh.loginOpen || !card.current) return;
    const f = (st.err && st.err.field && card.current.querySelector("#a-" + st.err.field)) || card.current.querySelector("input") || card.current.querySelector(".ab");
    if (f) f.focus();
  }, [sh.loginOpen, st.page, st.signUp, st.err]);

  useEffect(() => {
    if (st.page !== "verify" || !st.resend) return undefined;
    const t = setTimeout(() => set({ resend: st.resend - 1 }), 1000);
    return () => clearTimeout(t);
  }, [st.page, st.resend]);

  if (!sh.loginOpen) return null;

  function close(ok) { sh.closeLogin(); if (!ok && opener.current && opener.current.focus) opener.current.focus(); }
  function fail(msg, field) { set({ err: { msg, field: field || null }, busy: "" }); }
  function done(user) { sh.setSession(user); close(true); toast("Welcome!"); }
  function goVerify(email) { set({ page: "verify", busy: "", err: null, resend: 30, email }); }

  function submit(e) {
    e.preventDefault();
    const email = st.email.trim().toLowerCase(), accounts = load(AK, []), acc = accounts.filter((a) => a.email === email)[0];
    if (!email) return fail("Enter your email.", "email");
    if (!st.pw) return fail("Enter your password.", "pw");
    if (!validEmail(email)) return fail("That doesn't look like an email address.", "email");
    if (st.signUp) {
      if (st.pw.length < 6) return fail("Use at least 6 characters.", "pw");
      if (acc || email === "taken@example.com") return fail("That email already has an account. Sign in instead.", "email");
    } else {
      if (!acc) return fail("No account uses that email. Create one instead.", "email");
      if (acc.pw !== st.pw) return fail("That email and password don't match.", "pw");
    }
    set({ err: null, busy: "email" });
    setTimeout(() => {
      if (!alive.current) return;
      if (st.signUp) {
        accounts.push({ name: st.name.trim() || email.split("@")[0], email, pw: st.pw, verified: false });
        save(AK, accounts); goVerify(email);
      } else if (!acc.verified) goVerify(email);
      else done({ name: acc.name, email: acc.email });
    }, 700);
  }

  function forgot() {
    const em = st.email.trim().toLowerCase();
    if (!em) return fail("Enter your email, then tap Forgot password.", "email");
    if (!validEmail(em)) return fail("That doesn't look like an email address.", "email");
    if (!load(AK, []).some((x) => x.email === em)) return fail("No account uses that email. Create one instead.", "email");
    set({ err: null }); toast("Password reset link sent to " + em);
  }
  function confirmed() {
    set({ busy: "confirmed" });
    setTimeout(() => {
      if (!alive.current) return;
      const accounts = load(AK, []), acc = accounts.filter((x) => x.email === st.email)[0];
      if (!acc) return fail("Something went wrong. Try again.");
      acc.verified = true; save(AK, accounts); done({ name: acc.name, email: acc.email });
    }, 700);
  }
  function other() {
    save(AK, load(AK, []).filter((x) => !(x.email === st.email && !x.verified)));
    set({ page: "email", signUp: true, email: "", pw: "", err: null });
  }
  function google() {
    set({ busy: "google", err: null });
    setTimeout(() => { if (alive.current) done({ name: "Sample Reader", email: "reader@example.com" }); }, 900);
  }

  const bad = (id) => st.err && st.err.field === id;
  const line = st.err && !st.err.field ? <div className="af-err gen" role="alert">{st.err.msg}</div> : null;
  function field(id, label, type, val, ph, extra) {
    return (
      <div className="af"><label htmlFor={"a-" + id}>{label}</label>
        <div className={"af-box" + (bad(id) ? " bad" : "")}>
          <input id={"a-" + id} type={type} value={val} placeholder={ph || ""} autoComplete={id === "pw" ? (st.signUp ? "new-password" : "current-password") : id === "email" ? "email" : "name"}
            aria-invalid={bad(id) ? "true" : undefined} onChange={(e) => set({ [id]: e.target.value })} />{extra}</div>
        {bad(id) ? <div className="af-err" role="alert">{st.err.msg}</div> : null}
      </div>
    );
  }

  let body;
  if (st.page === "choose") {
    body = (
      <>
        <h2 id="auth-t">Your news, on every device you use</h2><p className="auth-sub">An account keeps what you've set up here, and unlocks follows.</p>
        <ul className="auth-perks"><li><b>Topics and publishers sync</b><span>Your tabs look the same on a new phone or browser.</span></li>
          <li><b>Follow stories and topics</b><span>Get a notification when a story you follow moves.</span></li></ul>
        {line}
        <div className="auth-btns">
          <button type="button" className="ab filled" disabled={!!st.busy} onClick={google}>{st.busy === "google" ? <i className="spin"></i> : G}<span>Continue with Google</span></button>
          <button type="button" className="ab" disabled={!!st.busy} onClick={() => set({ page: "email", err: null })}>{mail(18)}<span>Continue with email</span></button>
        </div>
        <p className="auth-legal">By continuing you agree to our <Link to="/terms" onClick={() => close(true)}>Terms of Service</Link> and <Link to="/privacy" onClick={() => close(true)}>Privacy Policy</Link>.</p>
      </>
    );
  } else if (st.page === "email") {
    const eye = <button type="button" className="af-eye" aria-label={st.showPw ? "Hide password" : "Show password"} onClick={() => set({ showPw: !st.showPw })}>{st.showPw ? "Hide" : "Show"}</button>;
    body = (
      <>
        <button type="button" className="auth-back" aria-label="Back" onClick={() => set({ page: "choose", err: null })}>&larr;</button>
        <h2 id="auth-t">{st.signUp ? "Create your account" : "Sign in with email"}</h2>
        <div className="auth-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={!st.signUp} className={!st.signUp ? "on" : ""} onClick={() => set({ signUp: false, err: null })}>Sign in</button>
          <button type="button" role="tab" aria-selected={st.signUp} className={st.signUp ? "on" : ""} onClick={() => set({ signUp: true, err: null })}>Create account</button>
        </div>
        <form className="auth-form" noValidate onSubmit={submit}>
          {st.signUp ? field("name", "Name", "text", st.name, "What should we call you?") : null}
          {field("email", "Email", "email", st.email, "you@example.com")}
          {field("pw", "Password", st.showPw ? "text" : "password", st.pw, st.signUp ? "At least 6 characters" : "", eye)}
          {st.signUp ? <p className="auth-note">We'll send a link to confirm it's your email.</p> : <button type="button" className="auth-link" onClick={forgot}>Forgot password?</button>}
          {line}
          <button type="submit" className="ab filled" disabled={!!st.busy}>{st.busy === "email" ? <i className="spin"></i> : null}<span>{st.signUp ? "Create account" : "Sign in"}</span></button>
        </form>
      </>
    );
  } else {
    body = (
      <>
        <button type="button" className="auth-back" aria-label="Back" onClick={() => set({ page: "email", err: null })}>&larr;</button>
        <div className="auth-mail">{mail(30)}</div><h2 id="auth-t">Check your inbox</h2>
        <p className="auth-sub">We sent a confirmation link to <b>{st.email}</b>.</p>
        <ol className="auth-steps"><li>Open the email from Open Indian News.</li><li>Click the link. It opens in your browser.</li><li>Come back here. We'll carry on from there.</li></ol>
        <p className="auth-note">Not there after a minute? Check Spam or Promotions.</p>{line}
        <div className="auth-btns"><a className="ab" href={"mailto:" + st.email}><span>Open email app</span></a>
          <button type="button" className="ab filled" disabled={!!st.busy} onClick={confirmed}>{st.busy === "confirmed" ? <i className="spin"></i> : null}<span>I've confirmed, continue</span></button></div>
        <div className="auth-foot"><button type="button" className="auth-link" onClick={other}>Use a different email</button>
          <button type="button" className="auth-link" disabled={st.resend > 0} onClick={() => { goVerify(st.email); toast("Confirmation email sent again"); }}>{st.resend ? "Resend in " + mmss(st.resend) : "Resend email"}</button></div>
      </>
    );
  }

  return createPortal(
    <div className="auth" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-t" ref={card}>
        <button type="button" className="auth-x" aria-label="Close" onClick={() => close()}>&times;</button>{body}
      </div>
    </div>, document.body);
}

// Account menu (signed in): who you are, profile link, sign out. Anchored under the header button.
export function AccountMenu({ anchor, onClose }) {
  const sh = useShell(), u = sh.session, ref = useRef(null);
  useEffect(() => {
    const r = anchor.getBoundingClientRect(), m = ref.current;
    m.style.top = r.bottom + scrollY + 8 + "px";
    m.style.left = Math.max(12, Math.min(r.right + scrollX - m.offsetWidth, innerWidth - m.offsetWidth - 12)) + "px";
    m.querySelector("button.acct-out").focus();
    const away = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) onClose(); };
    const esc = (e) => { if (e.key === "Escape") { onClose(); anchor.focus(); } };
    document.addEventListener("click", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("click", away); document.removeEventListener("keydown", esc); };
  }, []);
  if (!u) return null;
  return createPortal(
    <div className="acct" role="menu" ref={ref}>
      <div className="acct-who"><b>{u.name}</b><span>{u.email}</span></div>
      <Link role="menuitem" className="acct-out" to="/profile" style={{ display: "block", padding: "12px 16px", textDecoration: "none", color: "inherit" }} onClick={onClose}>Profile</Link>
      <button type="button" role="menuitem" className="acct-out" onClick={() => { sh.setSession(null); onClose(); toast("Signed out"); }}>Sign out</button>
    </div>, document.body);
}
