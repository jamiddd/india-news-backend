// Shared behaviour for every page: theme toggle, footer year, scroll
// reveals, and the home page's framing carousel. Each block no-ops when the
// element it drives is absent, so the legal pages load the same file.
(function () {
    var root = document.documentElement;
    var saved = null;
    try { saved = localStorage.getItem("oin-theme"); } catch (e) {}
    if (saved) root.setAttribute("data-theme", saved);

    var toggle = document.getElementById("theme");
    if (toggle) toggle.addEventListener("click", function () {
      var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      var current = root.getAttribute("data-theme") || (dark ? "dark" : "light");
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("oin-theme", next); } catch (e) {}
    });

    var year = document.getElementById("year");
    if (year) year.textContent = new Date().getFullYear();

    // Hero coverflow: real top stories, three visible at once with the
    // current one centred and raised above its two neighbours, looping
    // endlessly in both directions (backend/docs/website-roadmap.md item
    // 4). Navigating doesn't rebuild the visible cards -- it keeps a
    // rolling pool of five existing card elements (farleft/left/center/
    // right/farright) and reassigns which position class each one wears,
    // so the ones that stay in the DOM physically slide between positions
    // via the CSS transition on .cf-card, the way a RecyclerView scrolls
    // its items rather than swapping content in place instantly. Each
    // card's own per-outlet framing rows (only shown while centred) cycle
    // the same way the old single-story version did: the outgoing row
    // leaves upward while the incoming one arrives from below.
    var coverflow = document.getElementById("hero-coverflow");
    if (coverflow) {
      var track = document.getElementById("cf-track");
      var prevBtn = document.getElementById("cf-prev");
      var nextBtn = document.getElementById("cf-next");
      var FRAME_STEP = 3400;
      var still = window.matchMedia("(prefers-reduced-motion: reduce)");
      // Position classes in filmstrip order -- index i is always the
      // element i steps from the left edge of the pool, and slots[2] is
      // always the centred one (see go() below, which preserves that
      // invariant on every shift).
      var POSITIONS = ["cf-p-farleft", "cf-p-left", "cf-p-center", "cf-p-right", "cf-p-farright"];

      // What's on screen at load, so the hero always shows something even
      // if the live endpoint never answers -- see the roadmap doc's "A
      // static fallback" note. A single story: side peeks and looping stay
      // off (see buildPool()) until real stories replace this.
      var stories = [{
        image: "/static/img/storycard-light.webp",
        caption: "Trump says US may strike Iran's Pickaxe Mountain nuclear site ‘very soon’.",
        count: "14 outlets",
        framing: [
          { outlet: "India Today World", headline_angle: "Focuses on Trump's warning and context of recent strikes and Iran's condemnation." },
          { outlet: "Reuters (via Google News)", headline_angle: "Brief factual statement of Trump's threat." },
          { outlet: "Al Jazeera", headline_angle: "Frames within ongoing 'Iran war live' coverage, includes Iranian military response." },
          { outlet: "NDTV", headline_angle: "Emphasizes 'fresh threat' framing of Trump's statement." },
          { outlet: "Deccan Chronicle", headline_angle: "Straightforward report on Trump's statement with added quotes on nuclear weapon prevention." },
          { outlet: "Livemint", headline_angle: "Combines Pickaxe threat with Trump's dismissive 'small potatoes' remark on conflict casualties." }
        ]
      }];
      var centerAt = 0;
      var slots = [];

      // Per-card framing auto-cycle, independent per card so a card
      // carries its own timer as it moves through positions -- only ever
      // running while that card is the one in cf-p-center (see
      // setActiveFraming). Same show/tick behaviour as the single-story
      // version: outgoing row leaves upward, incoming arrives from below.
      function attachFraming(cardEl, list) {
        var stage = cardEl.querySelector(".framer-stage");
        var dots = cardEl.querySelector(".framer-dots");
        var cards = [], buttons = [], at = 0, timer = null;
        list.forEach(function (f, i) {
          var el = document.createElement("article");
          el.className = "frame-card" + (i === 0 ? " is-active" : "");
          var b = document.createElement("b"); b.textContent = f.outlet;
          var it = document.createElement("i"); it.textContent = f.headline_angle;
          el.appendChild(b); el.appendChild(it);
          stage.appendChild(el);
          cards.push(el);
        });
        cards.forEach(function (_, i) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.setAttribute("aria-label", "Show framing " + (i + 1) + " of " + cards.length);
          btn.setAttribute("aria-current", i === 0 ? "true" : "false");
          btn.addEventListener("click", function () { show(i); start(); });
          dots.appendChild(btn);
          buttons.push(btn);
        });
        function show(next) {
          if (next === at || !cards[next]) return;
          cards[at].classList.remove("is-active");
          cards[at].classList.add("is-out");
          var prev = at;
          window.setTimeout(function () { cards[prev] && cards[prev].classList.remove("is-out"); }, 600);
          at = next;
          cards[at].classList.add("is-active");
          buttons.forEach(function (b, i) { b.setAttribute("aria-current", i === at ? "true" : "false"); });
        }
        function tick() { show((at + 1) % cards.length); }
        function start() {
          stop();
          if (!still.matches && cards.length > 1) timer = window.setInterval(tick, FRAME_STEP);
        }
        function stop() { window.clearInterval(timer); timer = null; }
        return { start: start, stop: stop };
      }

      function makeFigure(story) {
        var fig = document.createElement("figure");
        fig.className = "cf-figure";
        var img = document.createElement("img");
        img.src = story.image;
        img.alt = "";
        img.loading = "lazy";
        var cap = document.createElement("p");
        cap.className = "cf-caption";
        cap.textContent = story.caption;
        fig.appendChild(img);
        fig.appendChild(cap);
        return fig;
      }

      function setPos(el, posClass) {
        POSITIONS.forEach(function (p) { el.classList.remove(p); });
        el.classList.add(posClass);
      }

      function storyAt(offsetFromCenter) {
        var n = stories.length;
        return ((centerAt + offsetFromCenter) % n + n) % n;
      }

      function makeCard(storyIdx, posClass) {
        var story = stories[storyIdx];
        var el = document.createElement("div");
        el.className = "cf-card";
        setPos(el, posClass);
        el.appendChild(makeFigure(story));
        var body = document.createElement("div");
        body.className = "cf-body";
        var head = document.createElement("div");
        head.className = "framer-head";
        head.innerHTML = "<b>Media framing</b><span></span>";
        head.querySelector("span").textContent = story.count;
        var stage = document.createElement("div");
        stage.className = "framer-stage";
        var dots = document.createElement("div");
        dots.className = "framer-dots";
        body.appendChild(head);
        body.appendChild(stage);
        body.appendChild(dots);
        el.appendChild(body);
        el._framing = attachFraming(el, story.framing);
        // Only the left/right peeks are meaningfully clickable -- farleft/
        // farright are pointer-events:none (off-canvas) and the centred
        // card itself has nowhere further to go. Reads the class live at
        // click time rather than closing over posClass, since this same
        // element's position class changes as the pool shifts under it.
        el.addEventListener("click", function () {
          if (el.classList.contains("cf-p-left")) go(-1);
          else if (el.classList.contains("cf-p-right")) go(1);
        });
        return el;
      }

      function pauseFraming() { slots.forEach(function (el) { el._framing.stop(); }); }
      function resumeFraming() {
        if (!slots.length) return;
        var center = slots.length >= 5 ? slots[2] : slots[0];
        center._framing.start();
      }

      // Builds the initial pool from scratch -- called once at load and
      // again the moment real stories replace the fallback. Every
      // navigation after that goes through go(), which reuses/shifts these
      // same elements instead of rebuilding.
      function buildPool() {
        slots.forEach(function (el) { el._framing.stop(); });
        track.innerHTML = "";
        slots = [];
        var n = stories.length;
        coverflow.classList.toggle("has-multi", n > 1);
        if (n <= 1) {
          var only = makeCard(0, "cf-p-center");
          slots = [only];
          track.appendChild(only);
          only._framing.start();
          return;
        }
        for (var i = -2; i <= 2; i++) {
          var el = makeCard(storyAt(i), POSITIONS[i + 2]);
          slots.push(el);
          track.appendChild(el);
        }
        resumeFraming();
      }

      function go(delta) {
        var n = stories.length;
        if (n <= 1) return;
        if (delta > 0) {
          var exitingLeft = slots.shift();
          exitingLeft._framing.stop();
          exitingLeft.parentNode.removeChild(exitingLeft);
          slots.forEach(function (el, i) { setPos(el, POSITIONS[i]); });
          centerAt = (centerAt + 1) % n;
          var incomingRight = makeCard(storyAt(2), POSITIONS[4]);
          slots.push(incomingRight);
          track.appendChild(incomingRight);
        } else {
          var exitingRight = slots.pop();
          exitingRight._framing.stop();
          exitingRight.parentNode.removeChild(exitingRight);
          slots.forEach(function (el, i) { setPos(el, POSITIONS[i + 1]); });
          centerAt = (centerAt - 1 + n) % n;
          var incomingLeft = makeCard(storyAt(-2), POSITIONS[0]);
          slots.unshift(incomingLeft);
          track.insertBefore(incomingLeft, track.firstChild);
        }
        resumeFraming();
      }

      prevBtn.addEventListener("click", function () { go(-1); });
      nextBtn.addEventListener("click", function () { go(1); });

      buildPool();

      // Stop the framing auto-cycle while the reader is hovering,
      // keyboard-focused inside, or has the tab in the background -- an
      // unattended interval keeps firing transitions on a page nobody is
      // looking at.
      coverflow.addEventListener("mouseenter", pauseFraming);
      coverflow.addEventListener("mouseleave", resumeFraming);
      coverflow.addEventListener("focusin", pauseFraming);
      coverflow.addEventListener("focusout", resumeFraming);
      document.addEventListener("visibilitychange", function () {
        if (document.hidden) pauseFraming(); else resumeFraming();
      });

      // Replace the fallback with real clusters once they load, and reuse
      // the first one to illustrate the "How it works" steps below with
      // real content instead of the placeholder bar diagrams. Silently
      // keeps both fallbacks on any failure (bad response, network error,
      // empty list) -- the hero and the steps must never end up empty.
      fetch("/api/v1/public/hero").then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      }).then(function (data) {
        if (!data || !data.items || !data.items.length) return;
        stories = data.items.map(function (it) {
          var n = it.source_count;
          return {
            image: it.image_url,
            caption: it.headline,
            count: n + (n === 1 ? " outlet" : " outlets"),
            framing: it.framing || [],
            summary_bullets: it.summary_bullets || []
          };
        });
        centerAt = 0;
        buildPool();
        renderHowItWorks(stories[0]);
      }).catch(function () {});
    }

    // Illustrates the three "How it works" steps with the hero coverflow's
    // first real story instead of the abstract bar/dot placeholders
    // already in the DOM (kept as the fallback -- see the coverflow's
    // fetch above, which is the only caller of this).
    function renderHowItWorks(story) {
      var clusterViz = document.getElementById("viz-cluster");
      var summarizeViz = document.getElementById("viz-summarize");
      var compareViz = document.getElementById("viz-compare");
      if (!clusterViz || !summarizeViz || !compareViz) return;

      // Headlines/outlet names/summary bullets are real scraped/AI-generated
      // text, not markup this page wrote -- escape before the innerHTML
      // interpolation below, same as any other untrusted string.
      function esc(s) {
        var div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
      }

      var outlets = story.framing.map(function (f) { return esc(f.outlet); });
      var headline = esc(story.caption);
      var count = esc(story.count);

      if (clusterViz && outlets.length) {
        var srcRows = outlets.slice(0, 3).map(function (o) {
          return '<div class="vcard tight"><div class="viz-src">' + o + "</div></div>";
        }).join("");
        clusterViz.innerHTML =
          '<div class="vstack">' + srcRows + "</div>" +
          '<div class="vdown">↓</div>' +
          '<div class="vcard"><div class="viz-headline">' + headline + "</div>" +
          '<span class="vchip">' + count + "</span></div>";
      }

      if (summarizeViz && story.summary_bullets && story.summary_bullets.length) {
        var bullets = story.summary_bullets.slice(0, 3).map(function (b) {
          return '<div class="viz-bullet">' + esc(b) + "</div>";
        }).join("");
        summarizeViz.innerHTML =
          '<div class="vcard tight"><div class="viz-src">' + headline + "</div></div>" +
          '<div class="vdown">↓</div>' +
          '<div class="vcard">' + bullets + '<span class="vchip">Real summary</span></div>';
      }

      if (compareViz && story.framing.length > 1) {
        var rows = story.framing.slice(0, 4).map(function (f) {
          return '<div class="vrow"><span class="bar accent" style="width:3px;height:26px"></span>' +
                 '<div class="viz-frame-row"><b>' + esc(f.outlet) + "</b><i>" + esc(f.headline_angle) + "</i></div></div>";
        }).join("");
        compareViz.innerHTML = '<div class="vcard">' + rows + '<span class="vchip">Same facts, different lead</span></div>';
      }
    }

    var items = document.querySelectorAll(".reveal");
    if (!("IntersectionObserver" in window)) {
      items.forEach(function (el) { el.classList.add("in"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -10% 0px" });
    items.forEach(function (el) { io.observe(el); });
  })();

// ---------------------------------------------------------------------------
// /donate. Posts to the same endpoint the Android app uses and follows the
// Razorpay Payment Link it mints, so the web and app donation paths cannot
// drift apart. Nothing is granted on return -- there is no entitlement to
// grant, by design -- so there is no success state to handle here beyond
// leaving the page.
(function () {
  var form = document.getElementById("donate-form");
  if (!form) return;

  var buttons = Array.prototype.slice.call(form.querySelectorAll(".amount"));
  var custom = document.getElementById("donate-custom");
  var go = document.getElementById("donate-go");
  var msg = document.getElementById("donate-msg");

  function pick(button) {
    buttons.forEach(function (b) { b.classList.toggle("is-picked", b === button); });
    if (button) custom.value = "";
  }

  buttons.forEach(function (b) {
    b.addEventListener("click", function () { pick(b); say(""); });
  });
  // Typing an amount deselects the presets, so the two inputs can never
  // disagree about what is about to be charged.
  custom.addEventListener("input", function () { pick(null); say(""); });

  function say(text, isError) {
    msg.textContent = text;
    msg.classList.toggle("err", !!isError);
  }

  function chosenRupees() {
    if (custom.value.trim() !== "") return Number(custom.value);
    var picked = form.querySelector(".amount.is-picked");
    return picked ? Number(picked.getAttribute("data-rupees")) : 0;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var rupees = chosenRupees();
    if (!isFinite(rupees) || Math.floor(rupees) !== rupees || rupees < 1 || rupees > 100000) {
      say("Enter a whole amount between \u20b91 and \u20b91,00,000.", true);
      return;
    }

    go.disabled = true;
    say("Starting your donation\u2026");

    fetch("/api/v1/donations/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount_paise: rupees * 100 })
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (data) {
      if (!data || !data.url) throw new Error("no url");
      window.location.href = data.url;
    }).catch(function () {
      go.disabled = false;
      say("Could not start the donation just now. Please try again in a moment, or write to us.", true);
    });
  });
})();

// /feedback. Posts to the same endpoint an in-app feedback form would use, so
// the two can never drift. The server is the authority on what it will accept;
// the checks here exist only to save a round trip on the obvious mistakes.
(function () {
  var form = document.getElementById("feedback-form");
  if (!form) return;

  var message = document.getElementById("fb-message");
  var count = document.getElementById("fb-count");
  var go = document.getElementById("fb-go");
  var msg = document.getElementById("fb-msg");

  function say(text, kind) {
    msg.textContent = text;
    msg.classList.toggle("err", kind === "err");
    msg.classList.toggle("ok", kind === "ok");
  }

  function tally() { count.textContent = String(message.value.length); }
  message.addEventListener("input", function () { tally(); say(""); });
  tally();

  form.addEventListener("submit", function (e) {
    e.preventDefault();

    var body = message.value.trim();
    if (body.length < 10) {
      say("Tell us a little more — at least a sentence.", "err");
      message.focus();
      return;
    }

    var email = document.getElementById("fb-email").value.trim();
    // Not a validity check, just a typo check: an address we cannot reply to is
    // worse than no address, because it looks like a reply is coming.
    if (email !== "" && email.indexOf("@") < 1) {
      say("That email address does not look right. Leave it blank if you do not want a reply.", "err");
      return;
    }

    go.disabled = true;
    say("Sending…");

    fetch("/api/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: document.getElementById("fb-category").value,
        name: document.getElementById("fb-name").value,
        email: email,
        message: body,
        website: document.getElementById("fb-website").value
      })
    }).then(function (r) {
      if (r.status === 429) {
        say("That is a lot of feedback in one hour. Try again later, or email us.", "err");
        go.disabled = false;
        return;
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      // Replace the form outright: leaving a filled-in form on screen next to
      // "thanks" invites a second identical submission.
      form.innerHTML = "";
      say("Thank you — that has been sent. If you left an email, you will hear back.", "ok");
      form.appendChild(msg);
    }).catch(function () {
      say("That did not send. Check your connection and try again, or email jamiddeka1@gmail.com.", "err");
      go.disabled = false;
    });
  });
})();
