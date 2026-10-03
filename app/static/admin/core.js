/* Admin SPA runtime. Served by app/admin_spa.py, talks to /admin/api/* (app/admin_api.py
 * and each section module's /admin/api/<section> router).
 *
 * A section file (sections/*.js) registers its pages:
 *
 *   Admin.page({
 *     path: 'explainers/:id/review',   // route pattern, '' is the home page; :name captures
 *     nav: 'explainers',               // sidebar item to highlight (also picks the page hue)
 *     title: (data, params) => '…',    // navbar crumb; string or function
 *     load: (params, query) => Admin.api.get('/explainers/' + params.id),   // optional, may be async
 *     render: (data, params, query) => html,   // build with Admin.ui helpers
 *     mount: (ctx) => { … },           // optional: bind behaviour after render
 *   });
 *
 * ctx = { root, data, params, query, reload(), actions({name: fn}), every(ms, fn), onCleanup(fn) }
 *   ctx.actions maps clicks on [data-act="name"] inside the page to fn(el, event). A button whose
 *   handler returns a promise shows a spinner until it settles; a thrown ApiError becomes a toast.
 *
 * Admin.api.get/post/put/del(path, bodyOrQuery)  JSON in and out, CSRF header added, 401 → login.
 * Admin.table(el, opts)   DataTables wrapper (client rows or server paging, sort, search, bulk select).
 * Admin.toast(msg, hue) · Admin.confirm({title, text, ok, danger}) → Promise<bool>
 * Admin.go(route) · Admin.href(route) · Admin.refreshBadges()
 * Admin.ui: esc, icon, head, st, pill, empty, panel, fmt.*
 */
(() => {
  'use strict';
  const BASE = window.ADMIN_BASE || '';
  const API = BASE + '/api';
  const Admin = (window.Admin = {});
  const $ = (s, r = document) => r.querySelector(s);

  /* ---------- icons ---------- */
  const I = {
    today: '<path d="M3.5 11 12 4l8.5 7v8.5a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z"/>',
    breaking: '<path d="M13 2.5 4.5 14H11l-1 7.5L19.5 10H13z"/>',
    reports: '<path d="M5 21V4m0 0h12l-2.5 4.5L17 13H5"/>',
    feedback: '<path d="M3.5 13 6.5 5h11l3 8v6.5h-17z"/><path d="M3.5 13H8l1.2 2.5h5.6L16 13h4.5"/>',
    explainers: '<path d="M4 19.5V6a2.5 2.5 0 0 1 2.5-2.5H20v14H6.5A2.5 2.5 0 0 0 4 20a.5.5 0 0 0 .5.5H20"/><path d="M9 8h6M9 11.5h4"/>',
    timelines: '<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="6" cy="12" r="2"/><path d="M11 6h9M11 12h6M11 18h8"/>',
    brief: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    announcements: '<path d="M3.5 10v4h3.5l7 5V5l-7 5z"/><path d="M17.5 9a4 4 0 0 1 0 6"/>',
    polls: '<path d="M5 20V11M12 20V4M19 20v-6"/>',
    quiz: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.3a2.6 2.6 0 1 1 3.6 2.4c-.7.3-1.1.9-1.1 1.6v.7M12 17h.01"/>',
    bank: '<path d="M3.5 9 12 4l8.5 5M5 9.5v8M9.7 9.5v8M14.3 9.5v8M19 9.5v8M3.5 20h17"/>',
    users: '<circle cx="9.5" cy="7.5" r="3.5"/><path d="M3 19.5v-1a4.5 4.5 0 0 1 4.5-4.5h4a4.5 4.5 0 0 1 4.5 4.5v1M16 3.6a3.5 3.5 0 0 1 0 7.8M21 19.5v-1a4.5 4.5 0 0 0-3-4.2"/>',
    topics: '<path d="M5 9h14M5 15h14M10.5 4l-2 16M15.5 4l-2 16"/>',
    donations: '<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>',
    arrow: '<path d="M7 17 17 7M9 7h8v8"/>',
    check: '<path d="M5 12.5 10 17l9-10"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    send: '<path d="M4 12 20 4l-5 16-3-7z"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4"/>',
    play: '<path d="M7 5v14l12-7z"/>',
    trash: '<path d="M4.5 7h15M9 7V4.5h6V7M6.5 7l1 13h9l1-13"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="M20.5 16l-5-5-8.5 8.5"/>',
    mic: '<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    archive: '<rect x="3.5" y="4.5" width="17" height="4" rx="1"/><path d="M5 8.5v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-10M10 12.5h4"/>',
    restore: '<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/>',
    sparkle: '<path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z"/><path d="M18.5 16.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    external: '<path d="M14 4.5h5.5V10M19.5 4.5 11 13M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    logout: '<path d="M14.5 4.5h4a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-4M10 16.5 5.5 12 10 7.5M5.5 12h10"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5v-3a1 1 0 0 0-1-1h-9a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h3"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  };
  const icon = (k, attrs = '') => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true" ${attrs}>${I[k] || ''}</svg>`;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ---------- formatting ---------- */
  const IST = 'Asia/Kolkata';
  const toDate = (v) => (v instanceof Date ? v : v ? new Date(v) : null);
  const fmt = {
    num: (n) => (n == null ? '—' : Number(n).toLocaleString('en-IN')),
    inr: (rupees, digits = 0) => (rupees == null ? '—' : '₹' + Number(rupees).toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })),
    date: (v) => { const d = toDate(v); return d ? d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: IST }) : '—'; },
    day: (v) => { const d = toDate(v); return d ? d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', timeZone: IST }) : '—'; },
    time: (v) => { const d = toDate(v); return d ? d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: IST }) : '—'; },
    dateTime: (v) => { const d = toDate(v); return d ? `${fmt.date(d)}, ${fmt.time(d)}` : '—'; },
    rel: (v) => {
      const d = toDate(v); if (!d) return '—';
      const s = (Date.now() - d.getTime()) / 1000, f = s < 0, a = Math.abs(s);
      const out = a < 60 ? 'just now' : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${Math.round(a / 3600)} h` : a < 172800 ? (f ? 'tomorrow' : 'yesterday') : `${Math.round(a / 86400)} days`;
      return a < 60 || a >= 86400 && a < 172800 ? out : f ? `in ${out}` : `${out} ago`;
    },
    plural: (n, one, many = one + 's') => `${fmt.num(n)} ${n === 1 ? one : many}`,
  };

  /* ---------- page-building helpers ---------- */
  let si = 0;
  const ui = {
    esc, icon, fmt,
    st: (inner, cls = '') => `<section class="st ${cls}" style="--d:${60 + si++ * 80}ms">${inner}</section>`,
    head: (eyebrow, title, sub = '', actions = '') => ui.st(`<div class="head"><div><span class="eyebrow mono"><i></i>${esc(eyebrow)}</span><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ''}</div>${actions ? `<div class="head-actions">${actions}</div>` : ''}</div>`),
    pill: (text, hue = '--muted', solid = false) => `<span class="pill${solid ? ' solid' : ''}" style="--c:var(${hue})${solid && hue === '--amber' ? ';color:var(--on-amber)' : ''}">${esc(text)}</span>`,
    empty: (text) => `<div class="empty-res">${text}</div>`,
    panel: (title, body, right = '', cls = '') => `<div class="panel ${cls}">${title || right ? `<div class="panel-h">${title ? `<h2>${title}</h2>` : '<span></span>'}${right}</div>` : ''}${body}</div>`,
    btn: (label, { act = '', id = '', kind = 'ink', size = '', ic = '', attrs = '' } = {}) =>
      `<button class="btn btn-${kind}${size ? ' btn-' + size : ''}"${act ? ` data-act="${act}"` : ''}${id !== '' ? ` data-id="${esc(id)}"` : ''} ${attrs}>${ic ? icon(ic) : ''}${label}</button>`,
    sample: () => '',
  };
  Admin.ui = ui;

  /* ---------- API ---------- */
  let csrf = null;
  class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
  Admin.ApiError = ApiError;
  const detailText = (d) => Array.isArray(d) ? d.map((e) => (e.loc ? e.loc.slice(1).join('.') + ': ' : '') + e.msg).join('; ') : typeof d === 'string' ? d : JSON.stringify(d);

  async function request(method, path, body) {
    let url = API + path;
    const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method === 'GET' && body) {
      const qs = new URLSearchParams();
      Object.entries(body).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') qs.set(k, v); });
      const s = qs.toString(); if (s) url += (url.includes('?') ? '&' : '?') + s;
    } else if (body !== undefined && body !== null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (method !== 'GET') init.headers['X-CSRF-Token'] = csrf || '';
    let res;
    try { res = await fetch(url, init); } catch (e) { throw new ApiError('Could not reach the server. Check your connection and try again.', 0); }
    const isJson = (res.headers.get('content-type') || '').includes('json');
    const data = isJson ? await res.json().catch(() => null) : await res.text();
    if (res.status === 401 && !path.startsWith('/login')) { showLogin(); throw new ApiError('Your session ended. Sign in again.', 401); }
    if (!res.ok) {
      const msg = data && data.detail ? detailText(data.detail) : res.status === 429 ? 'Too many requests. Wait a minute and try again.' : `Request failed (${res.status}).`;
      throw new ApiError(msg, res.status);
    }
    return data;
  }
  Admin.api = {
    get: (p, q) => request('GET', p, q),
    post: (p, b = {}) => request('POST', p, b),
    put: (p, b = {}) => request('PUT', p, b),
    patch: (p, b = {}) => request('PATCH', p, b),
    del: (p, b) => request('DELETE', p, b),
  };

  /* ---------- nav ---------- */
  const NAV = [
    { id: 'today', route: '', label: 'Today', group: 'Review', hue: '--blue', icon: 'today' },
    { id: 'breaking', route: 'breaking', label: 'Breaking', group: 'Review', hue: '--red', icon: 'breaking' },
    { id: 'reports', route: 'reports', label: 'Story reports', group: 'Review', hue: '--orange', icon: 'reports' },
    { id: 'feedback', route: 'feedback', label: 'Feedback', group: 'Review', hue: '--blue', icon: 'feedback' },
    { id: 'explainers', route: 'explainers', label: 'Explainers', group: 'Editorial', hue: '--purple', icon: 'explainers' },
    { id: 'timelines', route: 'timelines', label: 'Timelines', group: 'Editorial', hue: '--teal', icon: 'timelines' },
    { id: 'daily-brief', route: 'daily-brief', label: 'Daily Brief', group: 'Editorial', hue: '--amber', icon: 'brief' },
    { id: 'topics', route: 'topics', label: 'Hot topics', group: 'Editorial', hue: '--orange', icon: 'topics' },
    { id: 'announcements', route: 'announcements', label: 'Announcements', group: 'Editorial', hue: '--pink', icon: 'announcements' },
    { id: 'polls', route: 'polls', label: 'Poll of the Day', group: 'Games', hue: '--green', icon: 'polls' },
    { id: 'poll-bank', route: 'poll-bank', label: 'Poll bank', group: 'Games', hue: '--green', icon: 'bank' },
    { id: 'quiz', route: 'quiz', label: 'Daily Quiz', group: 'Games', hue: '--green', icon: 'quiz' },
    { id: 'quiz-bank', route: 'quiz-bank', label: 'Quiz bank', group: 'Games', hue: '--green', icon: 'bank' },
    { id: 'users', route: 'users', label: 'Users', group: 'Audience', hue: '--blue', icon: 'users' },
    { id: 'donations', route: 'donations', label: 'Donations', group: 'Audience', hue: '--pink', icon: 'donations' },
  ];
  const navById = Object.fromEntries(NAV.map((n) => [n.id, n]));
  Admin.nav = NAV;
  let badges = {};
  let activeNav = 'today';

  function renderNav() {
    let html = '', g = '';
    for (const n of NAV) {
      if (n.group !== g) { g = n.group; html += `<div class="nav-group mono">${g}</div>`; }
      const c = badges[n.id] || 0;
      html += `<a href="${Admin.href(n.route)}" data-label="${esc(n.label)}" class="${n.id === activeNav ? 'on' : ''}" style="--c:var(${n.hue})"${n.id === activeNav ? ' aria-current="page"' : ''}>${icon(n.icon)}<span class="lbl">${esc(n.label)}</span>${c ? `<span class="badge">${c > 99 ? '99+' : c}</span>` : ''}<i class="dot"></i></a>`;
    }
    $('#nav').innerHTML = html;
  }

  Admin.overview = null;
  let badgeTimer = null;
  Admin.refreshBadges = async () => {
    try {
      const o = await Admin.api.get('/overview');
      Admin.overview = o; badges = o.badges || {};
      renderNav();
      $('#bell .pip').hidden = !Object.values(badges).some((n) => n > 0);
    } catch (e) { /* the page's own load reports errors; badges just stay stale */ }
  };

  /* ---------- router ---------- */
  const pages = [];
  Admin.page = (def) => {
    const keys = [];
    const rx = new RegExp('^' + def.path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '/?$');
    pages.push({ def, rx, keys });
  };
  Admin.href = (route = '') => `${BASE}/${String(route).replace(/^\/+/, '')}`;
  /* Links to the public site/API (e.g. /api/v1/clusters/12). On admin.openindiannews.com every
     path is rewritten into /admin, so those must go to the main origin explicitly. */
  Admin.publicUrl = (path) => (BASE ? path : 'https://openindiannews.com' + path);
  Admin.go = (route, { replace = false } = {}) => {
    const url = Admin.href(route);
    if (url === location.pathname + location.search) return render({ keepScroll: true, animate: false });
    history[replace ? 'replaceState' : 'pushState'](null, '', url);
    render();
  };
  /* Updates the query string without re-rendering (filters remembered in the URL). */
  Admin.setQuery = (params) => {
    const q = new URLSearchParams(location.search);
    Object.entries(params).forEach(([k, v]) => (v === undefined || v === null || v === '' ? q.delete(k) : q.set(k, v)));
    const s = q.toString();
    history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
  };

  function currentPath() {
    let p = location.pathname;
    if (BASE && p.startsWith(BASE)) p = p.slice(BASE.length);
    return p.replace(/^\/+|\/+$/g, '');
  }

  const app = $('#app'), view = $('#view'), progress = $('#progress');
  let token = 0, cleanups = [], tables = [];
  const tableState = {};

  function teardown() {
    tables.forEach((t) => t._save());
    tables = [];
    cleanups.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
    cleanups = [];
  }

  function setChrome(def, data, params) {
    activeNav = def.nav || 'today';
    const n = navById[activeNav] || NAV[0];
    app.style.setProperty('--hue', `var(${n.hue})`);
    const title = typeof def.title === 'function' ? def.title(data, params) : def.title || n.label;
    $('#crumb').innerHTML = `<i class="hue-dot"></i><span>${esc(title)}</span>`;
    document.title = `${title} · OIN Admin`;
    renderNav();
  }

  async function render({ keepScroll = false, animate = true } = {}) {
    const my = ++token;
    const path = currentPath();
    const query = Object.fromEntries(new URLSearchParams(location.search));
    let match = null, params = {};
    for (const p of pages) {
      const m = path.match(p.rx);
      if (m) { match = p; p.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1]))); break; }
    }
    if (!match) match = { def: NOT_FOUND };
    const def = match.def;
    teardown();
    setChrome(def, null, params);
    app.classList.remove('nav-open'); $('#scrim').classList.remove('show');

    progress.className = 'topbar-progress on';
    const top = view.scrollTop;
    const skelTimer = keepScroll ? null : setTimeout(() => { if (my === token) view.innerHTML = SKELETON; }, 160);
    let data = null;
    try {
      data = def.load ? await def.load(params, query) : null;
    } catch (e) {
      clearTimeout(skelTimer);
      if (my !== token) return;
      progress.className = 'topbar-progress done';
      if (e.status === 401) return;
      si = 0;
      view.innerHTML = `<div class="wrap">${ui.panel('', `<div class="err"><b>This page didn't load.</b><span class="muted">${esc(e.message)}</span>${ui.btn('Try again', { act: '__retry', ic: 'refresh' })}</div>`)}</div>`;
      view.querySelector('[data-act="__retry"]').onclick = () => render();
      return;
    }
    clearTimeout(skelTimer);
    if (my !== token) return;
    progress.className = 'topbar-progress done';
    setChrome(def, data, params);

    si = 0;
    let html;
    try { html = def.render(data, params, query); } catch (e) { console.error(e); html = ui.panel('', `<div class="err"><b>This page hit a bug.</b><span class="muted">${esc(e.message)}</span></div>`); }
    view.innerHTML = `<div class="wrap">${html}</div>`;
    if (!animate) view.querySelectorAll('.st').forEach((s) => (s.style.animation = 'none'));
    view.scrollTop = keepScroll ? top : 0;

    const root = view.firstElementChild;
    const handlers = {};
    const ctx = {
      root, data, params, query,
      reload: (opts) => render({ keepScroll: true, animate: false, ...opts }),
      actions: (map) => Object.assign(handlers, map),
      onCleanup: (fn) => cleanups.push(fn),
      every: (ms, fn) => { const id = setInterval(() => { if (my === token) fn(); }, ms); cleanups.push(() => clearInterval(id)); return id; },
    };
    root.addEventListener('click', async (e) => {
      const el = e.target.closest('[data-act]');
      if (!el || !root.contains(el)) return;
      const fn = handlers[el.dataset.act];
      if (!fn) return;
      e.preventDefault();
      if (el.classList.contains('busy')) return;
      const isBtn = el.tagName === 'BUTTON';
      let r;
      try {
        r = fn(el, e);
        if (r && typeof r.then === 'function') {
          if (isBtn) { el.classList.add('busy'); el.disabled = true; }
          await r;
        }
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 401)) Admin.toast(err.message || 'Something went wrong.', '--red');
        console.error(err);
      } finally {
        if (isBtn && el.isConnected) { el.classList.remove('busy'); el.disabled = false; }
      }
    });
    try { def.mount && def.mount(ctx); } catch (e) { console.error(e); Admin.toast('Part of this page failed to start: ' + e.message, '--red'); }
  }
  Admin.reload = () => render({ keepScroll: true, animate: false });

  const SKELETON = `<div class="wrap"><div class="skel" style="height:86px;max-width:520px"></div><div class="grid g-4"><div class="skel" style="height:140px"></div><div class="skel" style="height:140px"></div><div class="skel" style="height:140px"></div><div class="skel" style="height:140px"></div></div><div class="skel" style="height:320px"></div></div>`;
  const NOT_FOUND = {
    nav: 'today', title: 'Not found',
    render: () => ui.head('Not found', 'There is no page here.', 'The link may be from an older version of the admin.', `<a class="btn btn-ink" href="${Admin.href('')}">Go to Today</a>`),
  };

  /* Internal links navigate in place; external ones, new tabs and downloads behave normally. */
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute('download') || a.dataset.external !== undefined) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    if (BASE ? !(url.pathname === BASE || url.pathname.startsWith(BASE + '/')) : false) return;
    const rest = BASE ? url.pathname.slice(BASE.length) : url.pathname;
    if (/^\/(api|assets)(\/|$)/.test(rest)) return;
    if (!BASE && /^\/(api\/v1|static)\//.test(url.pathname)) return;
    e.preventDefault();
    if (url.pathname + url.search === location.pathname + location.search && url.hash) return;
    history.pushState(null, '', url.pathname + url.search);
    render();
  });
  window.addEventListener('popstate', () => render({ keepScroll: false }));

  /* ---------- toasts & confirm ---------- */
  Admin.toast = (msg, hue = '--green') => {
    const t = document.createElement('div');
    t.className = 'toast'; t.style.setProperty('--c', `var(${hue})`); t.setAttribute('role', 'status');
    t.innerHTML = `<i></i><span>${esc(msg)}</span>`;
    $('#toasts').appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, hue === '--red' ? 5200 : 2800);
  };
  Admin.confirm = ({ title, text = '', ok = 'Confirm', danger = false, input = null }) => new Promise((resolve) => {
    const m = document.createElement('div');
    m.className = 'modal';
    m.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="mdl-t"><h3 id="mdl-t">${esc(title)}</h3>${text ? `<p>${text}</p>` : ''}${input ? `<div class="field" style="margin:-6px 0 18px"><label for="mdl-in">${esc(input.label)}</label><input class="inp" id="mdl-in" value="${esc(input.value || '')}" placeholder="${esc(input.placeholder || '')}"></div>` : ''}<div class="acts"><button class="btn btn-ghost" data-x>Cancel</button><button class="btn ${danger ? 'btn-red' : 'btn-ink'}" data-ok>${esc(ok)}</button></div></div>`;
    document.body.appendChild(m);
    const inp = m.querySelector('#mdl-in');
    const close = (v) => { m.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(false); if (e.key === 'Enter' && inp && document.activeElement === inp) close(inp.value); };
    document.addEventListener('keydown', onKey);
    m.addEventListener('click', (e) => {
      if (e.target === m || e.target.closest('[data-x]')) close(false);
      if (e.target.closest('[data-ok]')) close(inp ? inp.value : true);
    });
    (inp || m.querySelector('[data-ok]')).focus();
  });
  Admin.copy = async (text, label = 'Copied') => {
    try { await navigator.clipboard.writeText(text); Admin.toast(label); } catch (e) { Admin.toast('Copy is blocked here. Select the text instead.', '--amber'); }
  };

  /* ---------- tables (DataTables 2) ----------
   * Admin.table(el, {
   *   key: 'users',                         // remembers page/sort/search/selection across reloads
   *   columns: [{ title, data: 'field' | (row) => value, render: (row) => html, className, orderable, searchable, lead }],
   *   rows: [...]            // client-side mode, or
   *   server: async ({ start, length, search, orderBy, dir }) => ({ total, filtered, rows }),
   *   order: [[1, 'desc']], pageLength: 10, placeholder: 'Search…', empty: 'Nothing here yet',
   *   select: { id: (row) => row.id, actions: [{ label, primary, run: async (ids, rows) => {} }] },
   * })  → { dt, selected(), clear(), refresh() }
   * `orderBy` in server mode is the column's `data` key when it is a string.
   */
  Admin.table = (el, opts) => {
    const key = opts.key || el.id || 'table';
    const st = tableState[key] || {};
    const sel = opts.select ? (st.sel || new Set()) : null;
    const cols = opts.columns.slice();
    const offset = sel ? 1 : 0;
    const head = (sel ? `<th class="cb"><input type="checkbox" class="pick" data-all aria-label="Select all on this page"></th>` : '') +
      cols.map((c) => `<th class="${c.className || ''}">${esc(c.title || '')}</th>`).join('');
    const wrap = document.createElement('div');
    wrap.className = 'tbl-host';
    if (sel) wrap.innerHTML = `<div class="bulk" hidden><b>0 selected</b>${opts.select.actions.map((a, i) => `<button class="btn btn-sm${a.primary ? ' hi' : ''}" data-bulk="${i}">${esc(a.label)}</button>`).join('')}<button class="btn btn-sm" data-bulk="clear">Clear</button></div>`;
    const table = document.createElement('table');
    table.className = `stack${sel ? ' has-cb' : ''}`;
    table.innerHTML = `<thead><tr>${head}</tr></thead><tbody></tbody>`;
    wrap.appendChild(table);
    el.replaceChildren(wrap);

    const valueOf = (c, row) => (typeof c.data === 'function' ? c.data(row) : c.data ? row[c.data] : '');
    const dtCols = [];
    if (sel) dtCols.push({ data: null, orderable: false, searchable: false, className: 'cb', render: (d, t, row) => t === 'display' ? `<input type="checkbox" class="pick" data-sel="${esc(opts.select.id(row))}" aria-label="Select row">` : '' });
    cols.forEach((c) => dtCols.push({
      data: null,
      orderable: c.orderable !== false,
      searchable: c.searchable !== false,
      className: [c.className, c.lead ? 'lead' : ''].filter(Boolean).join(' '),
      render: (d, t, row) => {
        if (t === 'display') return c.render ? c.render(row) : esc(valueOf(c, row) ?? '');
        const v = valueOf(c, row);
        return v == null ? '' : v;
      },
      createdCell: c.lead ? undefined : (td) => { if (c.title) td.setAttribute('data-k', c.title); },
    }));

    const config = {
      autoWidth: false,
      columns: dtCols,
      pageLength: st.len || opts.pageLength || 10,
      lengthMenu: [10, 25, 50, 100],
      order: st.order || (opts.order || []).map(([i, d]) => [i + offset, d]),
      // A search passed in by the page (e.g. ?q= from a link) wins over the remembered one.
      displayStart: opts.search ? 0 : st.start || 0,
      search: { search: opts.search || st.search || '' },
      searching: opts.searching !== false,
      layout: { topStart: opts.searching === false ? null : 'search', topEnd: 'pageLength', bottomStart: 'info', bottomEnd: 'paging' },
      language: {
        search: '', searchPlaceholder: opts.placeholder || 'Search this table', lengthMenu: '_MENU_ per page',
        info: '_START_–_END_ of _TOTAL_', infoEmpty: 'No rows', infoFiltered: '· filtered from _MAX_',
        zeroRecords: 'No rows match that search', emptyTable: opts.empty || 'Nothing here yet',
        paginate: { first: '«', last: '»', previous: '‹', next: '›' },
      },
    };
    let lastRows = opts.rows || [];
    if (opts.server) {
      Object.assign(config, {
        serverSide: true, processing: false, searchDelay: 350,
        ajax: (p, cb) => {
          const o = p.order && p.order[0];
          const c = o ? cols[o.column - offset] : null;
          opts.server({ start: p.start, length: p.length, search: p.search.value, orderBy: c && typeof c.data === 'string' ? c.data : undefined, dir: o ? o.dir : undefined })
            .then((r) => { lastRows = r.rows; cb({ draw: p.draw, recordsTotal: r.total, recordsFiltered: r.filtered ?? r.total, data: r.rows }); })
            .catch((e) => { Admin.toast(e.message, '--red'); cb({ draw: p.draw, recordsTotal: 0, recordsFiltered: 0, data: [] }); });
        },
      });
    } else config.data = opts.rows;

    const dt = new DataTable(table, config);
    const rowOf = (id) => (opts.rows || lastRows).find((r) => String(opts.select.id(r)) === String(id));
    const sync = () => {
      if (!sel) return;
      const boxes = [...table.querySelectorAll('tbody input.pick')];
      boxes.forEach((b) => { b.checked = sel.has(b.dataset.sel); b.closest('tr').classList.toggle('sel-row', b.checked); });
      const all = table.querySelector('thead input.pick'), n = boxes.filter((b) => b.checked).length;
      all.checked = boxes.length > 0 && n === boxes.length; all.indeterminate = n > 0 && n < boxes.length;
      const bar = wrap.querySelector('.bulk');
      bar.hidden = sel.size === 0; bar.querySelector('b').textContent = `${sel.size} selected`;
    };
    dt.on('draw', sync);
    if (sel) {
      table.addEventListener('change', (e) => {
        const b = e.target.closest('input.pick'); if (!b) return;
        if (b.dataset.all !== undefined) table.querySelectorAll('tbody input.pick').forEach((c) => (b.checked ? sel.add(c.dataset.sel) : sel.delete(c.dataset.sel)));
        else b.checked ? sel.add(b.dataset.sel) : sel.delete(b.dataset.sel);
        sync();
      });
      wrap.querySelector('.bulk').addEventListener('click', async (e) => {
        const b = e.target.closest('[data-bulk]'); if (!b) return;
        if (b.dataset.bulk === 'clear') { sel.clear(); return sync(); }
        const a = opts.select.actions[+b.dataset.bulk];
        const ids = [...sel];
        b.classList.add('busy'); b.disabled = true;
        try { await a.run(ids, ids.map(rowOf).filter(Boolean)); sel.clear(); }
        catch (err) { Admin.toast(err.message, '--red'); }
        finally { if (b.isConnected) { b.classList.remove('busy'); b.disabled = false; } sync(); }
      });
    }
    sync();
    const handle = {
      dt,
      selected: () => (sel ? [...sel] : []),
      clear: () => { sel && sel.clear(); sync(); },
      refresh: () => (opts.server ? dt.ajax.reload(null, false) : null),
      _save: () => {
        const i = dt.page.info();
        tableState[key] = { order: dt.order(), start: i.start, len: i.length, search: dt.search(), sel };
        dt.destroy();
      },
    };
    tables.push(handle);
    return handle;
  };

  /* ---------- charts (single series; area line and bars) ---------- */
  Admin.chart = {
    line(el, points, { hue = '--blue', fmtY = fmt.num, fmtX = (x) => x, label = '' } = {}) {
      const draw = () => {
        if (!points.length) { el.innerHTML = ui.empty('No data yet.'); return; }
        const W = Math.max(el.clientWidth, 260), H = 200, L = 44, R = 12, T = 12, B = 26;
        const ys = points.map((p) => p.y), max = Math.max(...ys, 1), hi = niceMax(max), lo = 0;
        const n = points.length, x = (i) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1)), y = (v) => T + ((hi - v) / (hi - lo)) * (H - T - B);
        const pts = points.map((p, i) => `${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join(' ');
        let g = '';
        for (let k = 0; k <= 4; k++) { const v = (hi * k) / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(${k ? '--line' : '--line-2'})"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)" font-family="var(--mono)">${esc(fmtY(v))}</text>`; }
        [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i).forEach((i) => { g += `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}" font-size="11" fill="var(--muted)" font-family="var(--mono)">${esc(fmtX(points[i].x))}</text>`; });
        const gid = 'g' + Math.random().toString(36).slice(2, 8);
        el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(label)}"><defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(${hue})" stop-opacity=".28"/><stop offset="1" stop-color="var(${hue})" stop-opacity="0"/></linearGradient></defs>${g}<polygon points="${x(0)},${y(0)} ${pts} ${x(n - 1)},${y(0)}" fill="url(#${gid})"/><polyline points="${pts}" fill="none" stroke="var(${hue})" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><line class="xh" y1="${T}" y2="${H - B}" stroke="var(--ink)" stroke-opacity=".35" stroke-dasharray="3 3" opacity="0"/><circle class="hd" r="5" fill="var(${hue})" stroke="var(--tile)" stroke-width="2" opacity="0"/><circle cx="${x(n - 1)}" cy="${y(points[n - 1].y)}" r="5" fill="var(${hue})" stroke="var(--tile)" stroke-width="2"/></svg><div class="tip"></div>`;
        const svg = el.querySelector('svg'), tip = el.querySelector('.tip'), xh = svg.querySelector('.xh'), hd = svg.querySelector('.hd');
        svg.addEventListener('pointermove', (e) => {
          const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) * W) / r.width;
          const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1))));
          xh.setAttribute('x1', x(i)); xh.setAttribute('x2', x(i)); xh.setAttribute('opacity', 1);
          hd.setAttribute('cx', x(i)); hd.setAttribute('cy', y(points[i].y)); hd.setAttribute('opacity', 1);
          tip.style.left = (x(i) / W) * r.width + 'px'; tip.style.top = (y(points[i].y) / H) * r.height - 6 + 'px'; tip.style.opacity = 1;
          tip.innerHTML = `<b>${esc(fmtY(points[i].y))}</b> · ${esc(fmtX(points[i].x))}`;
        });
        svg.addEventListener('pointerleave', () => { xh.setAttribute('opacity', 0); hd.setAttribute('opacity', 0); tip.style.opacity = 0; });
      };
      draw(); watchResize(el, draw);
    },
    bars(el, points, { hue = '--pink', fmtY = fmt.num, fmtX = (x) => x, label = '', faded = () => false } = {}) {
      const draw = () => {
        if (!points.length) { el.innerHTML = ui.empty('No data yet.'); return; }
        const W = Math.max(el.clientWidth, 260), H = 210, L = 52, R = 8, T = 12, B = 26;
        const hi = niceMax(Math.max(...points.map((p) => p.y), 1));
        const bw = (W - L - R) / points.length, y = (v) => T + ((hi - v) / hi) * (H - T - B);
        let g = '';
        for (let k = 0; k <= 4; k++) { const v = (hi * k) / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(${k ? '--line' : '--line-2'})"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)" font-family="var(--mono)">${esc(fmtY(v))}</text>`; }
        const every = Math.ceil(points.length / 8);
        points.forEach((p, i) => {
          const bx = L + i * bw + bw * 0.18, w = Math.max(bw * 0.64, 2), top = y(p.y), h = y(0) - top, r = Math.min(4, h, w / 2);
          if (h > 0) g += `<path d="M${bx},${y(0)} V${top + r} Q${bx},${top} ${bx + r},${top} H${bx + w - r} Q${bx + w},${top} ${bx + w},${top + r} V${y(0)} Z" fill="var(${hue})" opacity="${faded(p, i) ? 0.45 : 1}"/>`;
          if (i % every === 0) g += `<text x="${bx + w / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--muted)" font-family="var(--mono)">${esc(fmtX(p.x))}</text>`;
          g += `<rect x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}" fill="transparent" data-i="${i}"/>`;
        });
        el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(label)}">${g}</svg><div class="tip"></div>`;
        const svg = el.querySelector('svg'), tip = el.querySelector('.tip');
        svg.querySelectorAll('rect[data-i]').forEach((rc) => {
          rc.addEventListener('pointerenter', () => {
            const i = +rc.dataset.i, r = svg.getBoundingClientRect();
            tip.style.left = ((L + i * bw + bw / 2) / W) * r.width + 'px'; tip.style.top = (y(points[i].y) / H) * r.height - 6 + 'px'; tip.style.opacity = 1;
            tip.innerHTML = `<b>${esc(fmtY(points[i].y))}</b> · ${esc(fmtX(points[i].x))}`;
          });
          rc.addEventListener('pointerleave', () => (tip.style.opacity = 0));
        });
      };
      draw(); watchResize(el, draw);
    },
  };
  function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p; }
  function watchResize(el, draw) {
    if (!window.ResizeObserver) return;
    let w = el.clientWidth, t;
    const ro = new ResizeObserver(() => { if (Math.abs(el.clientWidth - w) < 2) return; w = el.clientWidth; clearTimeout(t); t = setTimeout(draw, 80); });
    ro.observe(el); cleanups.push(() => ro.disconnect());
  }

  /* ---------- sidebar ---------- */
  const wide = matchMedia('(min-width: 1024px)');
  try { if (localStorage.getItem('oin_admin_rail') === '1') app.classList.add('rail'); } catch (e) {}
  $('#toggleNav').addEventListener('click', () => {
    if (wide.matches) {
      app.classList.toggle('rail');
      try { localStorage.setItem('oin_admin_rail', app.classList.contains('rail') ? '1' : '0'); } catch (e) {}
    } else {
      const o = app.classList.toggle('nav-open');
      $('#scrim').classList.toggle('show', o);
    }
  });
  $('#scrim').addEventListener('click', () => { app.classList.remove('nav-open'); $('#scrim').classList.remove('show'); });

  /* ---------- theme ---------- */
  $('#theme').addEventListener('click', () => {
    const root = document.documentElement;
    const dark = root.getAttribute('data-theme') ? root.getAttribute('data-theme') === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('oin_admin_theme', next); } catch (e) {}
  });
  $('#bell').addEventListener('click', () => Admin.go(''));
  $('#signOut').addEventListener('click', async () => {
    try { await Admin.api.post('/logout'); } catch (e) {}
    csrf = null; showLogin();
  });

  /* ---------- search ---------- */
  const q = $('#q'), res = $('#results'), topBar = $('#top');
  let hits = [], act = 0, searchTimer = null, searchSeq = 0;
  const hl = (t, s) => { t = String(t || ''); if (!s) return esc(t); const i = t.toLowerCase().indexOf(s.toLowerCase()); return i < 0 ? esc(t) : esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + s.length)) + '</mark>' + esc(t.slice(i + s.length)); };
  function paint(s) {
    if (!hits.length) { res.innerHTML = s.length < 2 ? ui.empty('Type at least two letters.') : ui.empty(`Nothing matches “${esc(s)}”. Try part of a headline, an email or a name.`); res.hidden = false; return; }
    let g = '', html = s ? '' : '<div class="grp mono">Jump to</div>';
    hits.forEach((h, i) => {
      if (s && h.kind !== g) { g = h.kind; html += `<div class="grp mono">${esc(g)}</div>`; }
      const n = navById[h.nav] || NAV[0];
      html += `<button class="res ${i === act ? 'act' : ''}" data-i="${i}" style="--c:var(${n.hue})"><span class="sw"${n.hue === '--amber' ? ' style="color:var(--on-amber)"' : ''}>${icon(h.url ? 'external' : n.icon)}</span><span class="t"><b>${hl(h.title, s)}</b><small>${esc(h.sub || '')}</small></span></button>`;
    });
    res.innerHTML = html; res.hidden = false;
  }
  function search() {
    const s = q.value.trim();
    const pageHits = NAV.filter((n) => !s || n.label.toLowerCase().includes(s.toLowerCase())).map((n) => ({ kind: 'Pages', title: n.label, sub: n.group, route: n.route, nav: n.id }));
    hits = s ? pageHits.slice(0, 4) : pageHits.slice(0, 7);
    act = 0; paint(s);
    clearTimeout(searchTimer);
    if (s.length < 2) return;
    const seq = ++searchSeq;
    searchTimer = setTimeout(async () => {
      try {
        const r = await Admin.api.get('/search', { q: s });
        if (seq !== searchSeq) return;
        hits = pageHits.slice(0, 3).concat(r.results || []);
        paint(s);
      } catch (e) { /* keep page matches */ }
    }, 250);
  }
  function pick(i) {
    const h = hits[i]; if (!h) return;
    closeSearch();
    if (h.url) window.open(Admin.publicUrl(h.url), '_blank', 'noopener');
    else Admin.go(h.route);
  }
  function closeSearch() { res.hidden = true; q.value = ''; q.blur(); topBar.classList.remove('searching'); }
  q.addEventListener('focus', search);
  q.addEventListener('input', search);
  q.addEventListener('keydown', (e) => {
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && hits.length) {
      e.preventDefault(); act = (act + (e.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
      res.querySelectorAll('.res').forEach((b, i) => { b.classList.toggle('act', i === act); if (i === act && b.scrollIntoView) b.scrollIntoView({ block: 'nearest' }); });
    }
    if (e.key === 'Enter') { e.preventDefault(); pick(act); }
    if (e.key === 'Escape') closeSearch();
  });
  res.addEventListener('mousedown', (e) => { const b = e.target.closest('.res'); if (b) { e.preventDefault(); pick(+b.dataset.i); } });
  q.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== q) res.hidden = true; }, 120));
  $('#openSearch').addEventListener('click', () => { topBar.classList.add('searching'); q.focus(); });
  $('#closeSearch').addEventListener('click', closeSearch);
  document.addEventListener('keydown', (e) => {
    if (!$('#login').hidden) return;
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); topBar.classList.add('searching'); q.focus(); }
    else if (!typing && e.key === '/') { e.preventDefault(); topBar.classList.add('searching'); q.focus(); }
    else if (!typing && e.key === '\\') $('#toggleNav').click();
    else if (e.key === 'Escape') { app.classList.remove('nav-open'); $('#scrim').classList.remove('show'); }
  });

  /* ---------- login ---------- */
  const login = $('#login');
  function showLogin() {
    login.hidden = false;
    app.setAttribute('aria-hidden', 'true');
    clearInterval(badgeTimer);
    setTimeout(() => $('#lu').focus(), 30);
  }
  $('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#loginBtn'), err = $('#loginErr');
    err.textContent = ''; btn.classList.add('busy'); btn.disabled = true;
    try {
      await Admin.api.post('/login', { username: $('#lu').value, password: $('#lp').value });
      $('#lp').value = '';
      await start();
    } catch (ex) {
      err.textContent = ex.message;
    } finally { btn.classList.remove('busy'); btn.disabled = false; }
  });

  /* ---------- boot ---------- */
  async function start() {
    let s;
    try { s = await Admin.api.get('/session'); } catch (e) { s = { signedIn: false }; }
    if (!s.signedIn) return showLogin();
    csrf = s.csrf;
    $('#who').textContent = s.username || 'Admin';
    $('#whoInitial').textContent = (s.username || 'A').slice(0, 1).toUpperCase();
    login.hidden = true; app.removeAttribute('aria-hidden');
    renderNav();
    Admin.refreshBadges();
    clearInterval(badgeTimer);
    badgeTimer = setInterval(() => { if (!document.hidden) Admin.refreshBadges(); }, 60000);
    render();
  }
  Admin.start = start;
})();
