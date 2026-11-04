export default function Download() {
  return (
    <main className="wrap doc">
      <h1>Install the Android app</h1>
      <p className="updated">Open Indian News &middot; direct download</p>

      <p>
        Open Indian News is not on Google Play yet. Until it is, the Android build can be installed
        directly from the link below. It is the same release build that will be published to Play.
      </p>

      <p style={{ margin: "28px 0" }}>
        <a className="btn btn-primary" href="/download/apk">
          Download the APK
        </a>
      </p>

      <h2>How to install</h2>
      <ol>
        <li>Tap <strong>Download the APK</strong> above on an Android phone.</li>
        <li>Open the downloaded file. Android will ask whether to allow installs from this source &mdash; this prompt is normal for any app installed outside the Play Store.</li>
        <li>Allow it, then tap <strong>Install</strong>.</li>
      </ol>
      <p>
        Android 8 and above ask for that permission per app, so allowing it here does not change any
        other setting on the device. It can be turned off again afterwards under
        <em> Settings &rarr; Apps &rarr; Special access &rarr; Install unknown apps</em>.
      </p>

      <h2>Signing in</h2>
      <p>
        The app can be used without an account. Signing in (with Google, or an email and password) is
        needed only for features tied to a profile &mdash; saved stories synced across devices,
        notification scheduling, and following stories, topics and sources. Donating does not need an
        account.
      </p>

      <h2>About donations in the app</h2>
      <div className="box">
        <p>
          The app is free to read and carries no ads. An optional <strong>Premium</strong> plan (bought
          through Google Play) adds extras such as the Context tab. You can also make an
          <strong> optional donation</strong> towards running costs, which <strong>unlocks nothing</strong>
          &mdash; no Premium features and no change of any kind inside the app.
        </p>
        <p>
          See <a href="/pricing">Pricing</a> and our
          <a href="/refunds"> Refund &amp; Cancellation Policy</a>.
        </p>
      </div>

      <h2>Requirements</h2>
      <ul>
        <li>Android 8.0 (Oreo) or later</li>
        <li>An internet connection &mdash; the feed is fetched live</li>
      </ul>

      <p>
        Trouble installing? <a href="/contact">Contact us</a> &mdash; email is read by a person.
      </p>
    </main>
  );
}
