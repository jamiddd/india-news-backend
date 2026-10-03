/* Explainers: ask a question, attach real stories as sources, generate with Claude (+ narration),
   review and publish. API: app/admin_explainers.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const STATUS_HUE = { draft: '--muted', generating: '--amber', ready_for_review: '--purple', published: '--green', archived: '--muted' };
  const BOARD = [
    { key: 'drafts', title: 'Drafts', statuses: ['draft', 'generating'], empty: 'No drafts. Ask a question to start one.' },
    { key: 'review', title: 'Ready for review', statuses: ['ready_for_review'], empty: 'Nothing waiting for review.' },
    { key: 'published', title: 'Published', statuses: ['published'], empty: 'Nothing published yet.' },
  ];
  const PER_COL = 8;
  const POLL_MS = 4000;

  const statusPill = (r) => pill(r.statusLabel || r.status, STATUS_HUE[r.status] || '--muted', r.status === 'ready_for_review');
  const review = (id) => Admin.href(`explainers/${id}/review`);
  const select = (id, opts, selected) => `<select class="inp" id="${id}">${opts.map((o) => `<option value="${esc(o.value)}"${o.value === selected ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
  const val = (root, id) => root.querySelector('#' + id).value;
  const duration = (s) => `${Math.floor((s || 0) / 60)}:${String((s || 0) % 60).padStart(2, '0')}`;
  const storyLink = (c, label) => `<a class="link" href="${esc(Admin.publicUrl(c.url))}" target="_blank" rel="noopener">${label}</a>`;

  /* ---------- list / board ---------- */
  const card = (r) => `<div class="card">
      <h3><a href="${review(r.id)}" style="text-decoration:none">${esc(r.question)}</a></h3>
      <div class="meta"><span>${esc(r.category)}</span><span>${esc(r.depth)}</span><span>${fmt.plural(r.sourceCount, 'source')}</span>${r.hasAudio ? '<span>audio</span>' : ''}<span title="${esc(fmt.dateTime(r.updatedAt))}">${esc(fmt.rel(r.updatedAt))}</span></div>
      ${r.status === 'generating' ? `<div>${statusPill(r)}</div><div class="prog"><i style="width:60%"></i></div>` : ''}
      ${r.status === 'draft' && r.error ? `<small class="danger-text">Last attempt failed: ${esc(r.error)}</small>` : ''}
    </div>`;

  Admin.page({
    path: 'explainers',
    nav: 'explainers',
    title: 'Explainers',
    load: () => api.get('/explainers'),
    render: (d) => {
      const waiting = d.counts.ready_for_review || 0;
      const head = ui.head('Explainers', waiting ? `${fmt.plural(waiting, 'explainer')} to review.` : 'Nothing to review.',
        'Questions the newsroom has asked, answered from our own reporting. Attach real stories as sources, generate, read it through, then publish.',
        `<a class="btn btn-ink" href="${Admin.href('explainers/new')}">${ui.icon('plus')}New question</a><a class="btn btn-ghost" href="${Admin.href('explainers/from-story')}">${ui.icon('sparkle')}From a top story</a>`);
      const suggestions = d.suggestions.length ? ui.st(ui.panel('Trending searches', `<p class="note" style="margin-bottom:12px">What readers searched for this week. Pick one to turn it into a question.</p><div class="chips">${d.suggestions.map((s) =>
        `<a class="chip" href="${Admin.href('explainers/new?q=' + encodeURIComponent(s.term))}" style="display:inline-flex;align-items:center;gap:6px;text-decoration:none">${esc(s.term)} <span class="muted num">${fmt.num(s.count)}</span></a>`).join('')}</div>`)) : '';
      const board = ui.st(`<div class="board">${BOARD.map((col) => {
        const rows = d.items.filter((r) => col.statuses.includes(r.status));
        return `<div class="col"><div class="col-h"><b>${col.title}</b><span class="mono num">${rows.length}</span></div>${rows.length ? rows.slice(0, PER_COL).map(card).join('') + (rows.length > PER_COL ? `<small class="muted" style="padding:0 6px">+${rows.length - PER_COL} more in the table below</small>` : '') : ui.empty(col.empty)}</div>`;
      }).join('')}</div>`);
      const table = ui.st(ui.panel('All explainers', '<div id="explainersTable"></div>', `<span class="muted">${fmt.plural(d.counts.archived || 0, 'archived')}</span>`));
      return head + suggestions + board + table;
    },
    mount: (ctx) => {
      Admin.table(ctx.root.querySelector('#explainersTable'), {
        key: 'explainers',
        rows: ctx.data.items,
        order: [[3, 'desc']],
        placeholder: 'Search questions',
        empty: 'No explainers yet. Ask one above.',
        columns: [
          { title: 'Question', lead: true, data: 'question', render: (r) => `<b><a href="${review(r.id)}" style="color:inherit;text-decoration:none">${esc(r.question)}</a></b><small>${esc(r.depth)} · ${fmt.plural(r.sourceCount, 'source')}${r.hasAudio ? ' · audio' : ''}</small>` },
          { title: 'Category', data: 'category' },
          { title: 'Status', data: 'statusLabel', render: statusPill },
          { title: 'Updated', data: 'updatedAt', render: (r) => `<span title="${esc(fmt.dateTime(r.updatedAt))}">${esc(fmt.rel(r.updatedAt))}</span>` },
          {
            title: '', orderable: false, searchable: false, className: 'r',
            render: (r) => `<a class="btn btn-sm btn-line" href="${review(r.id)}">${{ draft: 'Edit', generating: 'Progress', ready_for_review: 'Review', published: 'View', archived: 'Restore' }[r.status] || 'Open'}</a>`,
          },
        ],
      });
      const sig = (items) => items.filter((r) => r.status === 'generating').map((r) => r.id).join(',');
      const before = sig(ctx.data.items);
      if (before) {
        ctx.every(POLL_MS * 2, async () => {
          try {
            const d = await api.get('/explainers');
            if (sig(d.items) !== before) { Admin.refreshBadges(); ctx.reload(); }
          } catch (e) { /* keep the current board */ }
        });
      }
    },
  });

  /* ---------- question form (shared by "new" and "from a story") ---------- */
  const questionForm = (d, { question = '', category = '', placeholder = '' } = {}) => `<div class="form">
      <div class="field"><label for="exQ">Question</label><input class="inp" id="exQ" value="${esc(question)}" placeholder="${esc(placeholder)}" autocomplete="off"></div>
      <div class="form-row">
        <div class="field"><label for="exCat">Category</label>${select('exCat', d.categories, category || d.categories[0].value)}</div>
        <div class="field"><label for="exDepth">Depth</label>${select('exDepth', d.depths, 'standard')}</div>
      </div>
      <div class="field"><label for="exNotes">Angle or notes for the writer AI <span class="muted">(optional)</span></label><textarea class="inp" id="exNotes" rows="3" placeholder="e.g. focus on impact for importers, avoid jargon, mention RBI's likely response"></textarea></div>
      <div class="form-acts">${ui.btn('Save as draft', { act: 'save', ic: 'check' })}<a class="btn btn-ghost" href="${Admin.href('explainers')}">Cancel</a></div>
    </div>`;
  const readForm = (root) => {
    const question = val(root, 'exQ').trim();
    if (!question) { root.querySelector('#exQ').focus(); throw new Error('Write the question first.'); }
    return { question, category: val(root, 'exCat'), depth: val(root, 'exDepth'), adminNotes: val(root, 'exNotes').trim() || null };
  };

  Admin.page({
    path: 'explainers/new',
    nav: 'explainers',
    title: 'New explainer',
    load: () => api.get('/explainers/options'),
    render: (d, p, q) => ui.head('Explainers', 'Ask a new question.', 'Save it as a draft, then attach the stories it should answer from on the next screen.')
      + ui.st(ui.panel('', questionForm(d, { question: q.q || '' }))),
    mount: (ctx) => {
      ctx.root.querySelector('#exQ').focus();
      ctx.actions({
        save: async () => {
          const r = await api.post('/explainers', readForm(ctx.root));
          Admin.toast('Draft saved. Now attach sources.');
          Admin.go(`explainers/${r.id}/review`);
        },
      });
    },
  });

  /* ---------- from a top story ---------- */
  Admin.page({
    path: 'explainers/from-story',
    nav: 'explainers',
    title: 'From a top story',
    load: (p, q) => (q.q ? api.get('/explainers/from-story', { q: q.q }) : { q: '', items: [] }),
    render: (d) => ui.head('Explainers', 'Start from a top story.', 'Search for a story, then write the question yourself. Background stories already in our database get found and attached as sources on the next screen.')
      + ui.st(ui.panel('', `<div class="form-row" style="align-items:end"><div class="field"><label for="storyQ">Search stories</label><input class="inp" id="storyQ" value="${esc(d.q)}" placeholder="e.g. rupee dollar RBI" autocomplete="off"></div><div class="form-acts">${ui.btn('Search', { act: 'search', ic: 'search' })}</div></div>`))
      + (d.q ? ui.st(ui.panel(`${fmt.plural(d.items.length, 'match', 'matches')}`, d.items.length ? `<div class="list">${d.items.map((c) => `<div class="row"><div class="body"><b>${esc(c.headline)}</b><small>${fmt.plural(c.sourceCount, 'outlet')} · story ${c.id} · ${storyLink(c, 'open')}</small></div><div class="acts"><a class="btn btn-sm btn-ink" href="${Admin.href(`explainers/from-story/${c.id}/new`)}">Use this story</a></div></div>`).join('')}</div>` : ui.empty('No matching stories. Try fewer or different words.'))) : ''),
    mount: (ctx) => {
      const input = ctx.root.querySelector('#storyQ');
      const go = () => Admin.go('explainers/from-story' + (input.value.trim() ? '?q=' + encodeURIComponent(input.value.trim()) : ''));
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
      if (!ctx.query.q) input.focus();
      ctx.actions({ search: go });
    },
  });

  Admin.page({
    path: 'explainers/from-story/:clusterId/new',
    nav: 'explainers',
    title: 'From a top story',
    load: (p) => api.get(`/explainers/from-story/${p.clusterId}`),
    render: (d) => ui.head('Explainers', 'Ask about this story.', 'The story leads the source list. Related background from our database is attached automatically when you save.')
      + ui.st(`<div class="grid g-main"><div>${ui.panel('Question', questionForm(d, { category: d.suggestedCategory, placeholder: 'e.g. Why did the Supreme Court order this exemption?' }))}</div><div>${ui.panel('Story', `<div class="form"><b>${esc(d.story.headline)}</b>${d.story.summary ? `<p class="note">${esc(d.story.summary)}</p>` : ''}<small class="muted">${fmt.plural(d.story.sourceCount, 'outlet')} · story ${d.story.id} · ${storyLink(d.story, 'open')}</small></div>`)}</div></div>`),
    mount: (ctx) => {
      ctx.root.querySelector('#exQ').focus();
      ctx.actions({
        save: async () => {
          const r = await api.post(`/explainers/from-story/${ctx.params.clusterId}`, readForm(ctx.root));
          Admin.toast(`Draft saved with ${fmt.plural(r.sourceCount, 'source')}.`);
          Admin.go(`explainers/${r.id}/review`);
        },
      });
    },
  });

  /* ---------- review / editor ---------- */
  const progressPanel = (d) => ui.st(ui.panel('Generating', `<div class="form"><p class="note" id="exProgress">${esc(d.progress || 'starting')}…</p><div class="prog"><i style="width:60%"></i></div><small class="muted">This page updates by itself. It usually takes a couple of minutes.</small></div>`));

  const voiceAndNarrate = (d, { narrateId, voiceId, checked }) => `<div class="form-row" style="align-items:end">
      <label class="check"><input type="checkbox" id="${narrateId}"${checked ? ' checked' : ''}> Also generate audio narration</label>
      <div class="field"><label for="${voiceId}">Voice</label>${select(voiceId, d.voices, d.voice || 'shubh')}</div>
    </div>${d.audioConfigured ? '' : '<p class="hint">Audio isn\'t configured on this server; narration would be skipped.</p>'}`;

  const draftView = (d) => {
    const attached = d.attachedSources;
    const sources = ui.panel(`Sources <span class="muted num">${attached.length}</span>`, `<div class="form">
        <p class="note">Explainers answer from our own reporting, not the model's general knowledge. Attach at least one real story before generating.</p>
        <div class="form-row" style="align-items:end"><div class="field"><label for="srcQ">Search real stories to attach</label><input class="inp" id="srcQ" placeholder="e.g. rupee dollar RBI" autocomplete="off"></div><div class="form-acts">${ui.btn('Search', { act: 'srcSearch', ic: 'search', kind: 'ghost' })}</div></div>
        <div id="srcResults"></div>
        <hr class="sep">
        ${attached.length ? `<div class="list">${attached.map((c, i) => `<div class="row"><div class="body"><b>${esc(c.headline)}</b><small>${i === 0 ? 'Leads · ' : ''}${fmt.plural(c.sourceCount, 'outlet')} · story ${c.id} · ${storyLink(c, 'open')}</small></div><div class="acts">${ui.btn('Remove', { act: 'srcRemove', id: c.id, size: 'sm', kind: 'line', ic: 'x', attrs: d.running ? 'disabled' : '' })}</div></div>`).join('')}</div>` : ui.empty('No sources attached yet. Search above and add at least one.')}
      </div>`);
    const control = d.running ? '' : ui.panel('Generate', attached.length ? `<div class="form">
        ${voiceAndNarrate(d, { narrateId: 'genNarrate', voiceId: 'genVoice', checked: false })}
        <div class="form-acts">${ui.btn('Generate explainer', { act: 'generate', ic: 'sparkle', kind: 'hue' })}</div>
      </div>` : '<p class="note">Attach at least one source to enable generation.</p>');
    return ui.st(`<div class="grid g-main"><div>${sources}</div><div style="display:flex;flex-direction:column;gap:16px">${control}${detailsPanel(d)}</div></div>`);
  };

  const detailsPanel = (d) => ui.panel('Details', `<dl class="kv">
      <dt>Category</dt><dd>${esc(d.category)}</dd>
      <dt>Depth</dt><dd>${esc(d.depth)}</dd>
      <dt>Notes</dt><dd>${d.adminNotes ? esc(d.adminNotes) : '<span class="muted">none</span>'}</dd>
      ${d.generationCost != null ? `<dt>Cost so far</dt><dd>${fmt.inr(d.generationCost)}</dd>` : ''}
      <dt>Created</dt><dd>${esc(fmt.dateTime(d.createdAt))}</dd>
      <dt>Updated</dt><dd>${esc(fmt.dateTime(d.updatedAt))}</dd>
      ${d.publishedAt ? `<dt>Published</dt><dd>${esc(fmt.dateTime(d.publishedAt))}</dd>` : ''}
    </dl>${d.publicUrl ? `<p style="margin:12px 0 0"><a class="link" href="${esc(Admin.publicUrl(d.publicUrl))}" target="_blank" rel="noopener">Public JSON ↗</a></p>` : ''}`);

  const contentView = (d) => {
    const audio = ui.panel('Narration', `<div class="form">
        ${d.audioUrl ? `<audio controls preload="none" src="${esc(d.audioUrl)}"></audio><small class="muted">${duration(d.audioDurationSeconds)} · ${esc(d.voice || '')}</small>` : '<p class="note">No narration yet.</p>'}
        ${d.audioConfigured && !d.running ? `<div class="form-row" style="align-items:end"><div class="field"><label for="audVoice">Voice</label>${select('audVoice', d.voices, d.voice || 'shubh')}</div><div class="form-acts">${ui.btn(d.audioUrl ? 'Regenerate audio' : 'Generate audio', { act: 'regenAudio', ic: 'mic', kind: 'ghost' })}</div></div>` : ''}
        ${d.audioConfigured ? '' : '<p class="hint">Audio isn\'t configured on this server.</p>'}
      </div>`);
    const hero = d.heroImageUrl ? ui.panel('Hero image', `<img class="thumb" src="${esc(d.heroImageUrl)}" alt=""><p class="hint" style="margin:8px 0 0">Taken from the lead source story. It changes when you regenerate.</p>`) : '';
    const quick = ui.panel('Quick answer', `<div class="prose">${esc(d.quickAnswer || '')}</div>`);
    const sections = d.sections.map((s, i) => ui.panel(esc(s.heading), `<div class="prose">${esc(s.body)}</div>`,
      ui.btn('Regenerate section', { act: 'regenSection', id: i, size: 'sm', kind: 'line', ic: 'refresh', attrs: d.running ? 'disabled' : '' }))).join('');
    const sources = ui.panel(`Sources used <span class="muted num">${d.sources.length}</span>`, d.sources.length ? `<div class="list">${d.sources.map((s) =>
      `<div class="row"><div class="body"><b>${s.clusterId ? storyLink({ url: `/api/v1/clusters/${s.clusterId}` }, esc(s.title)) : esc(s.title)}</b><small>${esc(s.outlet || '')}${s.sourceCount ? ' · ' + fmt.plural(s.sourceCount, 'outlet') : ''}</small></div></div>`).join('')}</div>` : ui.empty('No sources recorded.'));
    const regenAll = d.status === 'ready_for_review' && !d.running ? ui.panel('Regenerate everything', `<div class="form">
        <p class="note">${esc(d.confirm.regenerateAll)}</p>
        ${voiceAndNarrate(d, { narrateId: 'allNarrate', voiceId: 'allVoice', checked: !!d.audioUrl })}
        <div class="form-acts">${ui.btn('Regenerate all', { act: 'regenAll', ic: 'refresh', kind: 'line' })}</div></div>`) : '';
    return ui.st(`<div class="grid g-main"><div style="display:flex;flex-direction:column;gap:16px">${quick}${sections}${sources}</div><div style="display:flex;flex-direction:column;gap:16px">${audio}${hero}${detailsPanel(d)}${regenAll}</div></div>`);
  };

  const headActions = (d) => {
    if (d.running) return '';
    if (d.status === 'ready_for_review') return ui.btn('Publish', { act: 'publish', ic: 'send', kind: 'hue' });
    if (d.status === 'published') return ui.btn('Archive', { act: 'archive', ic: 'archive', kind: 'line' });
    if (d.status === 'archived') return ui.btn('Restore and republish', { act: 'restore', ic: 'restore', kind: 'ink' });
    return '';
  };

  Admin.page({
    path: 'explainers/:id/review',
    nav: 'explainers',
    title: (d) => (d ? `Explainer #${d.id}` : 'Explainer'),
    load: (p) => api.get(`/explainers/${p.id}`),
    render: (d) => {
      const sub = `${statusPill(d)} <span class="muted">· ${esc(d.category)} · ${esc(d.depth)}</span>`;
      const head = ui.head(`Explainer #${d.id}`, esc(d.question), sub, headActions(d));
      const failed = d.error && !d.running ? ui.st(ui.panel('', `<p class="danger-text" style="margin:0"><b>Last attempt failed:</b> ${esc(d.error)}</p>`)) : '';
      const lastAudioError = !d.running && d.lastRun && d.lastRun.state === 'error' && !d.error ? ui.st(ui.panel('', `<p class="danger-text" style="margin:0"><b>Last run failed:</b> ${esc(d.lastRun.message)}</p>`)) : '';
      const body = d.status === 'draft' || d.status === 'generating' ? draftView(d) : contentView(d);
      return head + (d.running ? progressPanel(d) : '') + failed + lastAudioError + body;
    },
    mount: (ctx) => {
      const d = ctx.data, id = d.id, root = ctx.root;
      const base = `/explainers/${id}`;
      const after = (msg, hue) => { if (msg) Admin.toast(msg, hue); Admin.refreshBadges(); ctx.reload(); };

      if (d.running) {
        ctx.every(POLL_MS, async () => {
          let s;
          try { s = await api.get(`${base}/status`); } catch (e) { return; }
          if (s.state === 'generating') {
            const el = root.querySelector('#exProgress');
            if (el) el.textContent = (s.message || 'working') + '…';
          } else if (s.state === 'error') after(`Generation failed: ${s.message || 'unknown error'}`, '--red');
          else after(s.message ? `Done: ${s.message}.` : 'Done.');
        });
      }

      const srcInput = root.querySelector('#srcQ');
      const results = root.querySelector('#srcResults');
      const search = async () => {
        const q = srcInput.value.trim();
        if (!q) { results.innerHTML = ''; return; }
        const r = await api.get('/explainers/from-story', { q, limit: 8, explainer_id: id });
        results.innerHTML = r.items.length ? `<div class="list">${r.items.map((c) => `<div class="row"><div class="body"><b>${esc(c.headline)}</b><small>${fmt.plural(c.sourceCount, 'outlet')} · story ${c.id} · ${storyLink(c, 'open')}</small></div><div class="acts">${c.attached ? '<span class="muted">Already added</span>' : ui.btn('Add as source', { act: 'srcAdd', id: c.id, size: 'sm', ic: 'plus' })}</div></div>`).join('')}</div>` : ui.empty('No matching stories.');
      };
      if (srcInput) {
        srcInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search().catch((err) => Admin.toast(err.message, '--red')); } });
        const remembered = sessionStorageGet(`ex-src-q-${id}`);
        if (remembered) { srcInput.value = remembered; search().catch(() => {}); }
        ctx.onCleanup(() => sessionStorageSet(`ex-src-q-${id}`, srcInput.value.trim()));
      }

      const startGen = async (narrateId, voiceId, title, text, ok) => {
        if (!(await Admin.confirm({ title, text: esc(text), ok }))) return;
        await api.post(`${base}/generate`, { narrate: root.querySelector('#' + narrateId).checked, voice: val(root, voiceId) });
        after('Generation started.', '--purple');
      };

      ctx.actions({
        srcSearch: search,
        srcAdd: async (el) => { await api.post(`${base}/sources/add`, { clusterId: Number(el.dataset.id) }); after('Source added.'); },
        srcRemove: async (el) => { await api.post(`${base}/sources/remove`, { clusterId: Number(el.dataset.id) }); after('Source removed.', '--muted'); },
        generate: () => startGen('genNarrate', 'genVoice', 'Generate this explainer?', d.confirm.generate, 'Generate'),
        regenAll: () => startGen('allNarrate', 'allVoice', 'Regenerate the whole explainer?', d.confirm.regenerateAll, 'Regenerate all'),
        regenAudio: async () => {
          if (!(await Admin.confirm({ title: d.audioUrl ? 'Regenerate narration?' : 'Generate narration?', text: esc(d.confirm.regenerateAudio), ok: d.audioUrl ? 'Regenerate audio' : 'Generate audio' }))) return;
          await api.post(`${base}/regenerate-audio`, { voice: val(root, 'audVoice') });
          after('Voicing started.', '--purple');
        },
        regenSection: async (el) => {
          await api.post(`${base}/regenerate-section`, { sectionIndex: Number(el.dataset.id) });
          after('Section rewritten.');
        },
        publish: async () => {
          if (!(await Admin.confirm({ title: 'Publish this explainer?', text: 'It goes live in the app straight away.', ok: 'Publish' }))) return;
          await api.post(`${base}/publish`);
          after('Published.');
        },
        archive: async () => {
          if (!(await Admin.confirm({ title: 'Archive this explainer?', text: 'It disappears from the app. You can restore it later.', ok: 'Archive', danger: true }))) return;
          await api.post(`${base}/archive`);
          after('Archived.', '--muted');
        },
        restore: async () => {
          if (!(await Admin.confirm({ title: 'Restore and republish?', text: 'It goes live in the app again straight away.', ok: 'Restore' }))) return;
          await api.post(`${base}/restore`);
          after('Restored and republished.');
        },
      });
    },
  });

  function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function sessionStorageSet(k, v) { try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch (e) { /* ignore */ } }
})();
