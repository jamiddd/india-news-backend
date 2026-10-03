/* Hot topics: admin-pushed daily tabs left of "For You" in the app (not reader-followed topics). API: app/admin_topics.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;

  const field = (label, input, hint = '') => `<div class="field"><label>${label}</label>${input}${hint ? `<span class="hint">${hint}</span>` : ''}</div>`;

  Admin.page({
    path: 'topics',
    nav: 'topics',
    title: 'Hot topics',
    load: () => api.get('/topics'),
    render: (d) => {
      const today = d.items.filter((t) => t.date === d.today);
      const byDate = {};
      d.items.forEach((t) => (byDate[t.date] = byDate[t.date] || []).push(t));
      const groups = Object.keys(byDate).sort().map((date) => ui.panel(
        `${esc(fmt.day(date + 'T12:00:00+05:30'))}${date === d.today ? ' · today' : ''}`,
        `<div class="list">${byDate[date].map((t) => `<div class="row">
            <span class="ico-sq mono" style="--c:var(--orange);font-weight:700" title="Order">${esc(t.order)}</span>
            <div class="body"><b>${esc(t.word)}</b><small>Order ${esc(t.order)} · lower shows first</small></div>
            <div class="acts">${ui.btn('Delete', { act: 'del', id: t.id, size: 'sm', kind: 'line', ic: 'trash' })}</div></div>`).join('')}</div>`,
        date === d.today ? pill('Live in the app', '--orange', true) : pill(fmt.plural(byDate[date].length, 'tab'))));
      return ui.head('Hot topics', today.length ? `${fmt.plural(today.length, 'tab')} live today.` : 'No hot topic today.',
        `Each word becomes its own tab to the left of “For You” in the app, for that date only (India calendar). It expires the next day, nothing to clean up. Today is ${esc(fmt.date(d.today + 'T12:00:00+05:30'))}.`)
        + ui.st(`<div class="grid g-main">
          <div style="display:flex;flex-direction:column;gap:16px">${groups.length ? groups.join('') : ui.panel('', ui.empty('No hot topics scheduled from today onward.'))}</div>
          ${ui.panel('Add a tab', `<form class="form" id="topicForm" autocomplete="off">
            ${field('Word', '<input class="inp" name="word" maxlength="60" required placeholder="e.g. Elections">', 'Shown as the tab label and used as its search query')}
            <div class="form-row">
              ${field('Date', `<input class="inp" type="date" name="date" required value="${esc(d.today)}" min="${esc(d.today)}">`)}
              ${field('Order', '<input class="inp" type="number" name="order" value="0" step="1">', 'Lower shows first')}
            </div>
            <div class="form-acts">${ui.btn('Add tab', { act: 'add', ic: 'plus', attrs: 'type="submit"' })}</div></form>`)}
        </div>`);
    },
    mount: (ctx) => {
      const form = ctx.root.querySelector('#topicForm');
      // Enter in a field fires a click on the submit button, which ctx.actions handles; never navigate.
      form.addEventListener('submit', (e) => e.preventDefault());
      ctx.actions({
        add: async () => {
          if (!form.reportValidity()) return;
          const v = Object.fromEntries(new FormData(form));
          const order = Number(v.order || 0);
          if (!Number.isInteger(order)) throw new Error('Order must be a whole number.');
          if (v.date === ctx.data.today) {
            const ok = await Admin.confirm({
              title: 'Add this tab to the app now?',
              text: `<b>${esc(v.word.trim())}</b> appears as a tab for every reader today.`,
              ok: 'Add it now',
            });
            if (!ok) return;
          }
          await api.post('/topics', { word: v.word, date: v.date, order });
          Admin.toast(v.date === ctx.data.today ? `“${v.word.trim()}” is live in the app` : `“${v.word.trim()}” scheduled for ${fmt.day(v.date + 'T12:00:00+05:30')}`);
          ctx.reload();
        },
        del: async (el) => {
          const t = ctx.data.items.find((x) => String(x.id) === el.dataset.id);
          const live = t && t.date === ctx.data.today;
          const ok = await Admin.confirm({
            title: `Delete “${t ? t.word : 'this topic'}”?`,
            text: live ? 'The tab disappears from the app right away.' : 'It will not be shown.',
            ok: 'Delete', danger: true,
          });
          if (!ok) return;
          await api.del(`/topics/${el.dataset.id}`);
          Admin.toast('Topic deleted', '--muted');
          ctx.reload();
        },
      });
    },
  });
})();
