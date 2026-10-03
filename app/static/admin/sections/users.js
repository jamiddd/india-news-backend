/* Users: read-only account lookup for support and abuse triage. API: app/admin_users.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const TRIAL = { active: ['Trial active', '--teal'], ended: ['Trial ended', '--muted'], converted: ['Bought Premium', '--purple'] };
  const initials = (n) => String(n || '?').trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join('').toUpperCase();

  Admin.page({
    path: 'users',
    nav: 'users',
    title: 'Users',
    load: () => api.get('/users/stats'),
    render: (s) => {
      const providers = Object.entries(s.providers || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} ${fmt.num(v)}`).join(' · ');
      return ui.head('Users', `${fmt.plural(s.total, 'account')}.`, 'Look up an account by email, name or user ID. This page is read-only; readers delete their own accounts from the app.')
        + ui.st(`<div class="grid g-4">
            <div class="stat"><span class="mono">Accounts</span><b>${fmt.num(s.total)}</b><small>${providers || '—'}</small></div>
            <div class="stat"><span class="mono">Joined this week</span><b>${fmt.num(s.joinedThisWeek)}</b><small>Last 7 days</small></div>
            <div class="stat"><span class="mono">Trials running</span><b>${fmt.num(s.trialsActive)}</b><small>7-day Premium trial</small></div>
            <div class="stat"><span class="mono">Trials converted</span><b>${fmt.num(s.trialsConverted)}</b><small>Verified purchase after trial</small></div>
          </div>`)
        + ui.st(ui.panel('', `<div class="panel-h"><div><span class="mono">New accounts per day · last 30 days (UTC)</span></div></div><div class="chart" id="signupChart"></div>`))
        + ui.st(ui.panel('', '<div id="usersTable"></div>'));
    },
    mount: (ctx) => {
      Admin.chart.bars(ctx.root.querySelector('#signupChart'), ctx.data.signups.map((d) => ({ x: d.day, y: d.count })), {
        hue: '--blue', label: 'New accounts per day', fmtX: (d) => fmt.day(d + 'T12:00:00Z').replace(/^\w+,?\s*/, ''), fmtY: (v) => fmt.num(Math.round(v)),
      });
      Admin.table(ctx.root.querySelector('#usersTable'), {
        key: 'users',
        server: (p) => api.get('/users', { start: p.start, length: p.length, q: p.search, orderBy: p.orderBy, dir: p.dir }),
        search: ctx.query.q || '',
        order: [[3, 'desc']],
        pageLength: 25,
        placeholder: 'Email, name or user ID',
        empty: 'No accounts yet.',
        columns: [
          {
            title: 'Account', data: 'displayName', lead: true,
            render: (u) => `<div style="display:flex;gap:12px;align-items:center;min-width:0">${u.photoUrl ? `<img class="av" src="${esc(u.photoUrl)}" alt="" referrerpolicy="no-referrer" style="object-fit:cover">` : `<span class="av">${esc(initials(u.displayName || u.email))}</span>`}<div style="min-width:0"><b>${esc(u.displayName || '—')}</b><small>${esc(u.email)}</small></div></div>`,
          },
          { title: 'Sign-in', data: 'provider', render: (u) => pill(u.provider, '--muted') },
          { title: 'Premium trial', orderable: false, render: (u) => u.trial ? pill(...TRIAL[u.trial]) : '<span class="muted">—</span>' },
          { title: 'Joined', data: 'createdAt', render: (u) => `<span title="${esc(fmt.dateTime(u.createdAt))}">${esc(fmt.date(u.createdAt))}</span>` },
          { title: 'Saved', orderable: false, className: 'r num', render: (u) => fmt.num(u.saved) },
          { title: 'Donated', orderable: false, className: 'r num', render: (u) => u.donatedInr ? fmt.inr(u.donatedInr, 2) : '<span class="muted">—</span>' },
          { title: 'User ID', orderable: false, render: (u) => `<button class="btn btn-ghost btn-sm" data-act="copy" data-id="${esc(u.id)}" title="${esc(u.id)}">${esc(u.id.slice(0, 10))}… copy</button>` },
        ],
      });
      ctx.actions({ copy: (el) => Admin.copy(el.dataset.id, 'User ID copied') });
    },
  });
})();
