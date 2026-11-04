export default function Pricing() {
  return (
    <main className="wrap doc">
      <h1>Pricing</h1>
      <p className="updated">Open Indian News &middot; Last updated: 2 October 2026</p>

      <div className="box">
        <p>
          <strong>Open Indian News is free to read, with no ads.</strong> The news feed, story clustering,
          summaries, media framing, saved stories, notifications and today's daily games cost nothing.
          An optional <strong>Premium</strong> plan adds the extras listed below.
        </p>
      </div>

      <h2>Free and Premium</h2>
      <table>
        <tr><th>Feature</th><th>Free</th><th>Premium</th></tr>
        <tr><td>Ad-free news feed</td><td>Yes</td><td>Yes</td></tr>
        <tr><td>Stories grouped across sources, summaries and media framing</td><td>Yes</td><td>Yes</td></tr>
        <tr><td>Saved stories and scheduled digests</td><td>Yes</td><td>Yes</td></tr>
        <tr><td>Timelines and explainers (Context tab)</td><td>&mdash;</td><td>Yes</td></tr>
        <tr><td>Daily Brief and Late-Night Wrap-up, with audio (Context tab)</td><td>&mdash;</td><td>Yes</td></tr>
        <tr><td>Daily games and Word of the Day</td><td>Today's only</td><td>Any past date</td></tr>
        <tr><td>Accent colours</td><td>Default</td><td>All 18</td></tr>
      </table>

      <h2>Premium plans</h2>
      <table>
        <tr><th>Plan</th><th>Price</th><th>How it is billed</th></tr>
        <tr><td>Premium Monthly</td><td>&#8377;49 per month</td><td>Recurring. Renews automatically every month until you cancel.</td></tr>
        <tr><td>Premium All-Time</td><td>&#8377;490</td><td>One-time purchase. No renewal and no further charge.</td></tr>
      </table>
      <p>
        Premium is bought inside the app through <strong>Google Play</strong>, and Google Play handles the
        payment, billing, renewal and cancellation. You can cancel Monthly at any time in the Google Play
        Store; it stays active until the end of the period you have paid for. Premium is tied to your
        Google account, and <strong>Restore purchases</strong> in the app brings it back on a new device.
        See our <a href="/refunds">Refund &amp; Cancellation Policy</a> and
        <a href="/terms"> Terms of Service</a>. The price shown at checkout in Google Play is the price you
        pay; prices may change in future, which does not affect a plan you have already bought.
      </p>

      <h2>Donations</h2>
      <p>
        If you would rather keep using the free app than buy Premium, but would like to show your
        appreciation for an ad-free feed, you can make an <strong>optional donation</strong> towards
        running costs &mdash; servers, data and the services the app depends on.
      </p>
      <p>
        <strong>A donation unlocks nothing.</strong> It grants no Premium features and no change of any
        kind inside the app. It is a contribution towards keeping the app running, not a purchase and not
        a Premium plan. Nobody is asked to donate in order to use any part of the free app.
      </p>
      <p>
        Donations are processed by Razorpay and can be made <a href="/donate">on this site</a> or from inside the app. Amounts are chosen by the donor. Donations are separate from Premium and are not made through Google Play. See our
        <a href="/refunds"> Refund &amp; Cancellation Policy</a> for how to request a refund, and our
        <a href="/terms"> Terms of Service</a> for the terms that apply.
      </p>

      <h2>No advertising in the feed</h2>
      <p>
        The reading experience carries no advertising and no interstitials, on every plan, free or
        Premium.
      </p>

      <h2>Questions</h2>
      <p>
        Anything unclear about the above, please <a href="/contact">contact us</a> &mdash; email is read
        by a person.
      </p>
    </main>
  );
}
