// Prototype shell: one place for the header, footer, theme toggle and fake data,
// so a layout change is made once and every page picks it up.
(function () {
  var root = document.documentElement, saved = null;
  try { saved = localStorage.getItem("oin-theme"); } catch (e) {}
  if (saved) root.setAttribute("data-theme", saved);

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
  function links(list) { return list.map(function (l) { return '<a href="' + l[1] + '">' + esc(l[0]) + "</a>"; }).join(""); }

  window.PROTO = { esc: esc, ready: fetch("/api/sample").then(function (r) { return r.json(); }) };

  PROTO.ready.then(function (d) {
    PROTO.data = d;
    var h = document.getElementById("app-header");
    if (h) h.outerHTML =
      '<header class="masthead"><div class="wrap">' +
      '<a class="brand" href="/"><span class="ph round mark" aria-hidden="true" style="width:32px;height:32px;padding:0"></span>' +
      '<span class="brand-text"><span class="wordmark">' + esc(d.brand.name) + '<span class="stop">.</span></span>' +
      '<span class="brand-sub">' + esc(d.brand.tagline) + "</span></span></a>" +
      '<nav class="links">' + links(d.nav) + "</nav>" +
      '<a class="btn btn-primary btn-sm get-app" href="/download"><span>Get the app</span></a>' +
      '<button class="theme-btn" id="theme" aria-label="Toggle dark mode">&#9680;</button>' +
      '<button class="user-btn" id="user" aria-label="Sign in" aria-haspopup="dialog"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/></svg></button></div></header>';
    var f = document.getElementById("app-footer");
    if (f) f.outerHTML = '<footer><div class="wrap"><span>&copy; ' + new Date().getFullYear() + " " + esc(d.brand.name) +
      "</span><nav>" + links(d.footer) + "</nav></div></footer>";
    var t = document.getElementById("theme");
    if (t) t.addEventListener("click", function () {
      var dark = matchMedia("(prefers-color-scheme: dark)").matches;
      var cur = root.getAttribute("data-theme") || (dark ? "dark" : "light");
      var next = cur === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("oin-theme", next); } catch (e) {}
    });
    document.dispatchEvent(new Event("proto:ready"));
  });
})();

// Announcement strip pinned above the navbar, after the Android AnnouncementBanner:
// one dark strip (same in both themes), one announcement at a time, highest priority
// first, the next revealed when the current one is dismissed, with a push transition
// (the new item rises in from below while the old one leaves through the top).
PROTO.ready.then(function (d) {
  var KEY = "oin-ann-dismissed", ACC = { offer: "#FFB300", info: "#4FA3FF", event: "#A98BFF" };
  var gone = [];
  try { gone = JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) {}
  var queue = (d.announcements || []).filter(function (a) { return gone.indexOf(a.id) < 0; })
    .sort(function (a, b) { return b.priority - a.priority; });
  if (!queue.length) return;
  var ICON = {
    offer: '<path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0L3 13V3h10l7.6 7.6a2 2 0 010 2.8z"/><circle cx="7.5" cy="7.5" r="1.2"/>',
    event: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>'
  };
  var bar = document.createElement("section");
  bar.className = "ann"; bar.setAttribute("aria-label", "Announcements");
  bar.innerHTML = '<div class="ann-in"><div class="ann-stage"></div></div>';
  document.body.insertBefore(bar, document.body.firstChild);
  var stage = bar.querySelector(".ann-stage");

  function row(a) {
    var c = ACC[a.kind] || ACC.info, icon = ICON[a.kind] || ICON.info, el = document.createElement("div");
    el.className = "ann-row" + (a.action_type ? " clk" : "");
    el.innerHTML = '<span class="ann-bar" style="background:' + c + '"></span>' +
      '<svg class="ann-ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="' + c + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + icon + "</svg>" +
      '<div class="ann-txt"><b>' + PROTO.esc(a.title) + "</b>" + (a.body ? "<span>" + PROTO.esc(a.body) + "</span>" : "") + "</div>" +
      (a.cta_label && a.action_type ? '<a class="ann-cta" href="' + PROTO.esc(a.action_value || "#") + '" style="color:' + c + ';background:' + c + '2E">' + PROTO.esc(a.cta_label) + "</a>" : "") +
      '<button type="button" class="ann-x" aria-label="Dismiss">&times;</button>';
    el.addEventListener("click", function (e) {
      if (e.target.closest(".ann-x")) return;
      var cta = el.querySelector(".ann-cta");
      if (cta && !e.target.closest(".ann-cta")) cta.click();
    });
    el.querySelector(".ann-x").addEventListener("click", function () { dismiss(a, el); });
    return el;
  }
  var still = matchMedia("(prefers-reduced-motion: reduce)");
  function show(a) { var r = row(a); stage.appendChild(r); return r; }
  function dismiss(a, el) {
    gone.push(a.id);
    try { localStorage.setItem(KEY, JSON.stringify(gone)); } catch (e) {}
    queue.shift();
    var next = queue[0];
    if (still.matches) { el.remove(); if (next) show(next); else bar.remove(); return; }
    el.classList.add("out");
    if (next) show(next).classList.add("in");
    setTimeout(function () { el.remove(); if (!next) bar.remove(); }, 320);
  }
  show(queue[0]);
});

// Sources popover: clicking a story's source line opens a small panel next to it
// listing that story's coverage, modelled on the Android coverage sheet (time, a dot
// on a rail, the outlet's logo and name, and its headline; the earliest report is
// tagged FIRST). Closes on Esc or a click elsewhere; returns focus.
PROTO.ready.then(function (d) {
  var E = PROTO.esc, pop = null, trigger = null;
  function ago(m) { return m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : Math.round(m / 1440) + "d"; }
  function close() {
    if (!pop) return;
    pop.remove(); pop = null;
    if (trigger) { trigger.setAttribute("aria-expanded", "false"); trigger.focus(); trigger = null; }
  }
  function open(a) {
    var s = d.stories.filter(function (x) { return String(x.id) === a.dataset.sid; })[0];
    if (!s) return;
    close();
    trigger = a; a.setAttribute("aria-expanded", "true");
    var list = s.coverage.slice().sort(function (x, y) { return y.minutes_ago - x.minutes_ago; });
    pop = document.createElement("div");
    pop.className = "pop"; pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "Coverage of this story");
    pop.innerHTML = '<div class="pop-h"><b>Coverage</b><span>' + list.length + (list.length === 1 ? " article" : " articles") + "</span>" +
      '<button type="button" class="pop-x" aria-label="Close">&times;</button></div><div class="pop-l">' +
      list.map(function (v, i) {
        return '<div class="pop-r' + (i === list.length - 1 ? " last" : "") + '"><span class="pop-t">' + ago(v.minutes_ago) + '</span><span class="pop-dot' + (i === 0 ? " first" : "") + '"></span>' +
          '<div><div class="pop-n"><i class="fv"></i><a class="so" href="/source/' + PROTO.slug(v.outlet) + '"><b>' + E(v.outlet) + "</b></a>" + (i === 0 ? "<em>FIRST</em>" : "") + "</div><div class=\"pop-s\">" + E(v.title) + "</div></div></div>";
      }).join("") + "</div>";
    document.body.appendChild(pop);
    // Place it under the source line, left-aligned with it; flip above if it would
    // run off the bottom of the window and there is more room up there.
    var r = a.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    var left = Math.max(12, Math.min(r.left, innerWidth - w - 12));
    var top = r.bottom + 8;
    if (top + h > innerHeight - 12 && r.top - h - 8 > 12) top = r.top - h - 8;
    pop.style.left = left + scrollX + "px";
    pop.style.top = top + scrollY + "px";
    pop.querySelector(".pop-x").addEventListener("click", close);
    pop.querySelector(".pop-x").focus();
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a.src[data-sid]");
    if (a) { e.preventDefault(); if (pop && trigger === a) close(); else open(a); return; }
    if (pop && !pop.contains(e.target)) close();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && pop) close(); });
});

// Sign-in, after the Android LoginScreen, as a modal over whatever page you are on.
// Pages: choose (Google / email) -> email (sign in or create account) -> verify
// ("Check your inbox"). Accounts live in localStorage and nothing is sent anywhere.
// Demo shortcuts: any new email works; "taken@example.com" is already registered.
PROTO.ready.then(function () {
  var E = PROTO.esc, AK = "oin-accounts", SK = "oin-session";
  function load(k, def) { try { return JSON.parse(localStorage.getItem(k)) || def; } catch (e) { return def; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  var ub = document.getElementById("user");
  if (!ub) return;

  var G = '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>';
  var MAIL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>';
  var st = { page: "choose", signUp: false, name: "", email: "", pw: "", showPw: false, err: null, busy: "", resend: 0 };
  var modal = null, opener = null, timer = null;

  function session() { return load(SK, null); }
  function paintIcon() {
    var u = session();
    if (u) {
      ub.className = "user-btn on"; ub.setAttribute("aria-label", "Account: " + u.name);
      ub.innerHTML = '<span aria-hidden="true">' + E((u.name || u.email || "?").trim().charAt(0).toUpperCase()) + "</span>";
    }
  }
  paintIcon();

  function toast(msg) {
    var t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 300); }, 3200);
  }
  function fail(msg, field) { st.err = { msg: msg, field: field || null }; st.busy = ""; render(); }
  function done(user) {
    save(SK, user); closeModal(true); paintIcon(); toast("Welcome!");
  }

  function field(id, label, type, val, ph, extra) {
    var bad = st.err && st.err.field === id;
    return '<div class="af"><label for="a-' + id + '">' + label + "</label><div class=\"af-box" + (bad ? " bad" : "") + '"><input id="a-' + id + '" type="' + type + '" value="' + E(val) + '" placeholder="' + E(ph || "") + '" autocomplete="' + (id === "pw" ? (st.signUp ? "new-password" : "current-password") : id === "email" ? "email" : "name") + '"' + (bad ? ' aria-invalid="true"' : "") + ">" + (extra || "") + "</div>" +
      (bad ? '<div class="af-err" role="alert">' + E(st.err.msg) + "</div>" : "") + "</div>";
  }
  function line() { return st.err && !st.err.field ? '<div class="af-err gen" role="alert">' + E(st.err.msg) + "</div>" : ""; }

  function body() {
    if (st.page === "choose") {
      return '<h2 id="auth-t">Your news, on every device you use</h2><p class="auth-sub">An account keeps what you\'ve set up here, and unlocks follows.</p>' +
        '<ul class="auth-perks"><li><b>Topics and publishers sync</b><span>Your tabs look the same on a new phone or browser.</span></li>' +
        '<li><b>Follow stories and topics</b><span>Get a notification when a story you follow moves.</span></li></ul>' + line() +
        '<div class="auth-btns"><button type="button" class="ab filled" data-a="google"' + (st.busy ? " disabled" : "") + ">" + (st.busy === "google" ? '<i class="spin"></i>' : G) + "<span>Continue with Google</span></button>" +
        '<button type="button" class="ab" data-a="email"' + (st.busy ? " disabled" : "") + ">" + MAIL + "<span>Continue with email</span></button></div>" +
        '<p class="auth-legal">By continuing you agree to our <a href="/terms">Terms of Service</a> and <a href="/privacy">Privacy Policy</a>.</p>';
    }
    if (st.page === "email") {
      var eye = '<button type="button" class="af-eye" data-a="eye" aria-label="' + (st.showPw ? "Hide password" : "Show password") + '">' + (st.showPw ? "Hide" : "Show") + "</button>";
      return '<button type="button" class="auth-back" data-a="back" aria-label="Back">&larr;</button>' +
        '<h2 id="auth-t">' + (st.signUp ? "Create your account" : "Sign in with email") + "</h2>" +
        '<div class="auth-tabs" role="tablist"><button type="button" role="tab" aria-selected="' + !st.signUp + '" class="' + (!st.signUp ? "on" : "") + '" data-a="mode-in">Sign in</button><button type="button" role="tab" aria-selected="' + st.signUp + '" class="' + (st.signUp ? "on" : "") + '" data-a="mode-up">Create account</button></div>' +
        '<form class="auth-form" novalidate>' +
        (st.signUp ? field("name", "Name", "text", st.name, "What should we call you?") : "") +
        field("email", "Email", "email", st.email, "you@example.com") +
        field("pw", "Password", st.showPw ? "text" : "password", st.pw, st.signUp ? "At least 6 characters" : "", eye) +
        (st.signUp ? '<p class="auth-note">We\'ll send a link to confirm it\'s your email.</p>' : '<button type="button" class="auth-link" data-a="forgot">Forgot password?</button>') +
        line() + '<button type="submit" class="ab filled"' + (st.busy ? " disabled" : "") + ">" + (st.busy === "email" ? '<i class="spin"></i>' : "") + "<span>" + (st.signUp ? "Create account" : "Sign in") + "</span></button></form>";
    }
    return '<button type="button" class="auth-back" data-a="back" aria-label="Back">&larr;</button>' +
      '<div class="auth-mail">' + MAIL.replace('width="18" height="18"', 'width="30" height="30"') + '</div><h2 id="auth-t">Check your inbox</h2>' +
      '<p class="auth-sub">We sent a confirmation link to <b>' + E(st.email) + "</b>.</p>" +
      '<ol class="auth-steps"><li>Open the email from Open Indian News.</li><li>Click the link. It opens in your browser.</li><li>Come back here. We\'ll carry on from there.</li></ol>' +
      '<p class="auth-note">Not there after a minute? Check Spam or Promotions.</p>' + line() +
      '<div class="auth-btns"><a class="ab" href="mailto:' + E(st.email) + '"><span>Open email app</span></a>' +
      '<button type="button" class="ab filled" data-a="confirmed"' + (st.busy ? " disabled" : "") + ">" + (st.busy === "confirmed" ? '<i class="spin"></i>' : "") + "<span>I've confirmed, continue</span></button></div>" +
      '<div class="auth-foot"><button type="button" class="auth-link" data-a="other">Use a different email</button>' +
      '<button type="button" class="auth-link" data-a="resend"' + (st.resend ? " disabled" : "") + ">" + (st.resend ? "Resend in 0:" + ("0" + st.resend).slice(-2) : "Resend email") + "</button></div>";
  }

  function render() {
    if (!modal) return;
    var focused = document.activeElement && document.activeElement.id;
    modal.querySelector(".auth-card").innerHTML = '<button type="button" class="auth-x" data-a="close" aria-label="Close">&times;</button>' + body();
    var f = (focused && document.getElementById(focused)) || (st.err && st.err.field && document.getElementById("a-" + st.err.field)) ||
      modal.querySelector("input") || modal.querySelector(".ab");
    if (f) f.focus();
  }
  function readFields() {
    var n = document.getElementById("a-name"), e = document.getElementById("a-email"), p = document.getElementById("a-pw");
    if (n) st.name = n.value; if (e) st.email = e.value; if (p) st.pw = p.value;
  }

  function openModal() {
    if (modal) return;
    opener = document.activeElement;
    st = { page: "choose", signUp: false, name: "", email: "", pw: "", showPw: false, err: null, busy: "", resend: 0 };
    modal = document.createElement("div");
    modal.className = "auth";
    modal.innerHTML = '<div class="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-t"></div>';
    document.body.appendChild(modal); document.body.style.overflow = "hidden";
    modal.addEventListener("mousedown", function (e) { if (e.target === modal) closeModal(); });
    modal.addEventListener("click", onClick);
    modal.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
    render();
  }
  function closeModal(ok) {
    if (!modal) return;
    clearInterval(timer); modal.remove(); modal = null; document.body.style.overflow = "";
    if (!ok && opener && opener.focus) opener.focus();
  }

  function validEmail(v) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v); }
  function submit() {
    readFields();
    var email = st.email.trim().toLowerCase(), accounts = load(AK, []), acc = accounts.filter(function (a) { return a.email === email; })[0];
    st.err = null;
    if (!email) return fail("Enter your email.", "email");
    if (!st.pw) return fail("Enter your password.", "pw");
    if (!validEmail(email)) return fail("That doesn't look like an email address.", "email");
    if (st.signUp) {
      if (st.pw.length < 6) return fail("Use at least 6 characters.", "pw");
      if (acc || email === "taken@example.com") return fail("That email already has an account. Sign in instead.", "email");
    } else {
      if (!acc) return fail("No account uses that email. Create one instead.", "email");
      if (acc.pw !== st.pw) return fail("That email and password don't match.", "pw");
    }
    st.busy = "email"; render();
    setTimeout(function () {
      if (!modal) return;
      if (st.signUp) {
        accounts.push({ name: st.name.trim() || email.split("@")[0], email: email, pw: st.pw, verified: false });
        save(AK, accounts); st.email = email; goVerify();
      } else if (!acc.verified) { st.email = email; goVerify(); }
      else done({ name: acc.name, email: acc.email });
    }, 700);
  }
  function goVerify() {
    st.page = "verify"; st.busy = ""; st.err = null; st.resend = 30; clearInterval(timer);
    timer = setInterval(function () { if (st.resend > 0) { st.resend--; var b = modal && modal.querySelector('[data-a="resend"]'); if (b) { b.textContent = st.resend ? "Resend in 0:" + ("0" + st.resend).slice(-2) : "Resend email"; b.disabled = st.resend > 0; } } }, 1000);
    render();
  }

  function onClick(e) {
    var b = e.target.closest("[data-a]");
    if (!b) return;
    var a = b.dataset.a;
    readFields();
    if (a === "close") closeModal();
    else if (a === "google") {
      st.busy = "google"; st.err = null; render();
      setTimeout(function () { if (modal) done({ name: "Sample Reader", email: "reader@example.com" }); }, 900);
    } else if (a === "email") { st.page = "email"; st.err = null; render(); }
    else if (a === "back") { st.page = st.page === "verify" ? "email" : "choose"; st.err = null; clearInterval(timer); render(); }
    else if (a === "mode-in" || a === "mode-up") { st.signUp = a === "mode-up"; st.err = null; render(); }
    else if (a === "eye") { st.showPw = !st.showPw; render(); }
    else if (a === "forgot") {
      var em = st.email.trim().toLowerCase();
      if (!em) return fail("Enter your email, then tap Forgot password.", "email");
      if (!validEmail(em)) return fail("That doesn't look like an email address.", "email");
      if (!load(AK, []).some(function (x) { return x.email === em; })) return fail("No account uses that email. Create one instead.", "email");
      st.err = null; render(); toast("Password reset link sent to " + em);
    } else if (a === "confirmed") {
      st.busy = "confirmed"; render();
      setTimeout(function () {
        var accounts = load(AK, []), acc = accounts.filter(function (x) { return x.email === st.email; })[0];
        if (!acc) return fail("Something went wrong. Try again.");
        acc.verified = true; save(AK, accounts); done({ name: acc.name, email: acc.email });
      }, 700);
    } else if (a === "other") {
      var accounts = load(AK, []).filter(function (x) { return !(x.email === st.email && !x.verified); });
      save(AK, accounts); st.page = "email"; st.signUp = true; st.email = ""; st.pw = ""; st.err = null; clearInterval(timer); render();
    } else if (a === "resend" && !st.resend) { st.resend = 30; goVerify(); toast("Confirmation email sent again"); }
  }

  // Account menu (signed in): who you are, and sign out.
  var menu = null;
  function closeMenu() { if (menu) { menu.remove(); menu = null; ub.setAttribute("aria-expanded", "false"); } }
  function openMenu() {
    var u = session();
    menu = document.createElement("div");
    menu.className = "acct"; menu.setAttribute("role", "menu");
    menu.innerHTML = '<div class="acct-who"><b>' + E(u.name) + "</b><span>" + E(u.email) + '</span></div><a role="menuitem" class="acct-out" href="/profile" style="display:block;padding:12px 16px;text-decoration:none;color:inherit">Profile</a><button type="button" role="menuitem" class="acct-out">Sign out</button>';
    document.body.appendChild(menu);
    var anchor = PROTO._anchor && PROTO._anchor.offsetParent !== null ? PROTO._anchor : ub; PROTO._anchor = null;
    var r = anchor.getBoundingClientRect();
    menu.style.top = (anchor === ub ? r.bottom + scrollY + 8 : r.top + scrollY - menu.offsetHeight - 12) + "px";
    menu.style.left = Math.max(12, Math.min(r.right + scrollX - menu.offsetWidth, innerWidth - menu.offsetWidth - 12)) + "px";
    ub.setAttribute("aria-expanded", "true");
    menu.querySelector("button.acct-out").addEventListener("click", function () {
      try { localStorage.removeItem(SK); } catch (e) {}
      closeMenu(); ub.className = "user-btn"; ub.setAttribute("aria-label", "Sign in");
      ub.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/></svg>';
      toast("Signed out");
    });
    menu.querySelector("button.acct-out").focus();
  }
  PROTO.signedIn = function () { return !!session(); };
  PROTO.openLogin = openModal;
  ub.addEventListener("click", function (e) { e.stopPropagation(); if (session()) { menu ? closeMenu() : openMenu(); } else openModal(); });
  document.addEventListener("click", function (e) { if (menu && !menu.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (modal) closeModal(); else if (menu) { closeMenu(); ub.focus(); }
  });
});

// Save and share for posts that carry a hero image. Saved ids persist in localStorage;
// share uses the native share sheet when the browser has one and copies the link
// otherwise. (Defined synchronously so page scripts can ask PROTO.isSaved while they
// render.)
(function () {
  var SV = "oin-saved";
  function ids() { try { return JSON.parse(localStorage.getItem(SV)) || []; } catch (e) { return []; } }
  PROTO.isSaved = function (id) { return ids().indexOf(+id) > -1; };
  var BM = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4.5L6 21z"/></svg>';
  var SH = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4"/></svg>';
  PROTO.acts = function (id) {
    var on = PROTO.isSaved(id);
    return '<div class="acts"><button type="button" class="act save' + (on ? " on" : "") + '" data-save="' + id + '" aria-pressed="' + on + '" aria-label="' + (on ? "Remove from saved" : "Save story") + '" title="' + (on ? "Saved" : "Save") + '">' + BM + "</button>" +
      '<button type="button" class="act" data-share="' + id + '" aria-label="Share story" title="Share">' + SH + "</button></div>";
  };
  function note(msg) {
    var t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 300); }, 2600);
  }
  document.addEventListener("click", function (e) {
    var sv = e.target.closest("[data-save]"), sh = e.target.closest("[data-share]");
    if (sv) {
      e.preventDefault();
      var id = +sv.dataset.save, list = ids(), i = list.indexOf(id), on = i < 0;
      if (on) list.push(id); else list.splice(i, 1);
      try { localStorage.setItem(SV, JSON.stringify(list)); } catch (x) {}
      // every copy of this story on the page flips together
      [].forEach.call(document.querySelectorAll('[data-save="' + id + '"]'), function (b) {
        b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
        b.setAttribute("aria-label", on ? "Remove from saved" : "Save story"); b.title = on ? "Saved" : "Save";
      });
      note(on ? "Saved" : "Removed from saved");
    } else if (sh) {
      e.preventDefault();
      var url = location.origin + "/story/" + sh.dataset.share;
      var s = (PROTO.data && PROTO.data.stories.filter(function (x) { return String(x.id) === sh.dataset.share; })[0]) || null;
      if (navigator.share) { navigator.share({ title: s ? s.headline : "Open Indian News", url: url }).catch(function () {}); return; }
      var copied = function () { note("Link copied"); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(copied, function () { note(url); });
      else note(url);
    }
  });
})();

// Media carousel for a story (manual, scroll-snap): builder plus behaviour, shared by every page.
(function () {
  // The cover story can carry several images and videos: a manual carousel (no
  // auto-advance) with a button on each side and dot indicators over the media.
  var PLAY = '<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  PROTO.carousel = function (s) {
    var m = s.media || [];
    if (m.length < 2) return '<div class="ph mv" role="button" tabindex="0" aria-label="Open media fullscreen" style="aspect-ratio:16/9">' + (m[0] && m[0].kind === "video" ? '<span class="car-play">' + PLAY + "</span>" : "") + ((m[0] && m[0].label) || "image") + "</div>";
    return '<div class="car" role="region" aria-roledescription="carousel" aria-label="Media for this story" tabindex="0">' +
      '<div class="car-track">' + m.map(function (x, i) {
        return '<div class="car-slide ph" role="group" aria-roledescription="slide" aria-label="' + (i + 1) + " of " + m.length + '" style="aspect-ratio:16/9">' + (x.kind === "video" ? '<span class="car-play">' + PLAY + "</span>" : "") + x.label + " " + (i + 1) + "</div>";
      }).join("") + "</div>" +
      '<button type="button" class="car-btn prev" aria-label="Previous media" disabled><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button>' +
      '<button type="button" class="car-btn next" aria-label="Next media"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button>' +
      '<div class="car-dots">' + m.map(function (x, i) { return '<button type="button" class="car-dot' + (i === 0 ? " on" : "") + '" aria-label="Go to media ' + (i + 1) + '"' + (i === 0 ? ' aria-current="true"' : "") + "></button>"; }).join("") + "</div></div>";
  };
  // Carousel behaviour: native scroll-snap does the sliding (so touch swipe works);
  // the buttons, dots and arrow keys just scroll to a slide, and the dots/buttons
  // follow wherever the track ends up.
  var calm = matchMedia("(prefers-reduced-motion: reduce)");
  function carIndex(track) { return Math.round(track.scrollLeft / track.clientWidth); }
  function carSync(car, at) {
    var t = car.querySelector(".car-track"), i = at == null ? carIndex(t) : at, n = t.children.length;
    car.querySelector(".prev").disabled = i <= 0;
    car.querySelector(".next").disabled = i >= n - 1;
    [].forEach.call(car.querySelectorAll(".car-dot"), function (d, j) { d.classList.toggle("on", j === i); if (j === i) d.setAttribute("aria-current", "true"); else d.removeAttribute("aria-current"); });
  }
  function carGo(car, i) {
    var t = car.querySelector(".car-track"), n = t.children.length;
    i = Math.max(0, Math.min(n - 1, i));
    t.scrollTo({ left: i * t.clientWidth, behavior: calm.matches ? "auto" : "smooth" });
    carSync(car, i);
  }
  document.addEventListener("click", function (e) {
    var car = e.target.closest(".car");
    if (!car) return;
    var i = carIndex(car.querySelector(".car-track"));
    if (e.target.closest(".prev")) carGo(car, i - 1);
    else if (e.target.closest(".next")) carGo(car, i + 1);
    else { var dot = e.target.closest(".car-dot"); if (dot) carGo(car, [].indexOf.call(car.querySelectorAll(".car-dot"), dot)); }
  });
  document.addEventListener("keydown", function (e) {
    var car = e.target.closest && e.target.closest(".car");
    if (!car || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    carGo(car, carIndex(car.querySelector(".car-track")) + (e.key === "ArrowRight" ? 1 : -1));
  });
  document.addEventListener("scroll", function (e) {
    if (e.target.classList && e.target.classList.contains("car-track")) carSync(e.target.parentNode);
  }, true);
  // The controls are mostly see-through until the pointer moves over the media, and
  // fade back after it has been still for a moment (or has left).
  var IDLE_MS = 1500;
  function carWake(e) {
    var car = e.target.closest && e.target.closest(".car");
    if (!car) return;
    car.classList.add("moving");
    clearTimeout(car._idle);
    car._idle = setTimeout(function () { car.classList.remove("moving"); }, IDLE_MS);
  }
  document.addEventListener("pointermove", carWake);
  document.addEventListener("pointerdown", carWake);
  document.addEventListener("pointerout", function (e) {
    var car = e.target.closest && e.target.closest(".car");
    if (car && !car.contains(e.relatedTarget)) { clearTimeout(car._idle); car.classList.remove("moving"); }
  });
})();

// Shared story-row helpers, so every page builds source rows and tooltips the same way.
PROTO.ago = function (m) { return m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : Math.round(m / 1440) + "d"; };
PROTO.src = function (s) {
  var E = PROTO.esc, first = (s.coverage[0] || {}).outlet || "News Desk", n = Math.max(0, s.outlet_count - 1);
  return '<a class="src" data-sid="' + s.id + '" href="/story/' + s.id + '#coverage"><span class="favs"><i class="fv"></i>' + (s.outlet_count > 1 ? '<i class="fv"></i>' : "") + "</span><span>" +
    E(first) + (n ? " &amp; " + n + " other" + (n > 1 ? "s" : "") : "") + "</span></a>";
};
PROTO.srow = function (s) { return '<div class="srcrow">' + PROTO.src(s) + PROTO.acts(s.id) + "</div>"; };
PROTO.tip = function (id) { return '<div class="tip" role="group" aria-label="Story actions">' + PROTO.acts(id) + "</div>"; };

// The yellow "no ads" strip can be dismissed; the choice is remembered in this browser.
(function () {
  var KEY = "oin-noads-dismissed", bar = document.querySelector(".signal-bar");
  if (!bar) return;
  try { if (localStorage.getItem(KEY)) { bar.remove(); return; } } catch (e) {}
  var b = document.createElement("button");
  b.type = "button"; b.className = "signal-x"; b.setAttribute("aria-label", "Dismiss");
  b.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  b.addEventListener("click", function () {
    try { localStorage.setItem(KEY, "1"); } catch (e) {}
    bar.remove();
  });
  bar.querySelector(".wrap").appendChild(b);
})();

// Fullscreen media viewer: opens from a picture or video in a story (a carousel slide or
// a single media box). Dark full-window view, previous/next, a counter, close, and
// keyboard control (arrows, Esc). Placeholders stand in for the real media.
(function () {
  var view = null, items = [], at = 0, opener = null;
  function collect(from) {
    var car = from.closest(".car");
    if (car) {
      items = [].map.call(car.querySelectorAll(".car-slide"), function (sl) { return { video: !!sl.querySelector(".car-play"), label: sl.textContent.trim() }; });
      at = [].indexOf.call(car.querySelectorAll(".car-slide"), from.closest(".car-slide"));
    } else {
      items = [{ video: !!from.querySelector(".car-play"), label: from.textContent.trim() }]; at = 0;
    }
  }
  function paint() {
    var it = items[at], n = items.length;
    view.querySelector(".mvw-stage").innerHTML = '<div class="mvw-media ph">' + (it.video ? '<span class="car-play big">' + '<svg width="40" height="40" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg></span>' : "") + it.label + "</div>";
    view.querySelector(".mvw-count").textContent = (at + 1) + " / " + n;
    view.querySelector(".mvw-cap").textContent = it.video ? "Video" : "Image";
    view.querySelector(".mvw-prev").hidden = n < 2 || at === 0;
    view.querySelector(".mvw-next").hidden = n < 2 || at === n - 1;
    view.querySelector(".mvw-count").hidden = n < 2;
  }
  function open(from) {
    if (view) return;
    opener = document.activeElement;
    collect(from);
    view = document.createElement("div");
    view.className = "mvw"; view.setAttribute("role", "dialog"); view.setAttribute("aria-modal", "true"); view.setAttribute("aria-label", "Media viewer");
    view.innerHTML = '<div class="mvw-top"><span class="mvw-count"></span><span class="mvw-cap"></span><button type="button" class="mvw-x" aria-label="Close">&times;</button></div>' +
      '<button type="button" class="mvw-nav mvw-prev" aria-label="Previous"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button>' +
      '<div class="mvw-stage"></div>' +
      '<button type="button" class="mvw-nav mvw-next" aria-label="Next"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button>';
    document.body.appendChild(view); document.body.style.overflow = "hidden";
    view.addEventListener("click", function (e) {
      if (e.target.closest(".mvw-x") || e.target.classList.contains("mvw") || e.target.classList.contains("mvw-stage")) close();
      else if (e.target.closest(".mvw-prev")) go(-1);
      else if (e.target.closest(".mvw-next")) go(1);
    });
    paint();
    view.querySelector(".mvw-x").focus();
  }
  function go(d) { var n = Math.max(0, Math.min(items.length - 1, at + d)); if (n !== at) { at = n; paint(); } }
  function close() { if (!view) return; view.remove(); view = null; document.body.style.overflow = ""; if (opener && opener.focus) opener.focus(); }
  document.addEventListener("click", function (e) {
    if (view || e.target.closest(".car-btn, .car-dot")) return;
    var t = e.target.closest(".car-slide, .mv");
    if (t) open(t);
  });
  document.addEventListener("keydown", function (e) {
    if (view) {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("mv")) { e.preventDefault(); open(e.target); }
  }, true);
})();

// URL-safe name used for /source/<name> and /topic/<name>.
PROTO.slug = function (n) { return String(n).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""); };

// Narration player for timelines and explainers, with a mplay player that keeps going in the
// background. One player exists for the whole site: starting a narration anywhere makes it
// the current one, and a bar at the bottom of every page shows it (play/pause, a progress bar
// you can click to seek, time, speed, close, and a link back to the item). Its state lives in
// localStorage, and the position is worked out from a timestamp, so it carries on across
// page loads and even while the tab is hidden. There is no audio file yet, so the "audio" is
// a clock; wiring a real <audio> element in only means driving this same state from it.
(function () {
  var KEY = "oin-player", SPEEDS = [1, 1.25, 1.5, 2], P = null, bar = null, timer = null;
  function fmt(n) { n = Math.max(0, Math.floor(n)); return Math.floor(n / 60) + ":" + ("0" + (n % 60)).slice(-2); }
  function load() { try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; } }
  function save() { try { if (P) localStorage.setItem(KEY, JSON.stringify(P)); else localStorage.removeItem(KEY); } catch (e) {} }
  function pos() { return !P ? 0 : P.playing ? Math.min(P.dur, P.pos + (Date.now() - P.at) / 1000 * P.speed) : P.pos; }
  function fold() { if (P) { P.pos = pos(); P.at = Date.now(); } }

  var PLAY = "&#9654;", PAUSE = "&#10074;&#10074;";
  PROTO.player = function (a) {
    var d = ' data-id="' + a.id + '" data-kind="' + a.kind + '" data-title="' + PROTO.esc(a.title) + '" data-url="' + PROTO.esc(a.url) + '" data-dur="' + a.duration + '"';
    return '<div class="ap"' + d + '><button type="button" class="ap-btn" aria-label="Play">' + PLAY + '</button>' +
      '<div class="ap-mid"><div class="ap-label">' + PROTO.esc(a.label) + '</div><div class="ap-bar" role="progressbar" aria-label="Narration progress" aria-valuemin="0" aria-valuemax="' + a.duration + '" aria-valuenow="0"><i></i></div></div>' +
      '<span class="ap-time">0:00 / ' + fmt(a.duration) + "</span></div>";
  };

  // draw the current state into the mplay bar and into the matching inline player(s)
  function paint() {
    var t = pos();
    if (P && bar) {
      bar.querySelector(".mplay-pp").innerHTML = P.playing ? PAUSE : PLAY;
      bar.querySelector(".mplay-pp").setAttribute("aria-label", P.playing ? "Pause" : "Play");
      bar.querySelector(".mplay-bar i").style.width = (t / P.dur * 100) + "%";
      bar.querySelector(".mplay-bar").setAttribute("aria-valuenow", Math.floor(t));
      bar.querySelector(".mplay-time").textContent = fmt(t) + " / " + fmt(P.dur);
      bar.querySelector(".mplay-sp").textContent = P.speed + "×";
    }
    [].forEach.call(document.querySelectorAll(".ap"), function (ap) {
      var mine = P && ap.dataset.id === String(P.id) && ap.dataset.kind === P.kind, dur = +ap.dataset.dur, x = mine ? t : 0;
      var btn = ap.querySelector(".ap-btn");
      btn.innerHTML = mine && P.playing ? PAUSE : PLAY;
      btn.setAttribute("aria-label", mine && P.playing ? "Pause" : "Play");
      ap.querySelector(".ap-bar i").style.width = (x / dur * 100) + "%";
      ap.querySelector(".ap-bar").setAttribute("aria-valuenow", Math.floor(x));
      ap.querySelector(".ap-time").textContent = fmt(x) + " / " + fmt(dur);
    });
  }
  function tick() {
    if (!P) return;
    if (P.playing && pos() >= P.dur) { P.playing = false; P.pos = 0; P.at = Date.now(); save(); }
    paint();
  }
  function run() { clearInterval(timer); timer = setInterval(tick, 250); }
  function setRoot() {
    var on = !!bar;
    document.documentElement.classList.toggle("has-mplay", on);
    document.documentElement.style.setProperty("--mplay-h", on ? (matchMedia("(max-width:700px)").matches ? "52px" : "80px") : "0px");
  }
  var phone = matchMedia("(max-width:700px)");
  function place() {
    if (!bar) return;
    var tb = document.querySelector(".tabbar");
    if (phone.matches && tb) { if (bar.parentNode !== tb) tb.insertBefore(bar, tb.firstChild); }
    else if (bar.parentNode !== document.body) document.body.appendChild(bar);
    setRoot();
  }
  document.addEventListener("oin:tabbar", place);
  phone.addEventListener("change", place);
  function showBar() {
    if (bar || !P) return;
    bar = document.createElement("section");
    bar.className = "mplay"; bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Now playing");
    bar.innerHTML = '<div class="mplay-in"><button type="button" class="mplay-pp" aria-label="Play">' + PLAY + '</button>' +
      '<a class="mplay-t" href="' + PROTO.esc(P.url) + '"><small>' + PROTO.esc(P.kind) + ' &middot; narration</small><b>' + PROTO.esc(P.title) + "</b></a>" +
      '<div class="mplay-bar" role="progressbar" aria-label="Narration progress" aria-valuemin="0" aria-valuemax="' + P.dur + '" aria-valuenow="0"><i></i></div>' +
      '<span class="mplay-time">0:00 / ' + fmt(P.dur) + '</span><button type="button" class="mplay-sp" aria-label="Playback speed">1×</button>' +
      '<button type="button" class="mplay-x" aria-label="Close player">&times;</button></div>';
    document.body.appendChild(bar); place();
  }
  function closePlayer() {
    P = null; save(); clearInterval(timer);
    if (bar) { bar.remove(); bar = null; }
    setRoot(); paint();
  }
  function start(item) {
    P = { id: item.id, kind: item.kind, title: item.title, url: item.url, dur: item.dur, pos: 0, playing: true, at: Date.now(), speed: P ? P.speed : 1 };
    if (bar) { bar.remove(); bar = null; }
    save(); showBar(); run(); paint();
  }
  function toggle() {
    if (!P) return;
    fold(); P.playing = !P.playing; P.at = Date.now(); save(); paint();
  }
  function seek(frac) {
    if (!P) return;
    P.pos = Math.max(0, Math.min(1, frac)) * P.dur; P.at = Date.now(); save(); paint();
  }
  function fromBar(el, e) { var r = el.getBoundingClientRect(); return (e.clientX - r.left) / r.width; }

  document.addEventListener("click", function (e) {
    var ap = e.target.closest(".ap");
    if (ap) {
      if (e.target.closest(".ap-btn")) {
        var same = P && ap.dataset.id === String(P.id) && ap.dataset.kind === P.kind;
        if (same) toggle();
        else start({ id: ap.dataset.id, kind: ap.dataset.kind, title: ap.dataset.title, url: ap.dataset.url, dur: +ap.dataset.dur });
      } else if (e.target.closest(".ap-bar") && P && ap.dataset.id === String(P.id) && ap.dataset.kind === P.kind) {
        seek(fromBar(ap.querySelector(".ap-bar"), e));
      }
      return;
    }
    if (!bar || !bar.contains(e.target)) return;
    if (e.target.closest(".mplay-pp")) toggle();
    else if (e.target.closest(".mplay-x")) closePlayer();
    else if (e.target.closest(".mplay-sp")) { fold(); P.speed = SPEEDS[(SPEEDS.indexOf(P.speed) + 1) % SPEEDS.length]; save(); paint(); }
    else if (e.target.closest(".mplay-bar")) seek(fromBar(bar.querySelector(".mplay-bar"), e));
  });
  // another tab changed the player: follow it
  addEventListener("storage", function (e) {
    if (e.key !== KEY) return;
    P = load();
    if (!P) { if (bar) { bar.remove(); bar = null; } setRoot(); } else { showBar(); run(); }
    paint();
  });

  // carry on from wherever the last page left off
  P = load();
  if (P) {
    if (P.playing && pos() >= P.dur) { P.playing = false; P.pos = 0; P.at = Date.now(); save(); }
    var go = function () { showBar(); run(); paint(); };
    if (document.body) go(); else document.addEventListener("DOMContentLoaded", go);
  } else {
    // pages render their inline players after the data arrives; paint them idle once
    PROTO.ready.then(function () { setTimeout(paint, 0); });
  }
  document.addEventListener("proto:ready", function () { setTimeout(paint, 0); });
  // inline players are created after the data loads, so repaint when the page adds one
  new MutationObserver(function (m) { if (m.some(function (r) { return [].some.call(r.addedNodes, function (n) { return n.nodeType === 1 && (n.matches(".ap") || n.querySelector(".ap")); }); })) paint(); })
    .observe(document.documentElement, { childList: true, subtree: true });
})();

// First visit: ask about notifications in two steps. A short in-page explanation of why
// (breaking news and a daily digest), and only if the reader agrees does the browser's own
// permission prompt appear. Asked once; either answer is remembered, and nothing is asked
// if the browser has no notifications or the permission is already decided.
(function () {
  var KEY = "oin-notif-asked";
  function seen() { try { return !!localStorage.getItem(KEY); } catch (e) { return false; } }
  function mark(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  if (!("Notification" in window)) return;

  function toast(msg) {
    var t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 300); }, 4200);
  }
  var BELL = '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0112 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 003.4 0"/></svg>';
  var dlg = null, opener = null;

  function close(answer) {
    if (!dlg) return;
    mark(answer); dlg.remove(); dlg = null; document.body.style.overflow = "";
    if (opener && opener.focus) opener.focus();
  }
  function ask() {
    // Step 2: the browser's own prompt. The explanation stays up until it answers.
    var go = dlg.querySelector(".np-go");
    go.disabled = true; go.innerHTML = '<i class="spin"></i><span>Waiting for your browser…</span>';
    var done = function (perm) {
      close(perm);
      if (perm === "granted") {
        toast("Notifications are on");
        try { new Notification("Open Indian News", { body: "You’ll get breaking news alerts and your daily digest here. No ads, ever." }); } catch (e) {}
      } else if (perm === "denied") {
        toast("Notifications are off. You can turn them on in your browser’s site settings.");
      }
    };
    try {
      var r = Notification.requestPermission(done);   // older browsers take a callback
      if (r && r.then) r.then(done, function () { done("default"); });
    } catch (e) { done("default"); }
  }
  function open(force) {
    if (dlg || (!force && seen())) return;
    opener = document.activeElement;
    dlg = document.createElement("div");
    dlg.className = "np";
    dlg.innerHTML = '<div class="np-card" role="dialog" aria-modal="true" aria-labelledby="np-t">' +
      '<div class="np-ic">' + BELL + '</div><h2 id="np-t">Never miss a breaking story</h2>' +
      '<p class="np-sub">Turn on notifications and we’ll tell you when it matters, and only then.</p>' +
      '<ul class="np-l"><li><b>Breaking news</b><span>An alert the moment a major story breaks.</span></li>' +
      '<li><b>Your daily digest</b><span>A summary at the times of day you choose.</span></li>' +
      '<li><b>Stories you follow</b><span>A nudge when a story you follow moves.</span></li></ul>' +
      '<button type="button" class="ab filled np-go"><span>Turn on notifications</span></button>' +
      '<button type="button" class="auth-link np-no">Not now</button>' +
      '<p class="np-fine">Your browser will ask you to confirm next. You can change this any time.</p></div>';
    document.body.appendChild(dlg); document.body.style.overflow = "hidden";
    dlg.querySelector(".np-go").addEventListener("click", ask);
    dlg.querySelector(".np-no").addEventListener("click", function () { close("later"); });
    dlg.addEventListener("keydown", function (e) { if (e.key === "Escape" && !dlg.querySelector(".np-go").disabled) { e.stopPropagation(); close("later"); } });
    dlg.querySelector(".np-go").focus();
  }
  // a beat after the page is up, so it does not land on top of the first paint
  function tryOpen() {
    var decided = false; try { decided = !!localStorage.getItem("oin-cookie-consent"); } catch (e) { decided = true; }
    if (!decided) { setTimeout(tryOpen, 700); return; }
    open();
  }
  // the bell on phones asks again on request, if the browser has not decided yet
  PROTO.askNotifications = function () { if (Notification.permission === "default") open(true); return Notification.permission === "default"; };
  if (Notification.permission === "default" && !seen()) setTimeout(tryOpen, 1500);
})();

// Cookie notice. A small banner on the first visit, with "Accept all", "Essential only" and
// "Manage". Manage opens the full choices (also reachable from the footer's "Cookie
// settings"). There are no advertising cookies on this site, so there is no switch for them,
// only a line saying so. The choice is kept in this browser; PROTO.consent holds it.
(function () {
  var KEY = "oin-cookie-consent", banner = null, prefs = null, opener = null;
  function get() { try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; } }
  PROTO.consent = get();
  function save(c) {
    c.essential = true; c.ts = Date.now();
    try { localStorage.setItem(KEY, JSON.stringify(c)); } catch (e) {}
    PROTO.consent = c;
    document.dispatchEvent(new CustomEvent("oin:consent", { detail: c }));
  }
  function closeBanner() { if (banner) { banner.classList.add("out"); var b = banner; banner = null; setTimeout(function () { b.remove(); }, 260); } }

  function showBanner() {
    if (banner || get()) return;
    banner = document.createElement("section");
    banner.className = "ck"; banner.setAttribute("role", "dialog"); banner.setAttribute("aria-labelledby", "ck-t"); banner.setAttribute("aria-label", "Cookies");
    banner.innerHTML = '<h2 id="ck-t">Cookies, kept to the minimum</h2>' +
      '<p>We use a few cookies and similar storage to keep you signed in and remember your choices. <b>No advertising cookies, ever.</b> <a href="/privacy">Read how we use them</a>.</p>' +
      '<div class="ck-b"><button type="button" class="ab filled" data-ck="all"><span>Accept all</span></button>' +
      '<button type="button" class="ab" data-ck="min"><span>Essential only</span></button>' +
      '<button type="button" class="auth-link" data-ck="manage">Manage</button></div>';
    document.body.appendChild(banner);
    banner.addEventListener("click", function (e) {
      var b = e.target.closest("[data-ck]"); if (!b) return;
      if (b.dataset.ck === "all") { save({ preferences: true, analytics: true }); closeBanner(); }
      else if (b.dataset.ck === "min") { save({ preferences: false, analytics: false }); closeBanner(); }
      else openPrefs();
    });
  }

  function sw(id, label, desc, on, locked) {
    return '<div class="ckr"><div><b>' + label + "</b><span>" + desc + '</span></div><label class="ksw' + (locked ? " lock" : "") + '"><input type="checkbox" id="' + id + '" role="switch"' + (on ? " checked" : "") + (locked ? " disabled" : "") + ' aria-label="' + label + '"><i></i></label></div>';
  }
  function openPrefs() {
    if (prefs) return;
    opener = document.activeElement;
    var c = get() || { preferences: false, analytics: false };
    prefs = document.createElement("div");
    prefs.className = "ckp";
    prefs.innerHTML = '<div class="ckp-card" role="dialog" aria-modal="true" aria-labelledby="ckp-t"><button type="button" class="auth-x" data-ck="close" aria-label="Close">&times;</button>' +
      '<h2 id="ckp-t">Cookie settings</h2><p class="ckp-sub">Choose what we may store in this browser. You can change this any time from the footer.</p>' +
      sw("ck-ess", "Essential", "Keeps you signed in and remembers this choice. Always on.", true, true) +
      sw("ck-pref", "Preferences", "Your theme, saved stories, what you follow and which notices you have dismissed.", !!c.preferences) +
      sw("ck-ana", "Analytics", "Anonymous counts of which pages are read, so we can improve the site. Never linked to you.", !!c.analytics) +
      '<div class="ckr"><div><b>Advertising</b><span>We do not use any. There are no ads on this site, and no ad tracking.</span></div><span class="ck-never">Never</span></div>' +
      '<div class="ckp-b"><button type="button" class="ab filled" data-ck="save"><span>Save my choices</span></button>' +
      '<button type="button" class="ab" data-ck="all2"><span>Accept all</span></button></div></div>';
    document.body.appendChild(prefs); document.body.style.overflow = "hidden";
    prefs.addEventListener("mousedown", function (e) { if (e.target === prefs) closePrefs(); });
    prefs.addEventListener("click", function (e) {
      var b = e.target.closest("[data-ck]"); if (!b) return;
      var k = b.dataset.ck;
      if (k === "close") closePrefs();
      else if (k === "save") { save({ preferences: document.getElementById("ck-pref").checked, analytics: document.getElementById("ck-ana").checked }); closePrefs(true); closeBanner(); }
      else if (k === "all2") { save({ preferences: true, analytics: true }); closePrefs(true); closeBanner(); }
    });
    prefs.querySelector("#ck-pref").focus();
  }
  function closePrefs(saved) {
    if (!prefs) return;
    prefs.remove(); prefs = null; document.body.style.overflow = "";
    if (!saved && opener && opener.focus) opener.focus();
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && prefs) { e.stopPropagation(); closePrefs(); } }, true);
  // footer link "Cookie settings"
  document.addEventListener("click", function (e) {
    var a = e.target.closest('a[href="#cookie-settings"]');
    if (a) { e.preventDefault(); openPrefs(); }
  });
  setTimeout(showBanner, 700);
})();

// Phone chrome, after the Android app: a top app bar (logo, a title that swaps to today's date
// after five seconds, a day/night toggle and search) and a floating blurred pill of five
// icon tabs at the bottom. The top bar slides away when you scroll down and comes back when you scroll up; the bottom tabs stay put.
// Only shown at 700px and below; the desktop header is used above that.
PROTO.toast = function (msg) {
  var t = document.createElement("div");
  t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 300); }, 2600);
};
PROTO.ready.then(function (d) {
  var mast = document.querySelector("header.masthead");
  if (!mast) return;
  function fsvg(inner) { return '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' + inner + "</svg>"; }
  function svg(inner, extra) { return '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' + (extra || "") + ">" + inner + "</svg>"; }
  var IC = {
    sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    moon: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
    // The bottom tabs use the Android app's own icons, redrawn as SVG from AnimatedNavIcons.kt and
    // ic_services_grid.xml: solid shapes, filled with the current colour.
    // Home: a dog-eared page with a photo thumbnail cut out and a lighter folded corner.
    home: fsvg('<path fill-rule="evenodd" d="M4.8 2.88H16.32L21.12 7.68V19.2Q21.12 21.12 19.2 21.12H4.8Q2.88 21.12 2.88 19.2V4.8Q2.88 2.88 4.8 2.88Z M5.91 5.8H10.43A.84.84 0 0 1 11.27 6.64V11.16A.84.84 0 0 1 10.43 12H5.91A.84.84 0 0 1 5.07 11.16V6.64A.84.84 0 0 1 5.91 5.8Z"/><path class="fold" d="M16.32 2.88L21.12 7.68H16.32Z" opacity=".55"/>'),
    // Watch: a solid TV with a play triangle cut out and two antenna lines.
    watch: fsvg('<path fill-rule="evenodd" d="M5.5 6H18.5A3.5 3.5 0 0 1 22 9.5V17.5A3.5 3.5 0 0 1 18.5 21H5.5A3.5 3.5 0 0 1 2 17.5V9.5A3.5 3.5 0 0 1 5.5 6Z M10 10V17L16 13.5Z"/><path d="M12 6L8 2.8M12 6L16 2.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/>'),
    // Timelines (the app calls it Context): an open book, two pages either side of the spine.
    timelines: fsvg('<rect x="2.88" y="3.84" width="8.4" height="16.8" rx="2.4"/><rect class="pg" x="12.72" y="3.84" width="8.4" height="16.8" rx="2.4"/>'),
    // Profile: Material's solid person.
    profile: fsvg('<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>')
  };
  var now = new Date(), day = now.toLocaleDateString("en-IN", { weekday: "long" }), dm = now.toLocaleDateString("en-IN", { day: "numeric", month: "long" });
  mast.insertAdjacentHTML("afterend",
    '<header class="appbar"><a class="ab-brand" href="/" aria-label="Open Indian News, top of the feed"><span class="ph round ab-logo" aria-hidden="true"></span>' +
    '<span class="ab-title"><span class="ab-t1"><b>' + PROTO.esc(d.brand.name) + "</b><small>Multi-Outlet Clustered Feed &amp; Framing Intelligence</small></span>" +
    '<span class="ab-t2"><b>' + day + "</b><small>" + dm + "</small></span></span></a>" +
    '<div class="ab-act"><button type="button" class="ab-i" id="ab-theme"></button><button type="button" class="ab-i" id="ab-search" aria-label="Search">' + IC.search + "</button></div></header>");
  document.body.insertAdjacentHTML("beforeend",
    '<nav class="tabbar" aria-label="Main"><div class="tb-row"><a class="tb" data-tab="home" href="/" aria-label="Home">' + IC.home + '<span class="tb-l">Home</span></a>' +
    '<a class="tb" data-tab="watch" href="/watch" aria-label="Watch">' + IC.watch + '<span class="tb-l">Watch</span></a>' +
    '<a class="tb" data-tab="context" href="/context" aria-label="Context">' + IC.timelines + '<span class="tb-l">Context</span></a>' +
    '<button type="button" class="tb" data-tab="profile" id="tb-profile" aria-label="Profile">' + IC.profile + '<span class="tb-l">Profile</span></button></div></nav>');
  var bar = document.querySelector(".appbar"), tabs = document.querySelector(".tabbar");
  document.dispatchEvent(new Event("oin:tabbar"));   // the player may already be showing; let it move into the pill

  // title swaps to the date once, five seconds in
  setTimeout(function () { bar.classList.add("show-date"); }, 5000);

  // which tab is lit
  function setActive() {
    var home = location.pathname === "/", ctx = /^\/(context|timeline|explainer)(\/|$)/.test(location.pathname);
    var on = ctx ? "context" : home ? "home" : location.pathname === "/watch" ? "watch" : location.pathname === "/profile" ? "profile" : "";
    [].forEach.call(tabs.querySelectorAll("[data-tab]"), function (t) {
      var is = t.dataset.tab === on;
      t.classList.toggle("on", is); if (is) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
    });
  }
  setActive(); addEventListener("hashchange", setActive);

  // the profile tab shows your initial once you are signed in
  var prof = document.getElementById("tb-profile");
  function syncProfile() {
    var u = null; try { u = JSON.parse(localStorage.getItem("oin-session")); } catch (e) {}
    prof.classList.toggle("sess", !!u);
    prof.innerHTML = (u ? '<span class="tb-av" aria-hidden="true">' + PROTO.esc((u.name || u.email || "?").trim().charAt(0).toUpperCase()) + "</span>" : IC.profile) + '<span class="tb-l">Profile</span>';
    prof.setAttribute("aria-label", u ? "Profile: " + u.name : "Sign in");
  }
  syncProfile();
  var ub = document.getElementById("user");
  if (ub) new MutationObserver(syncProfile).observe(ub, { childList: true, attributes: true });

  tabs.addEventListener("click", function (e) {
    var soon = e.target.closest("[data-soon]"), p = e.target.closest("#tb-profile");
    if (soon) PROTO.toast(soon.dataset.soon + " isn’t in the prototype yet");
    else if (p) { e.preventDefault(); e.stopPropagation(); location.href = "/profile"; }
  });
  document.getElementById("ab-search").addEventListener("click", function () {
    var so = document.getElementById("search-open");
    if (so) so.click(); else location.href = "/?s=1";
  });
  // day / night toggle: shows a sun while it is dark (tap for day) and a moon while it is light (tap for night)
  var themeBtn = document.getElementById("ab-theme");
  function paintTheme() {
    var cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    themeBtn.innerHTML = cur === "dark" ? IC.sun : IC.moon;
    themeBtn.setAttribute("aria-label", cur === "dark" ? "Switch to day mode" : "Switch to night mode");
  }
  themeBtn.addEventListener("click", function () { var t = document.getElementById("theme"); if (t) t.click(); paintTheme(); });
  new MutationObserver(paintTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTheme);
  paintTheme();

  // the top app bar slides away on the way down and returns on the way up; the bottom tabs always stay
  var last = scrollY;
  addEventListener("scroll", function () {
    var y = scrollY, down = y > last + 6, up = y < last - 6;
    if (down && y > 90) bar.classList.add("hide");
    else if (up || y < 90) bar.classList.remove("hide");
    if (down || up) last = y;
  }, { passive: true });
});


// Reading history (shown on the profile's History tab): remember each story opened, newest last.
(function () {
  var m = location.pathname.match(/^\/story\/(\d+)/);
  if (!m) return;
  try {
    var id = +m[1], l = (JSON.parse(localStorage.getItem("oin-read")) || []).filter(function (x) { return x !== id; });
    l.push(id); localStorage.setItem("oin-read", JSON.stringify(l.slice(-50)));
  } catch (e) {}
})();
