import { useState } from "react";

export default function Feedback() {
  const [v, setV] = useState({ category: "other", message: "", name: "", email: "", website: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [msg, setMsg] = useState({ text: "", kind: "" });
  const set = (k) => (e) => { setV({ ...v, [k]: e.target.value }); if (k === "message") setMsg({ text: "", kind: "" }); };

  function submit(e) {
    e.preventDefault();
    const body = v.message.trim();
    if (body.length < 10) {
      setMsg({ text: "Tell us a little more \u2014 at least a sentence.", kind: "err" });
      return;
    }
    const email = v.email.trim();
    if (email !== "" && email.indexOf("@") < 1) {
      setMsg({ text: "That email address does not look right. Leave it blank if you do not want a reply.", kind: "err" });
      return;
    }
    setBusy(true);
    setMsg({ text: "Sending\u2026", kind: "" });
    fetch("/api/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: v.category, name: v.name, email, message: body, website: v.website }),
    })
      .then((r) => {
        if (r.status === 429) {
          setMsg({ text: "That is a lot of feedback in one hour. Try again later, or email us.", kind: "err" });
          setBusy(false);
          return;
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        setDone(true);
        setMsg({ text: "Thank you \u2014 that has been sent. If you left an email, you will hear back.", kind: "ok" });
      })
      .catch(() => {
        setMsg({ text: "That did not send. Check your connection and try again, or email jamiddeka1@gmail.com.", kind: "err" });
        setBusy(false);
      });
  }

  const status = (
    <p className={"form-msg" + (msg.kind ? " " + msg.kind : "")} id="fb-msg" role="status" aria-live="polite">{msg.text}</p>
  );

  return (
    <main className="wrap doc">
      <h1>Send Feedback</h1>
      <p className="updated">Tell us what is broken, missing, or wrong.</p>

      <p>
        This goes straight to the person who builds the app. You do not need an account, and you do not
        need to leave your name &mdash; say what happened and that is enough. Leave an email only if you
        want a reply.
      </p>

      {done ? status : (
      <form id="feedback-form" noValidate onSubmit={submit}>
        <div className="field">
          <label htmlFor="fb-category">What is this about?</label>
          <select id="fb-category" name="category" value={v.category} onChange={set("category")}>
            <option value="bug">Something is broken</option>
            <option value="story">A story is wrong or misleading</option>
            <option value="feature">An idea or a request</option>
            <option value="publisher">I publish news &mdash; include or remove my site</option>
            <option value="other">Something else</option>
          </select>
        </div>

        <div className="field">
          <label htmlFor="fb-message">Your message</label>
          <textarea id="fb-message" name="message" rows="7" required minLength="10" maxLength="5000"
                    value={v.message} onChange={set("message")}
                    placeholder="If something is broken, your device and roughly when it happened helps a lot."></textarea>
          <span className="hint"><span id="fb-count">{v.message.length}</span> / 5000</span>
        </div>

        <div className="row-2">
          <div className="field">
            <label htmlFor="fb-name">Your name <span className="opt">optional</span></label>
            <input id="fb-name" name="name" type="text" maxLength="120" autoComplete="name" value={v.name} onChange={set("name")} />
          </div>
          <div className="field">
            <label htmlFor="fb-email">Email <span className="opt">only if you want a reply</span></label>
            <input id="fb-email" name="email" type="email" maxLength="320" autoComplete="email" value={v.email} onChange={set("email")} />
          </div>
        </div>

        <div className="hp" aria-hidden="true">
          <label htmlFor="fb-website">Website</label>
          <input id="fb-website" name="website" type="text" tabIndex="-1" autoComplete="off" value={v.website} onChange={set("website")} />
        </div>

        <button type="submit" className="btn btn-primary" id="fb-go" disabled={busy}>Send feedback</button>
        {status}
      </form>
      )}

      <h2>Other ways to reach us</h2>
      <p>
        If you would rather use email, or you need our postal or business details, they are on the
        <a href="/contact"> Contact page</a>. To delete your account and data, see
        <a href="/delete-account"> Delete your account</a>.
      </p>
    </main>
  );
}
