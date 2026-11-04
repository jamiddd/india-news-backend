export default function Contact() {
  return (
    <main className="wrap doc">
      <h1>Contact Us</h1>
      <p className="updated">Open Indian News · Last updated: 4 September 2026</p>
      
      <p>
        Open Indian News is run by a single independent developer. Everything here is read by a person,
        not a ticketing system.
      </p>
      
      <div className="box">
        <strong>Just want to tell us something?</strong> The
        <a href="/feedback">feedback form</a> is the quickest way &mdash; no email client, no account, and
        you do not have to say who you are. Use the details below if you would rather write to us
        directly, or need our business details.
      </div>
      
      <div className="box">
        <strong>Email:</strong> <a href="mailto:jamiddeka1@gmail.com">jamiddeka1@gmail.com</a><br />
        <strong>Phone:</strong> <a href="tel:+916003499695">+91 60034 99695</a><br />
        <strong>Typical response time:</strong> 1&ndash;3 business days
      </div>
      
      <h2>What to write about</h2>
      <ul>
        <li><strong>Something is broken</strong> — a story that won't load, a game that won't open, a
            crash. Telling us your device model and roughly when it happened helps a lot.</li>
        <li><strong>A story is wrong or misleading</strong> — you can also report a story from inside the
            app, which sends us the story directly.</li>
        <li><strong>Your data</strong> — a copy of what we hold, or deletion of your account. You can
            delete your account yourself in Settings; email us if that doesn't work. See our
            <a href="/privacy">Privacy Policy</a>.</li>
        <li><strong>Donations and refunds</strong> — see the
            <a href="/refunds">Refund &amp; Cancellation Policy</a>. Include the payment ID
            (starting <code>pay_</code>) if you have it.</li>
        <li><strong>You publish news and want to be included, or want to be removed</strong> — we
            aggregate publicly available feeds and will honour a removal request from a publisher.</li>
      </ul>
      
      <h2>Business details</h2>
      <p>
        Open Indian News is operated as a sole proprietorship in India.
      </p>
      <div className="box">
        <strong>Registered address:</strong><br />
        2 No. Jyotinagar, KMC Building<br />
        Baruabari, PO Sonapur<br />
        Kamrup Metropolitan, Assam 782402<br />
        India
      </div>
    </main>
  );
}
