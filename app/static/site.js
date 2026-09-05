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
