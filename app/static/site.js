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

    // Hero framing carousel. Cards cycle one at a time; the outgoing card
    // leaves upward while the incoming one arrives from below, so the motion
    // reads as a single column advancing rather than a crossfade.
    var stage = document.getElementById("framer");
    if (stage) {
      var cards = Array.prototype.slice.call(stage.querySelectorAll(".frame-card"));
      var dots = document.getElementById("framer-dots");
      var at = 0, timer = null;
      var STEP = 3400;
      var still = window.matchMedia("(prefers-reduced-motion: reduce)");

      cards.forEach(function (c, i) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("aria-label", "Show framing " + (i + 1) + " of " + cards.length);
        b.addEventListener("click", function () { show(i); restart(); });
        dots.appendChild(b);
      });
      var buttons = Array.prototype.slice.call(dots.children);

      function show(next) {
        if (next === at) return;
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
        timer = window.setInterval(tick, STEP);
      }

      buttons[0].setAttribute("aria-current", "true");
      restart();

      // Stop while the reader is hovering, keyboard-focused inside, or has
      // the tab in the background -- an unattended interval keeps firing
      // transitions on a page nobody is looking at.
      var host = stage.parentNode;
      host.addEventListener("mouseenter", function () { window.clearInterval(timer); });
      host.addEventListener("mouseleave", restart);
      host.addEventListener("focusin", function () { window.clearInterval(timer); });
      host.addEventListener("focusout", restart);
      document.addEventListener("visibilitychange", function () {
        if (document.hidden) { window.clearInterval(timer); } else { restart(); }
      });
      if (still.matches) { window.clearInterval(timer); }
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
