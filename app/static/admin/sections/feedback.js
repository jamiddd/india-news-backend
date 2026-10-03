/* Feedback inbox: the website form and the app's support screens. API: app/feedback_admin.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const TABS = [['new', 'New'], ['read', 'Read'], ['closed', 'Closed'], ['all', 'All']];
  const STATUS_HUE = { new: '--blue', read: '--muted', closed: '--green' };
  const PAGE = 25;

  const who = (f) => {
    if (f.name && f.email) return `${f.name} <${f.email}>`;
    return f.name || f.email || (f.userId ? 'Signed-in reader' : 'Anonymous');
  };
  const replyNote = (f) => f.wantsReply ? pill('Wants a reply', '--blue', true)
    : f.emailFromAccount ? pill('Email from account', '--muted')
    : f.name && !f.email ? pill('No email, cannot reply', '--muted') : '';

  const listItem = (f, sel) => `<button class="msg ${f.status === 'new' ? 'unread' : ''} ${sel ? 'sel' : ''}" data-act="open" data-id="${f.id}">
      <span class="l1"><span>#${f.id} · ${esc(f.categoryLabel)}</span><span>${esc(fmt.rel(f.createdAt))}</span></span>
      <b>${esc(who(f))}</b><p>${esc(f.message)}</p></button>`;

  const detail = (f) => !f ? ui.empty('Pick a message to read it.') : `
      <button class="btn btn-ghost btn-sm back-btn" data-act="back">← All messages</button>
      <div style="display:flex;gap:8px;flex-wrap:wrap">${pill(f.categoryLabel, '--blue')}${pill(f.status, STATUS_HUE[f.status])}${pill(f.source, '--muted')}${replyNote(f)}</div>
      <h2>${esc(who(f))}</h2>
      <div class="quote prose">${esc(f.message)}</div>
      <dl class="kv">
        <dt>Message</dt><dd>#${f.id}</dd>
        <dt>Received</dt><dd>${esc(fmt.dateTime(f.createdAt))} IST</dd>
        ${f.email ? `<dt>Email</dt><dd>${esc(f.email)} <button class="btn btn-ghost btn-sm" style="margin-left:6px" data-act="copy" data-id="${esc(f.email)}">Copy</button></dd>` : ''}
        ${f.userId ? `<dt>User ID</dt><dd>${esc(f.userId)} <a class="link" style="margin-left:6px" href="${Admin.href('users?q=' + encodeURIComponent(f.userId))}">Open user</a></dd>` : ''}
      </dl>
      <div class="form-acts" style="margin-top:auto">
        ${f.status !== 'read' ? ui.btn(f.status === 'new' ? 'Mark read' : 'Move to read', { act: 'status', id: f.id + ':read', kind: f.status === 'new' ? 'ink' : 'ghost', ic: 'check' }) : ''}
        ${f.status !== 'closed' ? ui.btn('Close', { act: 'status', id: f.id + ':closed', kind: 'ghost', ic: 'archive' }) : ''}
        ${f.status !== 'new' ? ui.btn('Reopen', { act: 'status', id: f.id + ':new', kind: 'line', ic: 'restore' }) : ''}
      </div>`;

  Admin.page({
    path: 'feedback',
    nav: 'feedback',
    title: 'Feedback',
    load: async (p, q) => {
      const status = q.status || 'new';
      const list = await api.get('/feedback', { status, q: q.q, limit: PAGE });
      let open = list.items.find((i) => String(i.id) === q.open) || null;
      if (q.open && !open) open = await api.get('/feedback/' + encodeURIComponent(q.open)).catch(() => null);
      return { ...list, status, open };
    },
    render: (d, p, q) => {
      const total = d.counts.new + d.counts.read + d.counts.closed;
      const sel = d.open || (window.innerWidth > 680 ? d.items[0] : null);
      return ui.head('Feedback', d.counts.new ? `${fmt.plural(d.counts.new, 'unread message')}.` : 'Inbox zero.',
        'Messages from the website form and the app’s support screens, newest first. This is the only support inbox.')
        + ui.st(`<div class="panel-h" style="margin:0">
            <div class="chips">${TABS.map(([k, label]) => `<a class="chip ${d.status === k ? 'on' : ''}" style="display:inline-flex;align-items:center;text-decoration:none" href="${Admin.href('feedback' + (k === 'new' ? '' : '?status=' + k))}">${label} · <span class="num">&nbsp;${k === 'all' ? total : d.counts[k]}</span></a>`).join('')}</div>
            <form id="fbSearch" style="display:flex;gap:8px;flex:1;max-width:340px"><input class="inp" id="fbq" type="search" style="height:38px" placeholder="Search message, email or user ID" value="${esc(q.q || '')}"></form>
          </div>`)
        + ui.st(`<div class="inbox ${d.open ? 'reading' : ''}" id="inbox">
            <div class="list-pane" id="fbList">${d.items.length ? d.items.map((f) => listItem(f, sel && f.id === sel.id)).join('') : ui.empty(q.q ? `Nothing matches “${esc(q.q)}”.` : `Nothing ${esc(d.status === 'all' ? 'here' : d.status)}.`)}
              ${d.total > d.items.length ? `<button class="btn btn-ghost" data-act="more" style="margin:8px">Load older · ${fmt.num(d.total - d.items.length)} more</button>` : ''}</div>
            <div class="detail" id="fbDetail">${detail(sel)}</div>
          </div>`);
    },
    mount: (ctx) => {
      const d = ctx.data, items = d.items.slice();
      let current = d.open || (window.innerWidth > 680 ? items[0] : null);
      const inbox = ctx.root.querySelector('#inbox'), list = ctx.root.querySelector('#fbList'), pane = ctx.root.querySelector('#fbDetail');
      const show = (f) => {
        current = f;
        pane.innerHTML = detail(f);
        list.querySelectorAll('.msg').forEach((m) => m.classList.toggle('sel', f && m.dataset.id === String(f.id)));
        inbox.classList.toggle('reading', !!f);
        Admin.setQuery({ open: f ? f.id : null });
      };
      ctx.root.querySelector('#fbSearch').addEventListener('submit', (e) => {
        e.preventDefault();
        const v = ctx.root.querySelector('#fbq').value.trim();
        Admin.setQuery({ q: v || null, open: null });
        ctx.reload();
      });
      ctx.actions({
        open: async (el) => {
          const f = items.find((i) => String(i.id) === el.dataset.id);
          show(f);
          if (f && f.status === 'new') {
            const updated = await api.post('/feedback/' + f.id, { status: 'read' });
            Object.assign(f, updated);
            el.classList.remove('unread');
            pane.innerHTML = detail(f);
            Admin.refreshBadges();
          }
        },
        back: () => show(null),
        copy: (el) => Admin.copy(el.dataset.id, 'Email copied'),
        status: async (el) => {
          const [id, status] = el.dataset.id.split(':');
          await api.post('/feedback/' + id, { status });
          Admin.toast(status === 'closed' ? 'Message closed' : status === 'new' ? 'Message reopened' : 'Marked as read', '--blue');
          Admin.refreshBadges();
          Admin.setQuery({ open: null });
          ctx.reload();
        },
        more: async (el) => {
          const r = await api.get('/feedback', { status: d.status, q: ctx.query.q, offset: items.length, limit: PAGE });
          items.push(...r.items);
          el.insertAdjacentHTML('beforebegin', r.items.map((f) => listItem(f, false)).join(''));
          if (items.length >= r.total) el.remove();
          else el.textContent = `Load older · ${fmt.num(r.total - items.length)} more`;
        },
      });
    },
  });
})();
