// Real sign-in: Firebase Auth in the browser (Google and email/password, same project as the
// Android app), then POST /api/v1/auth/login with the Firebase ID token, which the backend
// verifies before creating or loading the account. Firebase is imported lazily so pages that
// never sign anyone in don't download it.
import config from "./firebaseConfig.js";

export const configured = () => !!(config.apiKey && config.appId);

let ready = null;
function fb() {
  if (!ready) {
    ready = Promise.all([import("firebase/app"), import("firebase/auth")]).then(([app, A]) => {
      const a = app.getApps().length ? app.getApp() : app.initializeApp(config);
      return { A, auth: A.getAuth(a) };
    });
    ready.catch(() => { ready = null; });
  }
  return ready;
}

export class AuthError extends Error {
  constructor(message, field) { super(message); this.name = "AuthError"; this.field = field || null; }
}

// Firebase error codes -> messages for people (and which form field they belong to).
export function explain(e) {
  if (e instanceof AuthError) return e;
  const c = (e && e.code) || "";
  const m = {
    "auth/invalid-email": ["That doesn't look like an email address.", "email"],
    "auth/missing-email": ["Enter your email.", "email"],
    "auth/user-not-found": ["No account uses that email. Create one instead.", "email"],
    "auth/invalid-credential": ["That email and password don't match.", "pw"],
    "auth/wrong-password": ["That email and password don't match.", "pw"],
    "auth/invalid-login-credentials": ["That email and password don't match.", "pw"],
    "auth/email-already-in-use": ["That email already has an account. Sign in instead.", "email"],
    "auth/weak-password": ["Use at least 6 characters.", "pw"],
    "auth/missing-password": ["Enter your password.", "pw"],
    "auth/user-disabled": ["This account has been disabled."],
    "auth/too-many-requests": ["Too many attempts. Wait a few minutes and try again."],
    "auth/network-request-failed": ["Couldn't reach the sign-in service. Check your connection."],
    "auth/popup-blocked": ["Your browser blocked the Google window. Allow pop-ups for this site and try again."],
    "auth/unauthorized-domain": ["Sign-in isn't enabled for this web address yet."],
    "auth/operation-not-allowed": ["This sign-in method isn't enabled yet."],
    "auth/account-exists-with-different-credential": ["That email is already registered another way. Use that method instead."],
  }[c];
  return new AuthError(m ? m[0] : "Something went wrong. Try again.", m ? m[1] : null);
}
export const cancelled = (e) => !!e && (e.code === "auth/popup-closed-by-user" || e.code === "auth/cancelled-popup-request");

async function guard(fn) {
  if (!configured()) throw new AuthError("Sign-in isn't available yet. Please try again soon.");
  try { return await fn(await fb()); } catch (e) { if (cancelled(e)) throw e; throw explain(e); }
}

// Exchange the Firebase ID token for our account. Returns the session the site keeps.
async function exchange(user, provider) {
  const token = await user.getIdToken();
  const r = await fetch("/api/v1/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, credentials: "same-origin",
    body: JSON.stringify({ uid: token, provider, email: user.email || "", display_name: user.displayName || (user.email || "").split("@")[0] }),
  });
  if (r.status === 403) throw new AuthError("Confirm your email first. Check your inbox for the link.");
  if (!r.ok) throw new AuthError("We couldn't finish signing you in. Try again.");
  const d = await r.json();
  return { user_id: d.user_id, name: d.display_name || user.displayName || d.email, email: d.email, photo: d.photo_url || null };
}

export const signInWithGoogle = () => guard(async ({ A, auth }) => {
  const p = new A.GoogleAuthProvider(); p.setCustomParameters({ prompt: "select_account" });
  const { user } = await A.signInWithPopup(auth, p);
  return exchange(user, "google");
});

// Email sign-in. Returns { verified: false, email } when the address still needs confirming.
export const signInWithEmail = (email, pw) => guard(async ({ A, auth }) => {
  const { user } = await A.signInWithEmailAndPassword(auth, email, pw);
  if (!user.emailVerified) { try { await A.sendEmailVerification(user); } catch (e) { /* rate limited: the earlier link still works */ } return { verified: false, email: user.email }; }
  return { verified: true, session: await exchange(user, "email") };
});

export const signUpWithEmail = (name, email, pw) => guard(async ({ A, auth }) => {
  const { user } = await A.createUserWithEmailAndPassword(auth, email, pw);
  if (name) { try { await A.updateProfile(user, { displayName: name }); } catch (e) { /* cosmetic */ } }
  await A.sendEmailVerification(user);
  return { email: user.email };
});

// "I've confirmed": refresh the user and finish sign-in if the link was clicked.
export const finishVerification = () => guard(async ({ A, auth }) => {
  const user = auth.currentUser;
  if (!user) throw new AuthError("Your sign-in expired. Please sign in again.");
  await user.reload();
  if (!auth.currentUser.emailVerified) throw new AuthError("We haven't seen your confirmation yet. Click the link in the email, then try again.");
  return exchange(auth.currentUser, "email");
});

export const resendVerification = () => guard(async ({ A, auth }) => {
  if (!auth.currentUser) throw new AuthError("Your sign-in expired. Please sign in again.");
  await A.sendEmailVerification(auth.currentUser);
});

export const sendReset = (email) => guard(async ({ A, auth }) => { await A.sendPasswordResetEmail(auth, email); });

// "Use a different email": drop the half-made account, then sign out.
export const abandonSignUp = () => guard(async ({ A, auth }) => {
  const u = auth.currentUser;
  if (u && !u.emailVerified) { try { await A.deleteUser(u); } catch (e) { /* needs a recent login: signing out is enough */ } }
  await A.signOut(auth);
});

export async function signOutEverywhere() {
  if (!configured()) return;
  try { const { A, auth } = await fb(); await A.signOut(auth); } catch (e) { /* already signed out */ }
}

// The signed-in Firebase user once Firebase has restored its own session, else null.
export async function restoredUser() {
  if (!configured()) return null;
  const { A, auth } = await fb();
  return new Promise((res) => { const off = A.onAuthStateChanged(auth, (u) => { off(); res(u); }); });
}

// For calls to /users/{id}/... that need Authorization: Bearer <token>.
export async function idToken() {
  if (!configured()) return null;
  const { auth } = await fb();
  return auth.currentUser ? auth.currentUser.getIdToken() : null;
}
