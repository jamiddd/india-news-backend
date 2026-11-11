// Web push: gets this browser an FCM token and registers it with the account, so the pushes the
// Android app gets (breaking, daily digest, followed stories and topics, morning brief) also reach
// Chrome. The service worker (app/static/sw.js) shows them. Everything here is best effort and
// never throws: no push is better than a broken page.
import config, { vapidKey } from "./firebaseConfig.js";
import { idToken } from "./auth.js";
import { toast } from "./components/shell/toast.js";

const TOKEN = "oin-push-token", SESSION = "oin-session", PREFS = "oin-prefs";
const rd = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } };
const userId = () => (rd(SESSION, null) || {}).user_id || null;
const wantsPush = () => rd(PREFS, {}).notify !== false;   // the Settings "Notifications" switch, on unless turned off

// Whether this browser can do web push at all (and the key has been set up).
export const pushSupported = () => !!vapidKey && typeof Notification !== "undefined" && "serviceWorker" in navigator && "PushManager" in window;

async function post(path, token) {
  const id = userId(), auth = await idToken();
  if (!id || !auth) return false;
  const r = await fetch("/api/v1/users/" + encodeURIComponent(id) + path, {
    method: "POST", credentials: "same-origin",
    headers: { Authorization: "Bearer " + auth, "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ fcm_token: token, platform: "web" }),
  });
  return r.ok;
}

let listening = false;
async function messaging() {
  const [app, m] = await Promise.all([import("firebase/app"), import("firebase/messaging")]);
  if (!(await m.isSupported())) return null;
  const a = app.getApps().length ? app.getApp() : app.initializeApp(config);
  return { m, msg: m.getMessaging(a) };
}

// Gets (or refreshes) this browser's token and registers it. Needs the browser permission already granted.
export async function enablePush() {
  try {
    if (!pushSupported() || Notification.permission !== "granted" || !userId()) return false;
    const fb = await messaging();
    if (!fb) return false;
    const reg = await navigator.serviceWorker.ready;
    const token = await fb.m.getToken(fb.msg, { vapidKey, serviceWorkerRegistration: reg });
    if (!token) return false;
    // Re-registered on every load: the server only touches updated_at, and a token handed to another
    // account on a shared browser moves to whoever is signed in now.
    if (!(await post("/device-tokens", token))) return false;
    try { localStorage.setItem(TOKEN, token); } catch (e) { /* private mode */ }
    if (!listening) {
      listening = true;
      // A focused tab shows no system notification, so say it in the page instead.
      fb.m.onMessage(fb.msg, (p) => { const d = (p && p.data) || {}; if (d.title) toast(d.title + (d.body ? ": " + d.body : ""), 6000); });
    }
    return true;
  } catch (e) { return false; }
}

// Turns push off for this browser: the server forgets the token and so does Firebase.
export async function disablePush() {
  try {
    const token = localStorage.getItem(TOKEN);
    if (!token) return;
    try { localStorage.removeItem(TOKEN); } catch (e) { /* ignore */ }
    await post("/device-tokens/unregister", token);
    if (pushSupported()) { const fb = await messaging(); if (fb) await fb.m.deleteToken(fb.msg); }
  } catch (e) { /* signed out or offline: the server drops dead tokens itself */ }
}

// On page load: keep a signed-in reader who has push on registered.
export function syncPush() {
  if (pushSupported() && userId() && wantsPush() && Notification.permission === "granted") return enablePush();
  return Promise.resolve(false);
}
