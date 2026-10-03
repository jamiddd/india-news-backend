/* Donations: read-only demand signal. API: app/admin_donations.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const monthLabel = (m) => new Date(m + '-15T00:00:00Z').toLocaleDateString('en-IN', { month: 'short', timeZone: 'UTC' });
  const monthLong = (m) => new Date(m + '-15T00:00:00Z').toLocaleDateString('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' });

  Admin.page({
    path: 'donations',
    nav: 'donations',
    title: 'Donations',
    load: () => api.get('/donations'),
    render: (d) => {
      const thisMonth = d.months[d.months.length - 1];
      const best = d.months.reduce((a, m) => (m.inr > a.inr ? m : a), d.months[0]);
      return ui.head('Donations', `${fmt.inr(d.allTime.inr)} from ${fmt.plural(d.allTime.count, 'donation')}.`,
        'Captured payments only, as recorded by the payment webhook. Read-only: this page tracks whether the demand signal is moving.')
        + ui.st(`<div class="grid g-4">
            <div class="kpi" style="--c:var(--pink)"><span class="mono">Last 30 days</span><div><div class="big">${fmt.inr(d.last30Days.inr)}</div><div class="sub">${fmt.plural(d.last30Days.count, 'donation')}</div></div></div>
            <div class="stat"><span class="mono">This month</span><b>${fmt.inr(thisMonth.inr)}</b><small>${esc(monthLong(thisMonth.month))}, so far</small></div>
            <div class="stat"><span class="mono">Best month</span><b>${fmt.inr(best.inr)}</b><small>${best.inr ? esc(monthLong(best.month)) : 'No donations yet'}</small></div>
            <div class="stat"><span class="mono">Signed-in donors</span><b>${fmt.num(d.allTime.donors)}</b><small>Distinct accounts, all time</small></div>
          </div>`)
        + ui.st(ui.panel('', `<div class="panel-h"><div><span class="mono">Captured per month · IST</span><h2 style="margin-top:4px">Last 12 months</h2></div><small class="muted">The current month is still running</small></div><div class="chart" id="donChart"></div>`))
        + ui.st(ui.panel('Recent payments', '<div id="donTable"></div>', `<span class="mono">Latest ${fmt.num(d.items.length)}</span>`));
    },
    mount: (ctx) => {
      const months = ctx.data.months;
      Admin.chart.bars(ctx.root.querySelector('#donChart'), months.map((m) => ({ x: m.month, y: m.inr })), {
        hue: '--pink', label: 'Donations captured per month', fmtX: monthLabel, fmtY: (v) => fmt.inr(v), faded: (p, i) => i === months.length - 1,
      });
      Admin.table(ctx.root.querySelector('#donTable'), {
        key: 'donations',
        rows: ctx.data.items,
        order: [[0, 'desc']],
        placeholder: 'Search donor, email or provider',
        empty: 'No donations yet.',
        columns: [
          { title: 'When', data: 'createdAt', render: (r) => `<span title="${esc(fmt.dateTime(r.createdAt))} IST">${esc(fmt.dateTime(r.createdAt))}</span>` },
          {
            title: 'Donor', lead: true, data: (r) => `${r.donorName || ''} ${r.donorEmail || ''} ${r.userId || ''}`,
            render: (r) => r.donorName ? `<b>${esc(r.donorName)}</b><small>${esc(r.donorEmail || '')}</small>` : `<span class="muted">${r.userId ? 'User ' + esc(r.userId) : 'Anonymous or signed out'}</span>`,
          },
          { title: 'Provider', data: 'provider', render: (r) => pill(r.provider, '--muted') },
          { title: 'Status', data: 'status', render: (r) => pill(r.status, r.status === 'captured' ? '--green' : '--red') },
          { title: 'Amount', data: 'inr', className: 'r num', render: (r) => `<b>${fmt.inr(r.inr, 2)}</b>` },
        ],
      });
    },
  });
})();
