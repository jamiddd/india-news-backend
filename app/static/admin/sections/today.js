/* Today: what needs a decision now. API: GET /admin/api/overview (+ /overview/activity) in app/admin_api.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, icon, pill } = ui;

  const taskState = (name, t) => {
    if (t.waiting) return { pill: pill('Needs review', '--red', true), urgent: true };
    if (!t.exists) return { pill: pill('Missing', '--red'), urgent: true };
    if (t.status === 'rejected') return { pill: pill(name === 'Poll' ? 'Rejected · fallback at 09:00' : 'Rejected · curated set', '--muted'), urgent: false };
    return { pill: pill('Done', '--green'), urgent: false };
  };
  const row = (route, hue, ic, title, sub, right) =>
    `<a class="row" href="${Admin.href(route)}" style="text-decoration:none"><span class="ico-sq" style="--c:var(${hue})${hue === '--amber' ? ';color:var(--on-amber)' : ''}">${icon(ic)}</span><div class="body"><b>${title}</b><small>${sub}</small></div>${right}</a>`;
  const tile = (route, hue, n, label, sub) =>
    `<a class="kpi${hue === '--amber' ? ' amber' : ''}" href="${Admin.href(route)}" style="--c:var(${hue})"><span class="mono">${label}</span><span class="arrow">${icon('arrow')}</span><div><div class="big">${fmt.num(n)}</div><div class="sub">${sub}</div></div></a>`;

  Admin.page({
    path: '',
    nav: 'today',
    title: 'Today',
    load: async () => {
      const [o, a] = await Promise.all([api.get('/overview'), api.get('/overview/activity').catch(() => null)]);
      Admin.overview = o;
      return { o, a };
    },
    render: ({ o, a }) => {
      const b = o.badges, t = o.tasks;
      const drafts = (b.polls || 0) + (b.quiz || 0);
      const poll = taskState('Poll', t.poll), quiz = taskState('Quiz', t.quiz);
      const draftRoute = b.polls ? 'polls' : 'quiz';
      const title = drafts ? `${fmt.plural(drafts, 'draft')} need${drafts === 1 ? 's' : ''} you before 09:00.`
        : (!t.poll.exists || !t.quiz.exists) ? 'A daily draft is missing.' : 'Nothing is waiting on you.';
      const last = a && a.days.length ? a.days[a.days.length - 1] : null;
      const prevWeek = a ? a.days.slice(0, 7).reduce((s, d) => s + d.readers, 0) : 0;
      const thisWeek = a ? a.days.slice(7).reduce((s, d) => s + d.readers, 0) : 0;
      const change = prevWeek ? Math.round(((thisWeek - prevWeek) / prevWeek) * 1000) / 10 : null;

      return ui.head(`Daily review · ${fmt.day(o.now)}`, title, 'Poll and quiz drafts expire at 09:00 IST. If nobody approves them, a fallback goes live.')
        + ui.st(`<div class="grid g-4">
            ${tile(draftRoute, '--green', drafts, 'Drafts to review', 'Poll and quiz for today')}
            ${tile('breaking', '--red', b.breaking || 0, 'Breaking decisions', 'Candidates and refreshes')}
            ${tile('reports', '--orange', b.reports || 0, 'Story reports', o.latestReportAt ? `Open · newest ${esc(fmt.rel(o.latestReportAt))}` : 'Open reports')}
            ${tile('feedback', '--blue', b.feedback || 0, 'Unread feedback', 'From the website and the app')}
          </div>`)
        + ui.st(`<div class="grid g-main">
            <div class="panel"><div class="panel-h"><div><span class="mono">Signed-in readers per day · 14 days (UTC)</span>
              <h2 style="margin-top:4px"><span class="num">${last ? fmt.num(last.readers) : '—'}</span> today${change != null ? ` <span style="color:var(${change >= 0 ? '--green' : '--red'});font-size:14px">${change >= 0 ? '▲' : '▼'} ${Math.abs(change)}% vs prior week</span>` : ''}</h2></div></div>
              <div class="chart" id="readersChart">${a ? '' : ui.empty('Reader activity is unavailable right now.')}</div>
              <p class="note" style="margin-top:10px">Counts readers who were signed in when they opened a story. Signed-out reading isn’t tracked.</p></div>
            <div class="panel"><div class="panel-h"><h2>Queue</h2><span class="mono">Due today</span></div><div class="list">
              ${row('polls', '--green', 'polls', 'Poll of the Day', esc(t.poll.summary || ''), poll.pill)}
              ${row('quiz', '--green', 'quiz', 'Daily Quiz', esc(t.quiz.summary || ''), quiz.pill)}
              ${row('explainers', '--purple', 'explainers', 'Explainers', b.explainers ? `${fmt.plural(b.explainers, 'explainer')} ready for review` : 'Nothing waiting for review', b.explainers ? pill('Review', '--purple', true) : pill('Clear', '--green'))}
              ${row('daily-brief', '--amber', 'brief', 'Daily Brief', 'Morning Brief and Late-Night Wrap-up', pill('Open', '--muted'))}
            </div></div>
          </div>`);
    },
    mount: (ctx) => {
      const a = ctx.data.a;
      if (a) Admin.chart.line(ctx.root.querySelector('#readersChart'), a.days.map((d) => ({ x: d.day, y: d.readers })), {
        hue: '--blue', label: 'Signed-in readers per day', fmtY: (v) => fmt.num(Math.round(v)),
        fmtX: (d) => new Date(d + 'T12:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }),
      });
      // Keep the counts honest if the page is left open through the morning.
      ctx.every(120000, () => ctx.reload());
    },
  });
})();
