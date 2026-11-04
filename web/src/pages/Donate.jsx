import { useState } from "react";

const PRESETS = [49, 99, 199, 499];

export default function Donate() {
  const [picked, setPicked] = useState(99);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ text: "", err: false });

  function submit(e) {
    e.preventDefault();
    const rupees = custom.trim() !== "" ? Number(custom) : picked || 0;
    if (!isFinite(rupees) || Math.floor(rupees) !== rupees || rupees < 1 || rupees > 10000) {
      setMsg({ text: "Enter a whole amount between \u20b91 and \u20b910,000.", err: true });
      return;
    }
    setBusy(true);
    setMsg({ text: "Starting your donation\u2026", err: false });
    fetch("/api/v1/donations/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount_paise: rupees * 100 }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((data) => {
        if (!data || !data.url) throw new Error("no url");
        window.location.href = data.url;
      })
      .catch(() => {
        setBusy(false);
        setMsg({ text: "Could not start the donation just now. Please try again in a moment, or write to us.", err: true });
      });
  }

  return (
    <main className="wrap doc">
      <h1>Donate</h1>
      <p className="updated">Open Indian News &middot; optional support towards running costs</p>

      <div className="box">
        <p>
          <strong>This is not a purchase.</strong> Donations are for people who want to keep using the
          free app, without Premium, and would like to show their appreciation for an ad-free feed. A
          donation <strong>unlocks nothing</strong> &mdash; no Premium features and no change of any kind
          inside the app. The free app, and its ad-free feed, stay the same whether you donate or not. If
          you want the extras instead, see <a href="/pricing">Premium</a>.
        </p>
      </div>

      <p>
        Donations go towards what it costs to run the app &mdash; servers, data, and the services the
        clustering and summarising depend on. It is built and paid for by one person.
      </p>

      <h2>Choose an amount</h2>
      <form id="donate-form" noValidate onSubmit={submit}>
        <div className="amounts" role="group" aria-label="Donation amount">
          {PRESETS.map((r) => (
            <button key={r} type="button" className={"amount" + (picked === r ? " is-picked" : "")} data-rupees={r}
                    onClick={() => { setPicked(r); setCustom(""); setMsg({ text: "", err: false }); }}>
              &#8377;{r}
            </button>
          ))}
        </div>

        <label className="custom" htmlFor="donate-custom">Or enter an amount (&#8377;1 &ndash; &#8377;10,000)</label>
        <input id="donate-custom" name="custom" type="number" inputMode="numeric" min="1" max="10000"
               step="1" placeholder="Amount in rupees" autoComplete="off" value={custom}
               onChange={(e) => { setCustom(e.target.value); setPicked(null); setMsg({ text: "", err: false }); }} />

        <button type="submit" className="btn btn-primary" id="donate-go" disabled={busy}>
          Continue to payment
        </button>
        <p className={"donate-msg" + (msg.err ? " err" : "")} id="donate-msg" role="status" aria-live="polite">{msg.text}</p>
      </form>

      <h2>How it works</h2>
      <ul>
        <li>Payment is handled by <strong>Razorpay</strong>. Continuing takes you to their secure payment page &mdash; card details are never entered on this site and never reach us.</li>
        <li>No account is needed to donate, and donating does not create one.</li>
        <li>We do not store your card details. We record that a payment was received, and its amount.</li>
        <li>Donations are one-off. Nothing recurring is set up, and nothing is charged again.</li>
      </ul>

      <h2>Refunds</h2>
      <p>
        If you donate by mistake or change your mind, see our
        <a href="/refunds"> Refund &amp; Cancellation Policy</a> &mdash; or just
        <a href="/contact"> write to us</a>. Our <a href="/terms">Terms of Service</a> apply.
      </p>
    </main>
  );
}
