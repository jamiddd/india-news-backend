/* Announcements: the scheduled banner at the top of the app. API: app/admin_announcements.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const KIND_HUE = { event: '--pink', offer: '--green', info: '--blue' };
  const STATUS_HUE = { active: '--pink', upcoming: '--blue', expired: '--muted' };
  const ACTION_HINT = { url: 'Full https:// URL to open', story: 'Story cluster id, e.g. 46759', paywall: 'Leave empty: opens the Premium paywall' };

  /* "YYYY-MM-DDTHH:MM" in IST for a datetime-local input, offset from now by `hours`. */
  const istLocal = (hours = 0) => new Date(Date.now() + (5.5 + hours) * 3600e3).toISOString().slice(0, 16);

  const field = (label, input, hint = '') => `<div class="field"><label>${label}</label>${input}${hint ? `<span class="hint">${hint}</span>` : ''}</div>`;

  Admin.page({
    path: 'announcements',
    nav: 'announcements',
    title: 'Announcements',
    load: () => api.get('/announcements'),
    render: (d) => {
      const live = d.items.filter((i) => i.status === 'active').length;
      const upcoming = d.items.length - live;
      const title = live || upcoming
        ? `${fmt.num(live)} live${upcoming ? `, ${fmt.num(upcoming)} scheduled` : ''}.`
        : 'No banner scheduled.';
      return ui.head('Announcements', title,
        'Events, offers and extra info at the top of the app, for a set window, without a release. Several can be live at once: the app shows one at a time, highest priority first, and queues the rest. Each one expires at its end time, nothing to clean up.')
        + ui.st(ui.panel('New announcement', `<form class="form" id="annForm" autocomplete="off">
            <div class="form-row">
              ${field('Kind', `<select class="inp" name="kind">${d.kinds.map((k) => `<option value="${esc(k)}"${k === 'info' ? ' selected' : ''}>${esc(k)}</option>`).join('')}</select>`)}
              ${field('Priority', '<input class="inp" type="number" name="priority" value="0" step="1">', 'Higher shows first')}
            </div>
            ${field('Title', '<input class="inp" name="title" maxlength="120" required placeholder="e.g. Election night live coverage">', 'Up to 120 characters')}
            ${field('Body', '<textarea class="inp" name="body" maxlength="240" placeholder="Optional one or two lines"></textarea>', 'Optional, up to 240 characters')}
            <div class="form-row">
              ${field('Button label', '<input class="inp" name="ctaLabel" maxlength="40" placeholder="e.g. Read more">', 'Optional, up to 40 characters')}
              ${field('Button action', `<select class="inp" name="actionType"><option value="">None</option>${d.actionTypes.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join('')}</select>`)}
              ${field('Action value', '<input class="inp" name="actionValue" maxlength="500">', '<span id="annActionHint">URL or story cluster id</span>')}
            </div>
            <div class="form-row">
              ${field('Starts (IST)', `<input class="inp" type="datetime-local" name="startsAt" required value="${istLocal(0)}">`)}
              ${field('Ends (IST)', `<input class="inp" type="datetime-local" name="endsAt" required value="${istLocal(24)}">`)}
            </div>
            <div class="form-acts">${ui.btn('Schedule announcement', { act: 'add', ic: 'plus', attrs: 'type="submit"' })}</div>
          </form>`))
        + ui.st(ui.panel(`Live and scheduled (${d.items.length})`, '<div id="annTable"></div>'));
    },
    mount: (ctx) => {
      const form = ctx.root.querySelector('#annForm');
      // Enter in a field fires a click on the submit button, which ctx.actions handles; never navigate.
      form.addEventListener('submit', (e) => e.preventDefault());
      form.elements.actionType.addEventListener('change', () => {
        ctx.root.querySelector('#annActionHint').textContent = ACTION_HINT[form.elements.actionType.value] || 'URL or story cluster id';
      });

      Admin.table(ctx.root.querySelector('#annTable'), {
        key: 'announcements',
        rows: ctx.data.items,
        order: [[2, 'asc']],
        placeholder: 'Search title or body',
        empty: 'Nothing scheduled from now onward.',
        columns: [
          {
            title: 'Announcement', lead: true, data: (r) => `${r.title} ${r.body || ''}`,
            render: (r) => `<b>${esc(r.title)}</b>${r.body ? `<small>${esc(r.body)}</small>` : ''}${r.ctaLabel || r.actionType
              ? `<small>Button: ${esc(r.ctaLabel || '(no label)')}${r.actionType ? ` → ${esc(r.actionType)}${r.actionValue ? ' ' + esc(r.actionValue) : ''}` : ''}</small>` : ''}`,
          },
          { title: 'Kind', data: 'kind', render: (r) => pill(r.kind, KIND_HUE[r.kind] || '--muted') },
          { title: 'Starts', data: 'startsAt', render: (r) => esc(fmt.dateTime(r.startsAt)) },
          { title: 'Ends', data: 'endsAt', render: (r) => `<span title="${esc(fmt.rel(r.endsAt))}">${esc(fmt.dateTime(r.endsAt))}</span>` },
          { title: 'Priority', data: 'priority', className: 'num' },
          { title: 'Status', data: 'status', render: (r) => pill(r.status === 'active' ? 'live' : r.status, STATUS_HUE[r.status], r.status === 'active') },
          {
            title: '', orderable: false, searchable: false, className: 'r',
            render: (r) => ui.btn('Delete', { act: 'del', id: r.id, size: 'sm', kind: 'line', ic: 'trash' }),
          },
        ],
      });

      ctx.actions({
        add: async () => {
          if (!form.reportValidity()) return;
          const v = Object.fromEntries(new FormData(form));
          const body = { ...v, priority: Number(v.priority || 0) };
          if (!Number.isInteger(body.priority)) throw new Error('Priority must be a whole number.');
          const startsNow = v.startsAt <= istLocal(0);
          const ok = await Admin.confirm({
            title: startsNow ? 'Show this banner now?' : 'Schedule this banner?',
            text: `<b>${esc(v.title)}</b> shows at the top of the app for every reader from ${esc(v.startsAt.replace('T', ' '))} to ${esc(v.endsAt.replace('T', ' '))} IST.`,
            ok: startsNow ? 'Show it now' : 'Schedule it',
          });
          if (!ok) return;
          const r = await api.post('/announcements', body);
          Admin.toast(r.item.status === 'active' ? 'Announcement is live in the app' : 'Announcement scheduled');
          ctx.reload();
        },
        del: async (el) => {
          const row = ctx.data.items.find((i) => String(i.id) === el.dataset.id);
          const ok = await Admin.confirm({
            title: 'Delete this announcement?',
            text: `<b>${esc(row ? row.title : 'It')}</b> ${row && row.status === 'active' ? 'disappears from the app right away' : 'will not be shown'}. This can't be undone.`,
            ok: 'Delete', danger: true,
          });
          if (!ok) return;
          await api.del(`/announcements/${el.dataset.id}`);
          Admin.toast('Announcement deleted', '--muted');
          ctx.reload();
        },
      });
    },
  });
})();
