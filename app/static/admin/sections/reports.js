/* Story reports: reader flags from the share sheet. API: app/story_reports_admin.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const REASON_HUE = { misleading: '--red', factually_incorrect: '--orange', offensive: '--purple', duplicate_spam: '--blue', other: '--muted' };
  const STATUS_HUE = { open: '--orange', reviewed: '--green', dismissed: '--muted' };
  const TABS = [['open', 'Open'], ['reviewed', 'Reviewed'], ['dismissed', 'Dismissed'], ['all', 'All']];

  Admin.page({
    path: 'reports',
    nav: 'reports',
    title: 'Story reports',
    load: (p, q) => api.get('/reports', { status: q.status || 'open' }),
    render: (d, p, q) => {
      const status = q.status || 'open';
      const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
      return ui.head('Story reports', d.counts.open ? `${fmt.plural(d.counts.open, 'open report')}.` : 'No open reports.',
        'Readers flag stories, timelines and explainers from the share sheet. Mark a report reviewed once the content is fixed or hidden, or dismiss it.')
        + ui.st(`<div class="panel" style="display:flex;flex-direction:column;gap:14px">
          <div class="chips">${TABS.map(([k, label]) => `<a class="chip ${status === k ? 'on' : ''}" href="${Admin.href('reports' + (k === 'open' ? '' : '?status=' + k))}" style="display:inline-flex;align-items:center;text-decoration:none">${label} · <span class="num">&nbsp;${k === 'all' ? total : d.counts[k] || 0}</span></a>`).join('')}</div>
          <div id="reportsTable"></div></div>`);
    },
    mount: (ctx) => {
      const status = ctx.query.status || 'open';
      const decide = async (ids, action) => {
        if (ids.length === 1) await api.post(`/reports/${ids[0]}`, { action });
        else await api.post('/reports/bulk', { ids: ids.map(Number), action });
        Admin.toast(`${fmt.plural(ids.length, 'report')} ${action === 'open' ? 'reopened' : action}`, action === 'reviewed' ? '--green' : '--orange');
        Admin.refreshBadges();
        ctx.reload();
      };
      Admin.table(ctx.root.querySelector('#reportsTable'), {
        key: 'reports-' + status,
        rows: ctx.data.items,
        order: [[3, 'desc']],
        placeholder: 'Search headline, reason or note',
        empty: status === 'open' ? 'No open reports. New ones appear here as readers send them.' : 'Nothing here.',
        select: status === 'open' ? {
          id: (r) => r.id,
          actions: [
            { label: 'Mark reviewed', primary: true, run: (ids) => decide(ids, 'reviewed') },
            { label: 'Dismiss', run: (ids) => decide(ids, 'dismissed') },
          ],
        } : null,
        columns: [
          {
            title: 'Reported', lead: true, data: (r) => `${r.target.title} ${r.note || ''}`,
            render: (r) => `<b>${r.target.url ? `<a class="link" style="color:inherit" href="${esc(Admin.publicUrl(r.target.url))}" target="_blank" rel="noopener">${esc(r.target.title)}</a>` : esc(r.target.title)}</b>${r.note ? `<small>“${esc(r.note)}”</small>` : ''}<small>${esc(r.target.kind)} · by ${esc(r.userId)}</small>`,
          },
          { title: 'Reason', data: 'reasonLabel', render: (r) => pill(r.reasonLabel, REASON_HUE[r.reason] || '--muted') },
          { title: 'Status', data: 'status', render: (r) => pill(r.status, STATUS_HUE[r.status]) },
          { title: 'When', data: 'createdAt', render: (r) => `<span title="${esc(fmt.dateTime(r.createdAt))}">${esc(fmt.rel(r.createdAt))}</span>` },
          {
            title: '', orderable: false, searchable: false, className: 'r',
            render: (r) => r.status === 'open'
              ? `<div class="acts" style="display:flex;gap:6px;justify-content:flex-end">${ui.btn('Reviewed', { act: 'decide', id: r.id + ':reviewed', size: 'sm' })}${ui.btn('Dismiss', { act: 'decide', id: r.id + ':dismissed', size: 'sm', kind: 'line' })}</div>`
              : ui.btn('Reopen', { act: 'decide', id: r.id + ':open', size: 'sm', kind: 'line' }),
          },
        ],
      });
      ctx.actions({
        decide: (el) => { const [id, action] = el.dataset.id.split(':'); return decide([id], action); },
      });
    },
  });
})();
