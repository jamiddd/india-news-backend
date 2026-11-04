export default function About() {
  return (
    <main className="wide abt" id="top">
      <header className="abt-hero">
        <div>
          <p className="eyebrow">About Open Indian News</p>
          <h1>The same story, told fifteen ways. We show you all of them, once.</h1>
          <p className="abt-lede">Open Indian News is an Android news app that groups every outlet's coverage of a story into a single item, summarises it, and shows how different publishers are framing the same facts. It is built and operated by a single independent developer in Assam, India.</p>
          <div className="abt-cta"><a className="btn btn-primary" href="/download">Download for Android</a><a className="btn btn-ghost" href="#how">How it works</a></div>
        </div>
        <dl className="abt-facts">
          <div><dt>Platform</dt><dd>Android, in development and preparing for release on Google Play</dd></div>
          <div><dt>Reading</dt><dd>Free to read, with no advertising anywhere</dd></div>
          <div><dt>News from</dt><dd>Publicly available feeds from Indian publishers. Every article links back to its publisher.</dd></div>
          <div><dt>Funded by</dt><dd>Optional Premium and optional donations. Nothing else.</dd></div>
          <div><dt>Last updated</dt><dd>2 October 2026</dd></div>
        </dl>
      </header>

      <nav className="abt-idx" aria-label="On this page">
        <a href="#how">How it works</a><a href="#does">What the app does</a><a href="#ads">No ads</a><a href="#source">Where the news comes from</a><a href="#funded">How it is funded</a><a href="#who">Who runs it</a>
      </nav>

      <section className="abt-sec" id="how">
        <div className="abt-l"><h2 className="sec-h"><span>How it works</span></h2><p className="abt-q">Clustering, not scrolling.</p></div>
        <div className="abt-r">
          <p className="abt-p">Most Indian news apps show you the same event over and over, wrapped in advertising. We collapse it into one item, then show you what changed between the tellings.</p>
          <ol className="abt-steps">
            <li>
              <span className="abt-n">01</span><h3>Cluster</h3>
              <p>Every article published on the same event is matched and folded into a single story, whether two outlets covered it or forty.</p>
              <div className="viz" aria-hidden="true"><div className="vrows"><i style={{width:"62%"}}></i><i style={{width:"78%"}}></i><i style={{width:"54%"}}></i></div><span className="varr">&darr;</span><div className="vcard2"><span className="favs"><i className="fv"></i><i className="fv"></i></span><i style={{width:"88%"}}></i><i style={{width:"64%"}}></i><em>One story</em></div></div>
            </li>
            <li>
              <span className="abt-n">02</span><h3>Summarise</h3>
              <p>The clustered coverage is condensed into a few bullets, enough to know what happened without opening anything.</p>
              <div className="viz" aria-hidden="true"><div className="vrows"><i style={{width:"100%"}}></i><i style={{width:"94%"}}></i><i style={{width:"98%"}}></i><i style={{width:"60%"}}></i></div><span className="varr">&darr;</span><div className="vcard2"><b>&bull;</b><i style={{width:"88%"}}></i><b>&bull;</b><i style={{width:"76%"}}></i><b>&bull;</b><i style={{width:"82%"}}></i><em>Three bullets</em></div></div>
            </li>
            <li>
              <span className="abt-n">03</span><h3>Compare the framing</h3>
              <p>Each outlet's angle on that same story is set out side by side, so the difference between the tellings is the thing you can see.</p>
              <div className="viz" aria-hidden="true"><div className="vcard2 wide2"><div className="vfr"><span className="fv"></span><i style={{width:"30%"}}></i><i style={{width:"46%"}}></i></div><div className="vfr"><span className="fv"></span><i style={{width:"38%"}}></i><i style={{width:"36%"}}></i></div><div className="vfr"><span className="fv"></span><i style={{width:"26%"}}></i><i style={{width:"50%"}}></i></div><em>Same facts, different lead</em></div></div>
            </li>
          </ol>
        </div>
      </section>

      <section className="abt-sec" id="does">
        <div className="abt-l"><h2 className="sec-h"><span>What the app does</span></h2></div>
        <div className="abt-r">
          <div className="abt-grid">
            <div><h3>Story clustering</h3><p>Articles published about the same event by different outlets are matched and folded into one story, so the same event is read once rather than scrolled past repeatedly.</p></div>
            <div><h3>Media framing</h3><p>For stories covered by several outlets, the app sets out the angle each publisher led with, side by side.</p></div>
            <div><h3>Short summaries</h3><p>Each story carries a short summary, so a reader can decide whether the full coverage is worth their time.</p></div>
            <div><h3>No advertising</h3><p>The reading experience carries no ads: no banners, no interstitials, and nothing that follows you between stories.</p></div>
            <div><h3>Saved stories and digests</h3><p>Save what you want to come back to, and have a digest delivered at the times of day you choose.</p></div>
            <div><h3>Daily games and services</h3><p>Crossword, sudoku, word search, spelling bee, word ladder, Daily Word and a daily quiz, plus horoscope, word of the day, quote of the day, on this day and a community poll.</p></div>
          </div>
        </div>
      </section>

      <section className="abt-band" id="ads">
        <div className="abt-big">No ads.<br /><em>Anywhere.</em></div>
        <div>
          <p>The only news app with zero advertising. Not in the feed, not between stories, not in a banner, an interstitial or a sponsored card dressed up as news.</p>
          <p className="abt-small">This holds on every plan, free or Premium.</p>
        </div>
      </section>

      <section className="abt-sec" id="source">
        <div className="abt-l"><h2 className="sec-h"><span>Where the news comes from</span></h2></div>
        <div className="abt-r">
          <p className="abt-p">The app aggregates publicly available news feeds from Indian publishers. Every article links back to the original publisher, who remains the source and the owner of that reporting. Open Indian News does not write news of its own.</p>
          <p className="abt-p">If you publish news and would like to be included, or removed, please <a href="/contact">get in touch</a>. Requests are handled by a person, not a queue.</p>
          <p className="abt-p">Found something broken, or think a story is being framed badly? <a href="/feedback">Tell us</a>. It goes straight to the person who builds the app, and you do not need an account.</p>
        </div>
      </section>

      <section className="abt-sec" id="funded">
        <div className="abt-l"><h2 className="sec-h"><span>How it is funded</span></h2></div>
        <div className="abt-r">
          <p className="abt-p">The app is free to read and carries no advertising. It is funded by an optional <a href="/pricing">Premium</a> plan and by optional donations towards running costs.</p>
          <div className="abt-plans">
            <div><span className="abt-tag">Free</span><b>Free to read</b><p>The feed, clustering, summaries, media framing, saved stories, notifications and today's games cost nothing.</p></div>
            <div><span className="abt-tag">Premium</span><b>&#8377;49 a month, or &#8377;490 once</b><p>Adds the Context tab (Daily Brief, timelines, explainers), back-dated games and all accent colours. Bought through Google Play.</p></div>
            <div><span className="abt-tag">Donate</span><b>Optional, and it unlocks nothing</b><p>A contribution towards servers, data and the services the app depends on. No Premium features, no change inside the app.</p></div>
          </div>
          <p className="abt-p abt-small">See <a href="/pricing">Pricing</a>, <a href="/donate">Donate</a> and the <a href="/refunds">Refund &amp; Cancellation Policy</a>.</p>
        </div>
      </section>

      <section className="abt-sec" id="who">
        <div className="abt-l"><h2 className="sec-h"><span>Who runs it</span></h2></div>
        <div className="abt-r">
          <p className="abt-p"><b>Built by one person, in Assam.</b> Open Indian News is operated by an individual developer based in Assam, India.</p>
          <dl className="abt-contact">
            <div><dt>Email</dt><dd><a href="mailto:jamiddeka1@gmail.com">jamiddeka1@gmail.com</a></dd></div>
            <div><dt>Phone</dt><dd>+91 60034 99695</dd></div>
            <div><dt>Availability</dt><dd>An Android app, currently in development and preparing for release on Google Play. This page will link to the listing once it is live.</dd></div>
          </dl>
          <div className="abt-cta" style={{marginTop:"20px"}}><a className="btn btn-primary" href="/download">Download for Android</a><a className="btn btn-ghost" href="/contact">Contact</a></div>
        </div>
      </section>
    </main>
  );
}
