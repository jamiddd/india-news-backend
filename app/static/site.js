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

    // Hero carousel: real top stories, swipeable, each with its own framing
    // rows cycling underneath (backend/docs/website-roadmap.md item 4).
    // Framing cards cycle one at a time; the outgoing card leaves upward
    // while the incoming one arrives from below, so the motion reads as a
    // single column advancing rather than a crossfade.
    var stage = document.getElementById("framer");
    if (stage) {
      var host = document.getElementById("hero-framer");
      var dots = document.getElementById("framer-dots");
      var imgLight = document.getElementById("framer-img-light");
      var imgDark = document.getElementById("framer-img-dark");
      var caption = document.getElementById("framer-caption");
      var countEl = document.getElementById("framer-count");
      var prevBtn = document.getElementById("framer-prev");
      var nextBtn = document.getElementById("framer-next");
      var cards = [], buttons = [], at = 0, timer = null;
      var STEP = 3400;
      var still = window.matchMedia("(prefers-reduced-motion: reduce)");

      // What's baked into the page at load, so the hero always shows
      // something even if the live endpoint never answers -- see the
      // roadmap doc's "A static fallback" note. `image` stays null here:
      // the fallback screenshot pair already in the DOM (framer-img-light/
      // dark) is left alone rather than replaced.
      var stories = [{
        image: null,
        caption: "",
        count: "14 outlets",
        framing: Array.prototype.slice.call(stage.querySelectorAll(".frame-card")).map(function (c) {
          return { outlet: c.querySelector("b").textContent, headline_angle: c.querySelector("i").textContent };
        })
      }];
      var storyAt = 0;

      function buildFraming(list) {
        stage.innerHTML = "";
        dots.innerHTML = "";
        cards = list.map(function (f, i) {
          var el = document.createElement("article");
          el.className = "frame-card" + (i === 0 ? " is-active" : "");
          var b = document.createElement("b"); b.textContent = f.outlet;
          var it = document.createElement("i"); it.textContent = f.headline_angle;
          el.appendChild(b); el.appendChild(it);
          stage.appendChild(el);
          return el;
        });
        buttons = cards.map(function (_, i) {
          var b = document.createElement("button");
          b.type = "button";
          b.setAttribute("aria-label", "Show framing " + (i + 1) + " of " + cards.length);
          b.setAttribute("aria-current", i === 0 ? "true" : "false");
          b.addEventListener("click", function () { show(i); restart(); });
          dots.appendChild(b);
          return b;
        });
        at = 0;
      }

      function show(next) {
        if (next === at || !cards[next]) return;
        cards[at].classList.remove("is-active");
        cards[at].classList.add("is-out");
        var prev = at;
        // Park the outgoing card back below the stage once it is out of
        // sight, so it slides up again on its next turn instead of dropping
        // in from the top.
        window.setTimeout(function () { cards[prev].classList.remove("is-out"); }, 600);
        at = next;
        cards[at].classList.add("is-active");
        buttons.forEach(function (b, i) { b.setAttribute("aria-current", i === at ? "true" : "false"); });
      }

      function tick() { show((at + 1) % cards.length); }
      function restart() {
        window.clearInterval(timer);
        if (!still.matches && cards.length > 1) timer = window.setInterval(tick, STEP);
      }

      function renderStory(i) {
        storyAt = ((i % stories.length) + stories.length) % stories.length;
        var s = stories[storyAt];
        if (s.image) {
          // A live story is a real news photo, not a themed UI capture, so
          // one <img> replaces the light/dark screenshot pair rather than
          // swapping each of their srcs.
          imgLight.style.display = "block";
          imgLight.src = s.image;
          imgLight.alt = s.caption;
          imgDark.style.display = "none";
        }
        caption.textContent = s.caption;
        countEl.textContent = s.count;
        buildFraming(s.framing);
        restart();
        host.classList.toggle("has-multi", stories.length > 1);
      }

      prevBtn.addEventListener("click", function () { renderStory(storyAt - 1); restart(); });
      nextBtn.addEventListener("click", function () { renderStory(storyAt + 1); restart(); });

      renderStory(0);

      // Stop while the reader is hovering, keyboard-focused inside, or has
      // the tab in the background -- an unattended interval keeps firing
      // transitions on a page nobody is looking at.
      host.addEventListener("mouseenter", function () { window.clearInterval(timer); });
      host.addEventListener("mouseleave", restart);
      host.addEventListener("focusin", function () { window.clearInterval(timer); });
      host.addEventListener("focusout", restart);
      document.addEventListener("visibilitychange", function () {
        if (document.hidden) { window.clearInterval(timer); } else { restart(); }
      });

      // Replace the fallback with real clusters once they load. Silently
      // keeps the fallback on any failure (bad response, network error,
      // empty list) -- see the roadmap doc: the hero must never end up
      // showing nothing.
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
            framing: it.framing || []
          };
        });
        renderStory(0);
      }).catch(function () {});
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
