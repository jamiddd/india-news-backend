/* Daily Brief + Late-Night Wrap-up: read the latest of each kind and rebuild it.
   API: app/admin_daily_brief.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const mmss = (s) => `${Math.floor((s || 0) / 60)}:${String((s || 0) % 60).padStart(2, '0')}`;
  const STATUS_HUE = { ready: '--green', building: '--amber', failed: '--red' };
  const longDate = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

  const runLine = (s) => {
    if (!s || !s.state || s.state === 'idle') return '';
    const at = s.at ? esc(fmt.dateTime(s.at)) : '';
    if (s.state === 'running') return `${pill('Building…', '--amber', true)} <span class="muted">${esc(s.message)}${at ? ` (started ${at})` : ''}. The page updates itself.</span>`;
    if (s.state === 'failed') return `${pill('Last run failed', '--red')} ${esc(s.message)} <span class="muted">(${at})</span>`;
    return `<span class="muted">Last run: ${esc(s.message)} (${at})</span>`;
  };

  const story = (it, n) => `<div class="row" style="align-items:flex-start">
    <div class="body" style="display:flex;flex-direction:column;gap:6px">
      <b>${n}. ${esc(it.headline)}</b>
      <div class="kv-inline">
        <span>${esc(it.category)}</span>
        <span>${fmt.plural(it.sourceCount, 'outlet')}</span>
        <span>${it.slotKind === 'most_covered' ? 'most covered' : 'category pick'}</span>
        <span>cluster <a class="link" href="${esc(Admin.publicUrl(it.clusterUrl))}" target="_blank" rel="noopener">${esc(it.clusterId)}</a></span>
        ${it.audioOffset != null ? `<span>starts at ${Math.round(it.audioOffset)}s</span>` : ''}
      </div>
      <p class="note" style="color:var(--ink)"><b>On screen:</b> ${esc(it.summary)}</p>
      <p class="note"><b>Spoken:</b> ${esc(it.spoken)}</p>
    </div>
    ${it.imageUrl ? `<img class="thumb" src="${esc(it.imageUrl)}" loading="lazy" alt="" style="width:132px;flex:none">` : ''}
  </div>`;

  const briefPanel = (d) => {
    const b = d.brief;
    const label = d.kinds[d.kind];
    if (!b || !b.items.length) {
      return ui.panel('', ui.empty(`No ${esc(label.toLowerCase())} has been built yet.`)
        + (b && b.error ? `<p class="note" style="margin-top:8px">Latest row (${esc(b.briefDate)}, ${esc(b.status)}): ${esc(b.error)}</p>` : ''));
    }
    const audio = b.audio
      ? `<audio controls preload="none" src="${esc(b.audio.url)}"></audio><span class="hint">${mmss(b.audio.durationSeconds)}</span>`
      : `${pill('Text-only', '--muted')} <span class="muted">No audio on this ${d.kind === 'wrapup' ? 'wrap-up' : 'brief'}.</span>`;
    return ui.panel(`${esc(label)} for ${esc(longDate(b.briefDate))}`,
      `<div style="display:flex;flex-direction:column;gap:12px">
        <div class="kv-inline"><span>Generated ${b.generatedAt ? esc(fmt.dateTime(b.generatedAt)) : 'at an unknown time'}</span><span>${fmt.plural(b.items.length, 'story', 'stories')}</span></div>
        <div style="display:flex;flex-direction:column;gap:4px">${audio}</div>
        ${b.error ? `<p class="note"><b>Error:</b> ${esc(b.error)}</p>` : ''}
        <p class="prose" style="margin:0"><b>Intro:</b> ${esc(b.intro)}</p>
        <div class="list">${b.items.map((it, i) => story(it, i + 1)).join('')}</div>
        <p class="prose" style="margin:0"><b>Closing:</b> ${esc(b.closing)}</p>
      </div>`,
      pill(b.status, STATUS_HUE[b.status] || '--muted'));
  };

  Admin.page({
    path: 'daily-brief',
    nav: 'daily-brief',
    title: 'Daily Brief',
    load: (p, q) => api.get('/daily-brief', { kind: q.kind }),
    render: (d) => {
      const label = d.kinds[d.kind];
      const running = d.status.state === 'running';
      const verb = d.builtToday ? 'Rebuild' : 'Build';
      const action = running
        ? ui.btn('Building…', { attrs: 'disabled', ic: 'refresh' })
        : ui.btn(`${verb} ${esc(label.toLowerCase())}`, { act: 'rebuild', ic: 'refresh', attrs: d.configured ? '' : 'disabled' });
      const tabs = Object.entries(d.kinds).map(([k, l]) =>
        `<a class="chip ${k === d.kind ? 'on' : ''}" href="${Admin.href('daily-brief' + (k === 'brief' ? '' : '?kind=' + k))}" style="display:inline-flex;align-items:center;text-decoration:none">${esc(l)}</a>`).join('');
      const run = runLine(d.status);
      return ui.head('Daily Brief', esc(label), `${esc(d.schedule)} Today (IST) is ${esc(fmt.day(d.today + 'T12:00:00+05:30'))}.`, action)
        + ui.st(`<div class="chips">${tabs}</div>`)
        + (run || !d.configured ? ui.st(ui.panel('', `<div style="display:flex;flex-direction:column;gap:8px">${run ? `<div>${run}</div>` : ''}${d.configured ? '' : `<p class="note">${esc(d.notConfiguredText)}</p>`}</div>`)) : '')
        + ui.st(briefPanel(d));
    },
    mount: (ctx) => {
      const d = ctx.data;
      const label = d.kinds[d.kind];
      ctx.actions({
        rebuild: async () => {
          const ok = await Admin.confirm({
            title: `${d.builtToday ? 'Rebuild' : 'Build'} the ${label}?`,
            text: esc(d.confirm),
            ok: d.builtToday ? 'Rebuild' : 'Build',
          });
          if (!ok) return;
          await api.post('/daily-brief/rebuild', { kind: d.kind });
          Admin.toast(`${label} build started. It takes a few minutes.`, '--amber');
          ctx.reload();
        },
      });
      // The build runs in the background on whichever worker started it;
      // poll the shared status and reload once it settles.
      if (d.status.state === 'running') {
        ctx.every(10000, async () => {
          try {
            const s = await api.get('/daily-brief/status', { kind: d.kind });
            if (s.state === 'running') return;
            Admin.toast(s.state === 'failed' ? `Build failed: ${s.message}` : `${label} built`, s.state === 'failed' ? '--red' : '--green');
            ctx.reload();
          } catch (e) { /* transient; try again next tick */ }
        });
      }
    },
  });
})();
