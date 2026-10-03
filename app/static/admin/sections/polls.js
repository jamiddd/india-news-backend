/* Poll of the Day (review today's AI draft) and the Poll bank (evergreen fallbacks).
 * APIs: app/poll_admin.py (/polls) and app/poll_bank_admin.py (/poll-bank). */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const MAX_OPTIONS = 4;
  const SLOW = 'Claude writes it, so this can take up to a minute.';

  const STATUS = {
    draft: ['Draft', '--amber'],
    scheduled: ['Approved', '--green'],
    active: ['Live', '--blue'],
    rejected: ['Rejected', '--red'],
    closed: ['Closed', '--muted'],
  };
  const statusPill = (s) => pill((STATUS[s] || [s])[0], (STATUS[s] || [])[1] || '--muted');

  const field = (label, inner, hint = '') => `<div class="field"><label>${label}</label>${inner}${hint ? `<span class="hint">${hint}</span>` : ''}</div>`;
  const textarea = (id, value, attrs = '') => `<textarea class="inp" id="${id}" ${attrs}>${esc(value || '')}</textarea>`;
  const input = (id, value, attrs = '') => `<input class="inp" id="${id}" value="${esc(value || '')}" ${attrs}>`;
  const errBox = (id) => `<div id="${id}" class="note" role="alert" hidden style="color:var(--red);font-weight:600"></div>`;
  const showErr = (root, id, msg) => { const el = root.querySelector('#' + id); if (!el) return; el.textContent = msg || ''; el.hidden = !msg; };
  const optionInputs = (prefix, options, required = 0) => `<div class="form-row">${Array.from({ length: MAX_OPTIONS }, (_, i) =>
    field(`Option ${i + 1}${i < required ? '' : ' <span class="muted">(optional)</span>'}`, input(`${prefix}${i}`, options[i], `maxlength="300" data-opt${i < required ? ' required' : ''}`))).join('')}</div>`;
  const readOptions = (root) => [...root.querySelectorAll('[data-opt]')].map((el) => el.value.trim()).filter(Boolean);

  /* ---------- Poll of the Day ---------- */

  function headline(p) {
    if (!p) return 'No draft yet';
    if (p.status === 'draft') return p.editable ? 'Draft waiting for review' : 'The draft missed its slot';
    if (p.status === 'scheduled') return 'Approved for 9:00 AM';
    if (p.status === 'active') return 'Live now';
    if (p.status === 'rejected') return 'Rejected. A bank poll runs instead';
    return 'Closed';
  }

  function details(p) {
    const source = p.sourceUrl
      ? `<a class="link" href="${esc(Admin.publicUrl(p.sourceUrl))}" target="_blank" rel="noopener">${esc(p.sourceHeadline || 'Story ' + p.sourceClusterId)}</a>`
      : esc(p.sourceHeadline || 'Evergreen fallback');
    return ui.panel('Details', `<dl class="kv">
      <dt>Status</dt><dd>${statusPill(p.status)}</dd>
      <dt>Date</dt><dd>${esc(fmt.day(p.date + 'T12:00:00+05:30'))}</dd>
      <dt>Publishes</dt><dd>${esc(fmt.dateTime(p.publishAt))} IST</dd>
      <dt>Closes</dt><dd>${esc(fmt.dateTime(p.closesAt))} IST</dd>
      <dt>Source</dt><dd>${source}</dd>
      <dt>Written by</dt><dd>${p.generationMethod === 'fallback' ? 'Poll bank' : 'Claude'}</dd>
      ${p.approvedAt ? `<dt>Approved</dt><dd>${esc(fmt.dateTime(p.approvedAt))}</dd>` : ''}
    </dl>`);
  }

  function editor(p) {
    return ui.panel('Review the draft', `<div class="form">
      ${field('Question', textarea('pq', p.question, 'rows="3" maxlength="400" required'))}
      ${field('Context', textarea('pc', p.context, 'rows="3" maxlength="800" required'), 'One neutral, factual sentence shown under the question.')}
      ${optionInputs('po', p.options, 2)}
      <span class="hint">Two to four options. Leave a slot blank to drop it.</span>
      ${errBox('pollErr')}
      <div class="form-acts">
        ${ui.btn('Approve for 9:00 AM', { act: 'approve', kind: 'hue', ic: 'check' })}
        ${ui.btn('Regenerate', { act: 'regenerate', kind: 'line', ic: 'refresh' })}
        ${ui.btn('Reject and use the bank', { act: 'reject', kind: 'line', ic: 'x' })}
      </div>
      <span class="hint">Regenerate replaces this draft and discards your edits. ${SLOW}</span>
    </div>`);
  }

  function readOnly(p) {
    return ui.panel('Poll', `<div style="display:flex;flex-direction:column;gap:12px">
      <b style="font-size:18px">${esc(p.question)}</b>
      <p class="note">${esc(p.context)}</p>
      <div style="display:flex;flex-direction:column;gap:6px">${p.options.map((o) => `<div class="opt">${esc(o)}</div>`).join('')}</div>
      <p class="note">This poll can no longer be edited.${p.status === 'rejected' ? ' At 9:00 AM the least recently used poll from the <a class="link" href="' + Admin.href('poll-bank') + '">poll bank</a> is published instead.' : ''}</p>
    </div>`);
  }

  Admin.page({
    path: 'polls',
    nav: 'polls',
    title: 'Poll of the Day',
    load: () => api.get('/polls'),
    render: (d) => {
      const p = d.poll;
      const head = ui.head('Poll of the Day', esc(headline(p)),
        'Claude drafts a neutral poll from the last day of corroborated stories. Approve it before 9:00 AM IST, or the day falls back to a poll from the bank.',
        `<a class="btn btn-line" href="${Admin.href('poll-bank')}">Poll bank</a>`);
      if (!p) {
        return head + ui.st(ui.panel('', `<div class="form" style="align-items:flex-start">
          <p class="note">No draft exists for today (${esc(fmt.day(d.today + 'T12:00:00+05:30'))}).</p>
          ${errBox('pollErr')}
          ${ui.btn('Generate draft', { act: 'generate', kind: 'hue', ic: 'sparkle' })}
          <span class="hint">${SLOW}</span>
        </div>`));
      }
      return head + ui.st(`<div class="grid g-main">${p.editable ? editor(p) : readOnly(p)}${details(p)}</div>`);
    },
    mount: (ctx) => {
      const { root } = ctx;
      const failing = (fn) => async () => {
        showErr(root, 'pollErr', '');
        try { await fn(); } catch (e) { showErr(root, 'pollErr', e.message); throw e; }
      };
      ctx.actions({
        generate: failing(async () => {
          await api.post('/polls/generate');
          Admin.toast('Draft generated');
          Admin.refreshBadges();
          ctx.reload();
        }),
        regenerate: async () => {
          const ok = await Admin.confirm({
            title: 'Regenerate the draft?',
            text: 'Claude writes a new poll and your edits are discarded. This can take up to a minute.',
            ok: 'Regenerate',
          });
          if (!ok) return;
          await failing(async () => {
            await api.post('/polls/regenerate');
            Admin.toast('New draft ready');
            ctx.reload();
          })();
        },
        approve: async () => {
          const body = {
            question: root.querySelector('#pq').value,
            context: root.querySelector('#pc').value,
            options: readOptions(root),
          };
          const ok = await Admin.confirm({
            title: 'Approve this poll?',
            text: `It goes live for every reader at 9:00 AM IST and can't be edited after this.<br><br><b>${esc(body.question)}</b>`,
            ok: 'Approve for 9:00 AM',
          });
          if (!ok) return;
          await failing(async () => {
            await api.post(`/polls/${ctx.data.poll.id}/approve`, body);
            Admin.toast('Poll approved for 9:00 AM');
            Admin.refreshBadges();
            ctx.reload();
          })();
        },
        reject: async () => {
          const ok = await Admin.confirm({
            title: 'Reject this draft?',
            text: 'The draft is discarded and at 9:00 AM the least recently used poll from the bank is published instead. You can\'t undo this.',
            ok: 'Reject and use the bank',
            danger: true,
          });
          if (!ok) return;
          await failing(async () => {
            await api.post(`/polls/${ctx.data.poll.id}/reject`);
            Admin.toast('Draft rejected. A bank poll runs at 9:00 AM', '--orange');
            Admin.refreshBadges();
            ctx.reload();
          })();
        },
      });
    },
  });

  /* ---------- Poll bank ---------- */

  const TABS = [['create', 'Add poll'], ['list', 'All polls']];

  function addForm(d) {
    return `<div class="grid g-2">
      ${ui.panel('Generate with Claude', `<div class="form">
        ${field('Category <span class="muted">(optional)</span>', input('gcat', '', `maxlength="${d.maxCategory}" placeholder="e.g. education, environment"`), 'Leave blank for any evergreen topic.')}
        ${errBox('genErr')}
        <div class="form-acts">${ui.btn('Generate draft', { act: 'generate', kind: 'hue', ic: 'sparkle' })}</div>
        <span class="hint">Fills the form for you to review. Nothing is saved until you add it. ${SLOW}</span>
      </div>`)}
      ${ui.panel('Add a poll', `<div class="form">
        ${field('Question', textarea('bq', '', 'rows="3" maxlength="400" required'))}
        ${field('Context', textarea('bc', '', 'rows="3" maxlength="800" required'), 'One neutral, factual sentence shown under the question.')}
        ${optionInputs('bo', [], 2)}
        ${field('Category <span class="muted">(optional)</span>', input('bcat', '', `maxlength="${d.maxCategory}" placeholder="e.g. education, environment"`))}
        ${errBox('addErr')}
        <div class="form-acts">${ui.btn('Add to bank', { act: 'add', ic: 'plus' })}</div>
      </div>`)}
    </div>`;
  }

  Admin.page({
    path: 'poll-bank',
    nav: 'poll-bank',
    title: 'Poll bank',
    load: () => api.get('/poll-bank'),
    render: (d, p, q) => {
      const tab = q.tab === 'list' ? 'list' : 'create';
      const warning = d.active === 0
        ? ui.st(ui.panel('', `<p class="note" style="color:var(--red);font-weight:600">No active polls. If no draft is approved by 9:00 AM the daily poll will fail to publish. Activate or add one.</p>`))
        : '';
      return ui.head('Poll bank', `${fmt.plural(d.total, 'poll')} · ${fmt.num(d.active)} active`,
        'The daily poll is drawn from here (least recently used first) when no AI draft is approved by 9:00 AM IST.',
        `<a class="btn btn-line" href="${Admin.href('polls')}">Today's poll</a>`)
        + warning
        + ui.st(`<div class="chips" style="margin-bottom:14px">${TABS.map(([k, label]) => `<a class="chip ${tab === k ? 'on' : ''}" href="${Admin.href('poll-bank' + (k === 'create' ? '' : '?tab=' + k))}" style="display:inline-flex;align-items:center;text-decoration:none"${tab === k ? ' aria-current="page"' : ''}>${label}${k === 'list' ? ` · <span class="num">&nbsp;${fmt.num(d.total)}</span>` : ''}</a>`).join('')}</div>`
          + (tab === 'create' ? addForm(d) : ui.panel('', '<div id="bankTable"></div>')));
    },
    mount: (ctx) => {
      const { root } = ctx;
      const tab = ctx.query.tab === 'list' ? 'list' : 'create';
      const val = (id) => root.querySelector('#' + id).value;

      if (tab === 'list') {
        Admin.table(root.querySelector('#bankTable'), {
          key: 'poll-bank',
          rows: ctx.data.items,
          order: [[5, 'desc']],
          placeholder: 'Search question, context, options or category',
          empty: 'No polls in the bank yet.',
          columns: [
            {
              title: 'Poll', lead: true, data: (r) => `${r.question} ${r.context} ${r.options.join(' ')}`,
              render: (r) => `<b>${esc(r.question)}</b><small>${esc(r.context)}</small><small>${r.options.map(esc).join(' · ')}</small>`,
            },
            { title: 'Category', data: (r) => r.category || '', render: (r) => (r.category ? pill(r.category, '--green') : '<span class="muted">—</span>') },
            { title: 'Status', data: (r) => (r.active ? 'Active' : 'Off'), render: (r) => pill(r.active ? 'Active' : 'Off', r.active ? '--green' : '--muted') },
            { title: 'Used', data: 'usedCount', className: 'num', render: (r) => fmt.num(r.usedCount) },
            { title: 'Last used', data: (r) => r.lastUsedAt || '', searchable: false, render: (r) => (r.lastUsedAt ? `<span title="${esc(fmt.dateTime(r.lastUsedAt))}">${esc(fmt.rel(r.lastUsedAt))}</span>` : '<span class="muted">Never</span>') },
            { title: 'Added', data: (r) => r.createdAt || '', searchable: false, render: (r) => esc(fmt.date(r.createdAt)) },
            {
              title: '', orderable: false, searchable: false, className: 'r',
              render: (r) => `<div class="acts" style="display:flex;gap:6px;justify-content:flex-end">${ui.btn(r.active ? 'Deactivate' : 'Activate', { act: 'toggle', id: r.id, size: 'sm', kind: r.active ? 'line' : 'ink' })}${ui.btn('Delete', { act: 'delete', id: r.id, size: 'sm', kind: 'line', ic: 'trash' })}</div>`,
            },
          ],
        });
      }

      const find = (id) => ctx.data.items.find((r) => String(r.id) === String(id));
      ctx.actions({
        generate: async () => {
          showErr(root, 'genErr', '');
          try {
            const { draft } = await api.post('/poll-bank/generate', { category: val('gcat').trim() || null });
            root.querySelector('#bq').value = draft.question || '';
            root.querySelector('#bc').value = draft.context || '';
            for (let i = 0; i < MAX_OPTIONS; i++) root.querySelector('#bo' + i).value = (draft.options || [])[i] || '';
            root.querySelector('#bcat').value = draft.category || '';
            showErr(root, 'addErr', '');
            Admin.toast('Draft ready. Review it, then add it to the bank');
            root.querySelector('#bq').focus();
          } catch (e) { showErr(root, 'genErr', e.message); throw e; }
        },
        add: async () => {
          showErr(root, 'addErr', '');
          try {
            await api.post('/poll-bank', {
              question: val('bq'), context: val('bc'), options: readOptions(root), category: val('bcat').trim() || null,
            });
          } catch (e) { showErr(root, 'addErr', e.message); throw e; }
          Admin.toast('Poll added to the bank');
          Admin.go('poll-bank?tab=list');
        },
        toggle: async (el) => {
          const r = await api.post(`/poll-bank/${el.dataset.id}/toggle`);
          Admin.toast(r.active ? 'Poll activated' : 'Poll deactivated', r.active ? '--green' : '--orange');
          ctx.reload();
        },
        delete: async (el) => {
          const row = find(el.dataset.id);
          const ok = await Admin.confirm({
            title: 'Delete this poll?',
            text: `${row ? `<b>${esc(row.question)}</b><br><br>` : ''}If the bank ends up empty, the built-in polls are re-added the next time this page loads.`,
            ok: 'Delete',
            danger: true,
          });
          if (!ok) return;
          await api.del(`/poll-bank/${el.dataset.id}`);
          Admin.toast('Poll deleted', '--orange');
          ctx.reload();
        },
      });
    },
  });
})();
