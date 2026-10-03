/* Timelines: editorial picks, narration and lead-image overrides for the Timeline/Context tab.
   API: app/admin_timelines.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;
  const mmss = (s) => `${Math.floor((s || 0) / 60)}:${String((s || 0) % 60).padStart(2, '0')}`;
  const ext = (url, label) => `<a class="link" href="${esc(Admin.publicUrl(url))}" target="_blank" rel="noopener">${esc(label)}</a>`;

  const flags = (r) => [
    r.isEditorialPick ? pill('Editorial pick', '--teal', true) : pill('Algorithmic slot'),
    r.lastSeenInTop ? '' : pill(`Not in the top 5${r.droppedFromTopAt ? ' since ' + fmt.date(r.droppedFromTopAt) : ''}`, '--muted'),
    r.coherent === false ? pill('Chain stopped cohering', '--red') : '',
    r.narrativeGeneratedAt ? '' : pill('Narrative not generated yet', '--orange'),
    r.manualImageUrl ? pill('Manual image', '--teal') : '',
  ].filter(Boolean).join(' ');

  const audioLine = (r) => {
    if (!r.audio) return '<span class="muted">No narration audio yet.</span>';
    const kind = r.audio.isCurrent ? pill('Current narration', '--green') : pill('Older narration (previous voice)', '--orange');
    return `${kind} <span class="muted">${mmss(r.audio.durationSeconds)} · generated ${r.audio.generatedAt ? esc(fmt.dateTime(r.audio.generatedAt)) : 'at an unknown time'}</span>`;
  };

  const runLine = (n) => {
    if (!n || n.state === 'idle' || !n.state) return '';
    const at = n.at ? esc(fmt.dateTime(n.at)) : '';
    if (n.state === 'running') return `${pill('Generating…', '--teal', true)} <span class="muted">started ${at}. This takes a few minutes; the page updates itself.</span>`;
    if (n.state === 'failed') return `${pill('Last run failed', '--red')} ${esc(n.message)} <span class="muted">(${at})</span>`;
    return `<span class="muted">Last run: ${esc(n.message)} (${at})</span>`;
  };

  const narrateControl = (r, configured) => {
    const label = r.audio ? 'Regenerate narration' : 'Generate narration';
    if (r.narration.state === 'running') return ui.btn('Generating…', { size: 'sm', kind: 'line', attrs: 'disabled', ic: 'mic' });
    if (r.narrateBlocked) return `${ui.btn(label, { size: 'sm', kind: 'line', attrs: 'disabled', ic: 'mic' })} <span class="hint">(${esc(r.narrateBlocked)})</span>`;
    if (!configured) return `${ui.btn(label, { size: 'sm', kind: 'line', attrs: 'disabled', ic: 'mic' })} <span class="hint">(not configured on this server)</span>`;
    return ui.btn(label, { act: 'narrate', id: r.id, size: 'sm', kind: 'line', ic: 'mic' });
  };

  const rowCard = (r, configured) => `<div class="row" id="row-${r.id}" style="align-items:flex-start">
    <div class="body" style="display:flex;flex-direction:column;gap:8px">
      <b>${esc(r.label)}</b>
      <div class="chips">${flags(r)}</div>
      <div class="kv-inline">
        <span>anchor cluster ${ext(r.clusterUrl, String(r.anchorClusterId))}</span>
        <span>${fmt.plural(r.chainLength, 'story', 'stories')} in chain</span>
        <span>${fmt.plural(r.beatCount, 'beat')}</span>
        <span>${fmt.plural(r.viewCount, 'viewer')}</span>
        <span title="${esc(fmt.dateTime(r.pickedAt))}">added ${esc(fmt.dateTime(r.pickedAt))}</span>
        ${r.narrativeGeneratedAt ? `<span>narrative ${esc(fmt.rel(r.narrativeGeneratedAt))}</span>` : ''}
        ${r.updatedAt ? `<span>updated ${esc(fmt.rel(r.updatedAt))}</span>` : ''}
        <span>${ext(r.timelineUrl, 'API')}</span>
      </div>
      ${r.context ? `<details><summary class="hint" style="cursor:pointer">Context</summary><p class="note" style="margin-top:6px">${esc(r.context)}</p></details>` : ''}
      <div class="kv-inline"><span>Image: ${r.manualImageUrl ? 'manual override' : 'auto-selected'}</span><a class="link" href="${Admin.href('timelines/image/' + r.id)}">Choose image…</a></div>
      <div>Narration: ${audioLine(r)}</div>
      ${runLine(r.narration) ? `<div>${runLine(r.narration)}</div>` : ''}
      ${r.audio ? `<audio controls preload="none" src="${esc(r.audio.url)}"></audio>` : ''}
    </div>
    <div class="acts" style="flex-direction:column;align-items:flex-end">
      ${r.isEditorialPick
        ? ui.btn('Remove editorial pick', { act: 'pick', id: `${r.anchorClusterId}:unpick`, size: 'sm', kind: 'line' })
        : ui.btn('Make editorial pick', { act: 'pick', id: `${r.anchorClusterId}:pick`, size: 'sm', ic: 'plus' })}
      ${narrateControl(r, configured)}
    </div>
  </div>`;

  Admin.page({
    path: 'timelines',
    nav: 'timelines',
    title: 'Timelines',
    load: (p, q) => api.get('/timelines', { q: q.q }),
    render: (d, p, q) => {
      const picks = d.rows.filter((r) => r.isEditorialPick).length;
      const running = d.rows.filter((r) => r.narration.state === 'running').length;
      const results = d.search
        ? ui.st(ui.panel(`Search results for “${esc(d.search.q)}”`, d.search.results.length
          ? `<div class="list">${d.search.results.map((c) => `<div class="row"><div class="body"><b>${esc(c.headline)}</b><small>cluster ${ext(c.url, String(c.id))} · ${fmt.plural(c.sourceCount, 'source')}</small></div><div class="acts">${ui.btn('Make editorial pick', { act: 'pick', id: `${c.id}:pick`, size: 'sm', ic: 'plus' })}</div></div>`).join('')}</div>`
          : ui.empty('No unpicked clusters match.'),
          ui.btn('Clear search', { act: 'clearSearch', size: 'sm', kind: 'ghost' })))
        : '';
      return ui.head('Timelines', 'Timeline editorial picks',
        `Up to ${d.slots} slots show in the Timeline/Context tab. Editorial picks fill first; the generation script fills the rest by chain length × recency.`)
        + ui.st(`<div class="grid g-3">
          <div class="stat"><small>Timeline rows</small><b>${fmt.num(d.rows.length)}</b></div>
          <div class="stat"><small>Editorial picks</small><b>${fmt.num(picks)}</b></div>
          <div class="stat"><small>Narrations running</small><b>${fmt.num(running)}</b></div></div>`)
        + ui.st(ui.panel('Find a story to pick', `<form class="form-acts" id="tlSearch" style="flex-wrap:nowrap">
            <input class="inp" name="q" placeholder="Search by headline" value="${esc(q.q || '')}" autocomplete="off">
            ${ui.btn('Search', { ic: 'search', attrs: 'type="submit"' })}</form>
            ${d.configured ? '' : '<p class="hint" style="margin-top:10px">Narration isn\'t configured on this server (SARVAM_API_KEY / Supabase storage).</p>'}`))
        + results
        + ui.st(ui.panel('Current rows', d.rows.length
          ? `<div class="list">${d.rows.map((r) => rowCard(r, d.configured)).join('')}</div>`
          : ui.empty('No timeline rows yet. Pick a story above, or wait for the generation script.')));
    },
    mount: (ctx) => {
      const form = ctx.root.querySelector('#tlSearch');
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const q = form.elements.q.value.trim();
        Admin.go('timelines' + (q ? '?q=' + encodeURIComponent(q) : ''));
      });
      ctx.actions({
        clearSearch: () => Admin.go('timelines'),
        pick: async (el) => {
          const [clusterId, action] = el.dataset.id.split(':');
          await api.post('/timelines/update', { clusterId: Number(clusterId), action });
          Admin.toast(action === 'pick' ? 'Editorial pick added' : 'Editorial pick removed', action === 'pick' ? '--teal' : '--muted');
          ctx.reload();
        },
        narrate: async (el) => {
          const row = ctx.data.rows.find((r) => String(r.id) === el.dataset.id);
          const ok = await Admin.confirm({
            title: `${row && row.audio ? 'Regenerate' : 'Generate'} narration for “${row ? row.label : 'this timeline'}”?`,
            text: esc(ctx.data.narrateConfirm),
            ok: 'Generate narration',
          });
          if (!ok) return;
          await api.post('/timelines/narrate', { rowId: Number(el.dataset.id) });
          Admin.toast('Narration started. It takes a few minutes.', '--teal');
          ctx.reload();
        },
      });
      // A run finishes minutes later on whichever worker started it; poll the
      // running rows and reload once any of them settles.
      const running = ctx.data.rows.filter((r) => r.narration.state === 'running').map((r) => r.id);
      if (running.length) {
        ctx.every(15000, async () => {
          try {
            const states = await Promise.all(running.map((id) => api.get(`/timelines/narrate/${id}`)));
            const settled = states.filter((s) => s.state !== 'running');
            if (!settled.length) return;
            settled.forEach((s) => Admin.toast(s.state === 'failed' ? `Narration failed: ${s.message}` : 'Narration finished', s.state === 'failed' ? '--red' : '--green'));
            ctx.reload();
          } catch (e) { /* transient; try again next tick */ }
        });
      }
    },
  });

  Admin.page({
    path: 'timelines/image/:rowId',
    nav: 'timelines',
    title: (d) => (d ? `Image · ${d.label}` : 'Choose image'),
    load: (p) => api.get(`/timelines/image/${encodeURIComponent(p.rowId)}`),
    render: (d) => {
      const status = d.manualImageUrl
        ? `<div class="form-acts">${pill('Manual override active', '--teal', true)}${ui.btn('Clear override (use auto-select)', { act: 'clear', size: 'sm', kind: 'line', ic: 'restore' })}</div>`
        : '<p class="note">No override set. The app is auto-selecting (HD first, then most recent).</p>';
      const grid = d.candidates.length
        ? `<div class="thumbs">${d.candidates.map((c, i) => `<button class="thumb-pick${c.current ? ' on' : ''}" ${c.current ? '' : 'data-act="use"'} data-id="${i}" ${c.current ? 'aria-current="true"' : ''} title="${c.current ? 'Currently selected' : 'Use this image'}">
            <img class="thumb" src="${esc(c.imageUrl)}" loading="lazy" alt="">
            <small><b>${esc(c.source)}</b>${c.hd ? ' · HD' : ''}</small>
            <small>${esc(fmt.dateTime(c.publishedAt))}</small>
            <small>${c.current ? '<b>Currently selected</b>' : 'Use this image'}</small></button>`).join('')}</div>`
        : ui.empty("No article images found across this timeline's chain.");
      return ui.head('Timelines', `Choose image: ${esc(d.label)}`,
        'Override the auto-selected lead image (list hero and detail cover) with any photo carried by an article in this timeline\'s chain.',
        `<a class="btn btn-line" href="${Admin.href('timelines')}#row-${d.id}">← Back to timelines</a>`)
        + ui.st(ui.panel('', status))
        + ui.st(ui.panel(`Available images (${d.candidates.length})`, grid));
    },
    mount: (ctx) => {
      const id = ctx.data.id;
      ctx.actions({
        use: async (el) => {
          const c = ctx.data.candidates[Number(el.dataset.id)];
          await api.post(`/timelines/image/${id}`, { imageUrl: c.imageUrl });
          Admin.toast('Lead image set', '--teal');
          ctx.reload();
        },
        clear: async () => {
          await api.post(`/timelines/image/${id}/clear`);
          Admin.toast('Override cleared. Auto-selecting again.', '--muted');
          ctx.reload();
        },
      });
    },
  });
})();
