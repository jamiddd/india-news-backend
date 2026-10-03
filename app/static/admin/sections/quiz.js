/* Daily Quiz review + Quiz bank. APIs: app/quiz_admin.py, app/quiz_bank_admin.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const STATUS_HUE = { draft: '--amber', approved: '--green', rejected: '--red' };
  const SLOW = 'Drafting with Claude can take up to a minute. Keep this page open.';

  /* An editable question: text, four options with a radio for the correct one, explanation. */
  const questionEditor = (q, i, n) => `
    <div class="field"><label for="q${i}">Question</label>
      <textarea class="inp" id="q${i}" data-q="${i}" rows="2" required>${esc(q.question)}</textarea></div>
    <div class="field"><label>Options <span class="hint">· select the correct answer</span></label>
      <div class="form" style="gap:8px">${Array.from({ length: n }, (_, o) => `
        <label class="opt" style="font-weight:400">
          <input type="radio" name="c${i}" value="${o}" ${o === q.correctIndex ? 'checked' : ''} aria-label="Option ${o + 1} is correct" style="width:18px;height:18px;accent-color:var(--hue);margin:0;flex:none">
          <input class="inp" data-o="${i}:${o}" value="${esc(q.options[o] || '')}" placeholder="Option ${o + 1}" aria-label="Option ${o + 1}">
        </label>`).join('')}</div></div>
    <div class="field"><label for="e${i}">Explanation</label>
      <input class="inp" id="e${i}" data-e="${i}" value="${esc(q.explanation || '')}" placeholder="One sentence on why the answer is right"></div>`;

  const readEditor = (root, i, n) => ({
    question: root.querySelector(`[data-q="${i}"]`).value,
    options: Array.from({ length: n }, (_, o) => root.querySelector(`[data-o="${i}:${o}"]`).value),
    correctIndex: Number((root.querySelector(`input[name="c${i}"]:checked`) || { value: -1 }).value),
    explanation: root.querySelector(`[data-e="${i}"]`).value,
  });

  /* Read-only question with the correct option marked. */
  const questionView = (q, i) => `<div class="q"><b>${i + 1}. ${esc(q.question)}</b>
    <div class="form" style="gap:6px;margin-top:10px">${q.options.map((o, k) => `<div class="opt"${k === q.correctIndex ? ' style="box-shadow:inset 0 0 0 2px var(--hue)"' : ''}>${k === q.correctIndex ? ui.icon('check') : '<span style="width:20px"></span>'}<span>${esc(o)}</span></div>`).join('')}</div>
    ${q.explanation ? `<div class="ans">${esc(q.explanation)}</div>` : ''}</div>`;

  /* ---------------- Daily Quiz ---------------- */
  Admin.page({
    path: 'quiz',
    nav: 'quiz',
    title: 'Daily Quiz',
    load: () => api.get('/quiz'),
    render: (d) => {
      const q = d.quiz;
      if (!q) {
        return ui.head('Daily Quiz', `No quiz for ${esc(fmt.day(d.date))} yet.`,
          'Claude drafts five questions; you review them before readers see anything. Until a quiz is approved, readers get the curated fallback set.')
          + ui.st(ui.panel('Generate today’s draft', `<div class="form"><p class="note">Claude writes five questions on today’s theme. If its draft fails validation, the quiz bank and then the curated set fill in.</p>
            <div class="form-acts">${ui.btn('Generate draft', { act: 'generate', kind: 'hue', ic: 'sparkle' })}<span class="hint">${SLOW}</span></div></div>`));
      }
      const meta = `${pill(q.status, STATUS_HUE[q.status] || '--muted', true)} ${pill('Source: ' + q.source)}`;
      const served = q.servedToReaders
        ? `Readers are being served this quiz${q.publishAt ? ` from ${esc(fmt.dateTime(q.publishAt))}` : ''}.`
        : 'Readers are currently being served the curated fallback set, not this quiz.';
      const head = ui.head('Daily Quiz', `Quiz for ${esc(fmt.day(q.puzzleDate))}`,
        `${meta}<br><span class="note">${served}</span>`);

      if (!q.editable) {
        return head
          + ui.st(ui.panel('This quiz is no longer a draft', `<div class="form">
              <p class="note">${q.status === 'approved' ? 'Approved' : 'Rejected'}${q.approvedAt ? ' ' + esc(fmt.dateTime(q.approvedAt)) : ''}. It can't be edited. Regenerate to start a new draft.</p>
              <div class="form-acts">${ui.btn('Regenerate a new draft', { act: 'regenerate', kind: 'line', ic: 'refresh' })}<span class="hint">${SLOW}</span></div></div>`))
          + ui.st(ui.panel('Questions', q.questions.map(questionView).join('')));
      }

      return head
        + q.questions.map((qq, i) => ui.st(ui.panel(`Question ${i + 1}`, `<div class="form">${questionEditor(qq, i, d.optionCount)}</div>`))).join('')
        + ui.st(ui.panel('', `<div class="form">
            <p class="note">Your edits are saved when you approve. Regenerate and reject discard them.</p>
            <div class="form-acts">
              ${ui.btn('Approve and publish', { act: 'approve', kind: 'hue', ic: 'check' })}
              ${ui.btn('Regenerate', { act: 'regenerate', kind: 'line', ic: 'refresh' })}
              ${ui.btn('Reject and use curated set', { act: 'reject', kind: 'red', ic: 'x' })}
            </div><span class="hint">${SLOW}</span></div>`));
    },
    mount: (ctx) => {
      const d = ctx.data, q = d.quiz;
      const done = (msg, hue) => { Admin.toast(msg, hue); Admin.refreshBadges(); ctx.reload(); };
      ctx.actions({
        generate: async () => {
          await api.post('/quiz/generate');
          done('Draft ready for review');
        },
        regenerate: async () => {
          const text = q.status === 'approved'
            ? 'Regenerating takes the live quiz offline: readers get the curated set until you approve the new draft.'
            : q.editable ? 'All five questions are replaced with a new Claude draft. Your edits are lost.' : 'A new Claude draft replaces these questions.';
          if (!(await Admin.confirm({ title: 'Regenerate the quiz?', text: esc(text), ok: 'Regenerate', danger: q.status === 'approved' }))) return;
          await api.post('/quiz/generate');
          done('New draft ready for review');
        },
        approve: async () => {
          const questions = q.questions.map((_, i) => readEditor(ctx.root, i, d.optionCount));
          await api.post(`/quiz/${q.id}/approve`, { questions });
          done('Quiz approved and published');
        },
        reject: async () => {
          if (!(await Admin.confirm({ title: 'Reject this draft?', text: 'Readers get the curated fallback set today. You can still regenerate a new draft afterwards.', ok: 'Reject draft', danger: true }))) return;
          await api.post(`/quiz/${q.id}/reject`);
          done('Draft rejected. Readers get the curated set.', '--orange');
        },
      });
    },
  });

  /* ---------------- Quiz bank ---------------- */
  const TABS = [['create', 'Add question'], ['list', 'All questions']];
  const SHOW = [['all', 'All'], ['active', 'Active'], ['inactive', 'Inactive']];
  const blank = (n) => ({ question: '', options: Array(n).fill(''), correctIndex: 0, explanation: '', category: '' });
  let pendingDraft = null; // a Claude draft carried into the add form across the re-render

  Admin.page({
    path: 'quiz-bank',
    nav: 'quiz-bank',
    title: 'Quiz bank',
    load: () => api.get('/quiz-bank'),
    render: (d, p, query) => {
      const tab = query.tab === 'list' ? 'list' : 'create';
      const n = d.optionCount;
      const tabs = `<div class="chips">${TABS.map(([k, label]) => `<a class="chip ${tab === k ? 'on' : ''}" href="${Admin.href('quiz-bank' + (k === 'create' ? '' : '?tab=' + k))}" style="display:inline-flex;align-items:center;text-decoration:none">${label}${k === 'list' ? ` · <span class="num">&nbsp;${d.counts.total}</span>` : ''}</a>`).join('')}</div>`;
      const head = ui.head('Quiz bank', `${fmt.plural(d.counts.total, 'question')} · ${fmt.num(d.counts.active)} active`,
        'When Claude’s daily draft fails validation, the quiz draws five active questions from here, least recently used first, before falling back to the hardcoded curated set.')
        + ui.st(tabs);

      if (tab === 'list') {
        const show = SHOW.some(([k]) => k === query.show) ? query.show : 'all';
        const cat = query.category || '';
        return head + ui.st(`<div class="panel" style="display:flex;flex-direction:column;gap:14px">
          <div class="form-acts" style="justify-content:space-between">
            <div class="chips">${SHOW.map(([k, label]) => `<button class="chip ${show === k ? 'on' : ''}" data-act="show" data-id="${k}">${label} · <span class="num">&nbsp;${fmt.num(k === 'all' ? d.counts.total : d.counts[k])}</span></button>`).join('')}</div>
            ${d.categories.length ? `<select class="inp" id="bankCat" style="width:auto;min-width:180px" aria-label="Category"><option value="">All categories</option>${d.categories.map((c) => `<option value="${esc(c)}" ${c === cat ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>` : ''}
          </div>
          <div id="bankTable"></div></div>`);
      }

      const draft = pendingDraft || blank(n);
      return head
        + ui.st(ui.panel('Draft with Claude', `<div class="form">
            <div class="field"><label for="genCat">Category (optional)</label>
              <input class="inp" id="genCat" list="bankCats" value="${esc(draft.category || '')}" placeholder="e.g. history, geography"></div>
            <div class="form-acts">${ui.btn('Draft a question', { act: 'draft', kind: 'line', ic: 'sparkle' })}
              <span class="hint">The draft fills the form below. Nothing is saved until you add it. ${SLOW}</span></div></div>`))
        + ui.st(ui.panel('Add a question', `<div class="form" id="addForm">
            ${questionEditor(draft, 0, n)}
            <div class="field"><label for="addCat">Category (optional)</label>
              <input class="inp" id="addCat" list="bankCats" value="${esc(draft.category || '')}" placeholder="e.g. history, geography"></div>
            <datalist id="bankCats">${d.categories.map((c) => `<option value="${esc(c)}">`).join('')}</datalist>
            <div class="form-acts">${ui.btn('Add to bank', { act: 'add', kind: 'hue', ic: 'plus' })}${ui.btn('Clear form', { act: 'clear', kind: 'ghost' })}</div></div>`));
    },
    mount: (ctx) => {
      const d = ctx.data, n = d.optionCount;
      const tab = ctx.query.tab === 'list' ? 'list' : 'create';

      if (tab === 'create') {
        const draftFromForm = () => ({ ...readEditor(ctx.root, 0, n), category: ctx.root.querySelector('#addCat').value });
        ctx.actions({
          draft: async () => {
            const category = ctx.root.querySelector('#genCat').value;
            const r = await api.post('/quiz-bank/generate', { category });
            pendingDraft = { ...r.draft, category: r.draft.category || '' };
            ctx.reload();
            Admin.toast('Draft ready. Review it, then add it to the bank.');
          },
          add: async () => {
            await api.post('/quiz-bank', draftFromForm());
            pendingDraft = null;
            Admin.toast('Question added to the bank');
            ctx.reload();
          },
          clear: () => { pendingDraft = null; ctx.reload(); },
        });
        return;
      }

      const show = SHOW.some(([k]) => k === ctx.query.show) ? ctx.query.show : 'all';
      const cat = ctx.query.category || '';
      const rows = d.items.filter((r) => (show === 'all' || (show === 'active') === r.isActive) && (!cat || r.category === cat));
      /* action: 'activate' | 'deactivate' | 'delete'. A row button flips one question via /toggle;
         bulk actions set the state explicitly, so re-activating an active row is harmless. */
      const run = async (ids, action, { toggle = false } = {}) => {
        if (action === 'delete' && !(await Admin.confirm({ title: `Delete ${fmt.plural(ids.length, 'question')}?`, text: 'This can’t be undone. Deactivate instead to keep a question out of quizzes without losing it.', ok: 'Delete', danger: true }))) return;
        if (toggle) await api.post(`/quiz-bank/${ids[0]}/toggle`);
        else if (action === 'delete' && ids.length === 1) await api.del(`/quiz-bank/${ids[0]}`);
        else await api.post('/quiz-bank/bulk', { ids: ids.map(Number), action });
        Admin.toast(`${fmt.plural(ids.length, 'question')} ${action === 'delete' ? 'deleted' : action + 'd'}`, action === 'delete' ? '--orange' : '--green');
        ctx.reload();
      };
      const bulk = (action) => (ids) => run(ids, action);
      Admin.table(ctx.root.querySelector('#bankTable'), {
        key: `quiz-bank-${show}-${cat}`,
        rows,
        order: [[4, 'desc']],
        placeholder: 'Search question, option or category',
        empty: d.counts.total ? 'No questions match this filter.' : 'No questions in the bank yet. Add one from the Add question tab.',
        select: {
          id: (r) => r.id,
          actions: [
            { label: 'Activate', primary: true, run: bulk('activate') },
            { label: 'Deactivate', run: bulk('deactivate') },
            { label: 'Delete', run: bulk('delete') },
          ],
        },
        columns: [
          {
            title: 'Question', lead: true, data: (r) => `${r.question} ${r.options.join(' ')}`,
            render: (r) => `<b>${esc(r.question)}</b><small>${r.options.map((o, i) => i === r.correctIndex ? `<strong style="color:var(--ink)">★ ${esc(o)}</strong>` : esc(o)).join(' · ')}</small>${r.explanation ? `<small>${esc(r.explanation)}</small>` : ''}`,
          },
          { title: 'Category', data: (r) => r.category || '', render: (r) => (r.category ? pill(r.category, '--muted') : '<span class="muted">—</span>') },
          { title: 'Active', data: (r) => (r.isActive ? 'active' : 'inactive'), render: (r) => pill(r.isActive ? 'Active' : 'Inactive', r.isActive ? '--green' : '--muted') },
          { title: 'Used', data: 'usedCount', className: 'num', render: (r) => `<span title="${r.lastUsedAt ? 'Last used ' + esc(fmt.dateTime(r.lastUsedAt)) : 'Never used'}">${fmt.num(r.usedCount)}</span>` },
          { title: 'Added', data: 'createdAt', render: (r) => `<span title="${esc(fmt.dateTime(r.createdAt))}">${esc(fmt.date(r.createdAt))}</span>` },
          {
            title: '', orderable: false, searchable: false, className: 'r',
            render: (r) => `<div class="acts" style="display:flex;gap:6px;justify-content:flex-end">${ui.btn(r.isActive ? 'Deactivate' : 'Activate', { act: 'toggle', id: r.id, size: 'sm', kind: r.isActive ? 'line' : 'ink' })}${ui.btn('Delete', { act: 'del', id: r.id, size: 'sm', kind: 'line', ic: 'trash' })}</div>`,
          },
        ],
      });
      const sel = ctx.root.querySelector('#bankCat');
      if (sel) sel.addEventListener('change', () => { Admin.setQuery({ category: sel.value }); ctx.reload(); });
      ctx.actions({
        show: (el) => { Admin.setQuery({ show: el.dataset.id === 'all' ? '' : el.dataset.id }); ctx.reload(); },
        toggle: (el) => { const r = d.items.find((x) => String(x.id) === el.dataset.id); return run([el.dataset.id], r && r.isActive ? 'deactivate' : 'activate', { toggle: true }); },
        del: (el) => run([el.dataset.id], 'delete'),
      });
    },
  });
})();
