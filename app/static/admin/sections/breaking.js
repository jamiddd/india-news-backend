/* Breaking: human review of the pinned Breaking slot + the lead-image picker. API: app/admin_breaking.py */
(() => {
  const { ui, api } = Admin;
  const { esc, fmt, pill } = ui;

  const clusterLink = (id, url, label) =>
    `<a class="link" href="${esc(Admin.publicUrl(url))}" target="_blank" rel="noopener">${esc(label || 'cluster ' + id)}</a>`;

  const articleList = (item) => {
    if (!item.articles.length) return `<p class="note">No articles.</p>`;
    const rows = item.articles.map((a) => `<div class="row" style="padding:9px 2px;align-items:flex-start">
        <span class="mono muted" style="font-size:12px;min-width:104px;padding-top:2px">${esc(fmt.day(a.publishedAt))} ${esc(fmt.time(a.publishedAt))}</span>
        <div class="body"><b style="font-weight:600">${esc(a.title)}</b><small>${esc(a.sourceName)}</small></div></div>`).join('');
    const more = item.omitted ? `<p class="hint" style="margin:6px 2px 0">…and ${fmt.plural(item.omitted, 'more article')}</p>` : '';
    return `<div class="list">${rows}</div>${more}`;
  };

  const sourceChips = (sources) => sources.length
    ? `<div class="chips">${sources.map((s) => `<span class="chip" style="cursor:default">${esc(s)}</span>`).join('')}</div>`
    : '';

  const candidateCard = (c) => ui.panel(
    esc(c.headline),
    `<div style="display:flex;flex-direction:column;gap:14px">
      <div class="kv-inline"><span>${clusterLink(c.clusterId, c.url)}</span><span>${fmt.plural(c.sourcesAtPromotion, 'source')} at promotion</span>
        <span>crossed the multi-source threshold in ${esc(c.hoursToThreshold)} h</span><span>raised ${esc(fmt.rel(c.promotedAt))}</span>
        <span>${fmt.plural(c.articleCount, 'article')}</span></div>
      ${sourceChips(c.sources)}
      ${articleList(c)}
      <div class="form-acts">
        ${ui.btn('Genuinely developing: write narrative', { act: 'candidate', id: `${c.clusterId}:approve`, ic: 'check' })}
        ${ui.btn('Just echo: reject', { act: 'candidate', id: `${c.clusterId}:reject`, kind: 'line', ic: 'x' })}
      </div></div>`,
    pill('Candidate', '--red'));

  const refreshCard = (r) => ui.panel(
    esc(r.title || r.headline),
    `<div style="display:flex;flex-direction:column;gap:14px">
      <div class="kv-inline"><span>${clusterLink(r.clusterId, r.url)}</span>
        <span><b style="color:var(--ink)">${fmt.num(r.sourceCount)}</b> sources now, was ${fmt.num(r.previousSourceCount)} at last review</span>
        <span>raised ${esc(fmt.rel(r.createdAt))}</span>${r.lastGeneratedAt ? `<span>narrative last written ${esc(fmt.rel(r.lastGeneratedAt))}</span>` : ''}</div>
      <div><p class="hint" style="margin:0 0 6px">Existing beats (${r.beats.length})</p>
        ${r.beats.length ? `<div class="list">${r.beats.map((b) => `<div class="row" style="padding:9px 2px;align-items:flex-start">
          <span class="mono muted" style="font-size:12px;min-width:64px;padding-top:2px">${esc(b.timeLabel)}</span>
          <div class="body"><b style="font-weight:600">${esc(b.label)}</b><small>${esc(b.narration)}</small></div></div>`).join('')}</div>` : '<p class="note">No beats yet.</p>'}</div>
      <div><p class="hint" style="margin:0 0 6px">New articles since the last narrative (${fmt.num(r.articleCount)})</p>
        ${sourceChips(r.sources)}<div style="margin-top:8px">${articleList(r)}</div></div>
      <div class="form-acts">
        ${ui.btn('Genuinely new: extend narrative', { act: 'refresh', id: `${r.id}:approve`, ic: 'check' })}
        ${ui.btn('Just echo: skip', { act: 'refresh', id: `${r.id}:reject`, kind: 'line', ic: 'x' })}
      </div></div>`,
    pill('Refresh', '--orange'));

  const liveRow = (s) => `<div class="row">
      <span class="ico-sq" style="--c:var(--red)">${ui.icon('breaking')}</span>
      <div class="body"><b>${esc(s.title)}</b>
        <small>${clusterLink(s.clusterId, s.url)} · ${fmt.plural(s.beatCount, 'beat')} · promoted ${esc(fmt.dateTime(s.promotedAt))}${s.lastBeatAt ? ` · last moved ${esc(fmt.rel(s.lastBeatAt))}` : ''}</small></div>
      <div class="acts">${s.manualImageUrl ? pill('Manual image', '--red') : pill('Auto image')}
        <a class="btn btn-line btn-sm" href="${Admin.href('breaking/image/' + s.clusterId)}">${ui.icon('image')}Choose image</a></div></div>`;

  Admin.page({
    path: 'breaking',
    nav: 'breaking',
    title: 'Breaking review',
    load: () => api.get('/breaking'),
    render: (d) => {
      const waiting = d.candidates.length + d.refreshes.length;
      return ui.head('Breaking', waiting ? `${fmt.plural(waiting, 'decision')} waiting.` : 'Nothing waiting.',
        'Approving runs the LLM narrative pass and puts the story in the app’s pinned Breaking slot; rejecting costs nothing. Up to ' +
        `${d.previewLimit} articles are shown per item. See backend/docs/breaking-human-review-plan.md.`)
        + ui.st(`<div class="grid g-3">
            <div class="stat"><small>New candidates</small><b>${fmt.num(d.candidates.length)}</b></div>
            <div class="stat"><small>Refreshes</small><b>${fmt.num(d.refreshes.length)}</b></div>
            <div class="stat"><small>Live now</small><b>${fmt.num(d.live.length)}</b></div></div>`)
        + ui.st(`<h2 style="margin:6px 0 12px">New candidates</h2>${d.candidates.length
          ? `<div style="display:flex;flex-direction:column;gap:16px">${d.candidates.map(candidateCard).join('')}</div>`
          : ui.panel('', ui.empty('No candidates waiting. The detector raises new ones as stories cross the source threshold.'))}`)
        + ui.st(`<h2 style="margin:6px 0 12px">Refreshes</h2>${d.refreshes.length
          ? `<div style="display:flex;flex-direction:column;gap:16px">${d.refreshes.map(refreshCard).join('')}</div>`
          : ui.panel('', ui.empty('No refreshes waiting. One appears when a live story picks up enough new sources.'))}`)
        + ui.st(ui.panel(`Live stories (${d.live.length})`,
          d.live.length ? `<div class="list">${d.live.map(liveRow).join('')}</div>` : ui.empty('No stories are live in the Breaking slot.')));
    },
    mount: (ctx) => {
      const queueKey = (d) => [...d.candidates.map((c) => 'c' + c.clusterId), ...d.refreshes.map((r) => 'r' + r.id)].sort().join(',');
      const shown = queueKey(ctx.data);
      ctx.every(60000, async () => {
        try {
          const d = await api.get('/breaking');
          if (queueKey(d) !== shown) { Admin.toast('The review queue changed.', '--red'); Admin.refreshBadges(); ctx.reload(); }
        } catch (e) { /* next tick retries */ }
      });
      const done = () => { Admin.refreshBadges(); ctx.reload(); };
      ctx.actions({
        candidate: async (el) => {
          const [clusterId, action] = el.dataset.id.split(':');
          const c = ctx.data.candidates.find((x) => String(x.clusterId) === clusterId);
          if (action === 'approve') {
            const ok = await Admin.confirm({
              title: 'Write the narrative and go live?',
              text: `<b>${esc(c ? c.headline : 'This story')}</b> gets an LLM-written narrative and is pinned in the app’s Breaking slot. This takes a few seconds.`,
              ok: 'Approve and go live',
            });
            if (!ok) return;
          }
          const r = await api.post(`/breaking/candidates/${clusterId}`, { action });
          if (r.status === 'active') Admin.toast(`Live in Breaking with ${fmt.plural(r.beatCount, 'beat')}`, '--green');
          else if (action === 'approve') Admin.toast('The model found nothing citable, so the candidate was rejected', '--amber');
          else Admin.toast('Candidate rejected', '--muted');
          done();
        },
        refresh: async (el) => {
          const [id, action] = el.dataset.id.split(':');
          if (action === 'approve') {
            const ok = await Admin.confirm({
              title: 'Extend the live narrative?',
              text: 'The LLM writes new beats from the new articles and appends them to the story readers see now.',
              ok: 'Approve and extend',
            });
            if (!ok) return;
          }
          const r = await api.post(`/breaking/refreshes/${id}`, { action });
          if (r.status === 'approved') Admin.toast(r.newBeats ? `${fmt.plural(r.newBeats, 'new beat')} added` : 'Approved, but the model added no new beats', r.newBeats ? '--green' : '--amber');
          else if (r.reason === 'no_new_articles') Admin.toast('No new articles since the last narrative, so the refresh was skipped', '--amber');
          else Admin.toast('Refresh skipped', '--muted');
          done();
        },
      });
    },
  });

  Admin.page({
    path: 'breaking/image/:clusterId',
    nav: 'breaking',
    title: (d) => (d ? `Image · ${d.title}` : 'Choose image'),
    load: (p) => api.get(`/breaking/image/${encodeURIComponent(p.clusterId)}`),
    render: (d) => {
      const back = `<a class="btn btn-line" href="${Admin.href('breaking')}">Back to Breaking</a>`;
      const clear = d.manualImageUrl ? ui.btn('Clear override, use auto-select', { act: 'clear', kind: 'ink', ic: 'restore' }) : '';
      return ui.head('Breaking · lead image', esc(d.title),
        `Pick the photo shown on the feed card and the story’s hero. Only images carried by articles on ${clusterLink(d.clusterId, d.url)} are offered, best (HD, newest) first.`,
        back + clear)
        + ui.st(ui.panel(`Available images (${d.images.length})`,
          d.images.length ? `<div class="thumbs">${d.images.map((im) => `<button class="thumb-pick${im.current ? ' on' : ''}" data-act="pick" data-url="${esc(im.url)}"${im.current ? ' aria-pressed="true"' : ''}>
              <img class="thumb" src="${esc(im.url)}" loading="lazy" alt="">
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${im.current ? pill('In use', '--red', true) : ''}${im.hd ? pill('HD', '--green') : ''}</div>
              <small><b style="color:var(--ink)">${esc(im.sourceName)}</b><br>${esc(fmt.dateTime(im.publishedAt))}</small></button>`).join('')}</div>`
            : ui.empty('No article on this story carries an image.'),
          d.manualImageUrl ? pill('Manual override active', '--red') : pill('Auto-selecting')));
    },
    mount: (ctx) => {
      const id = ctx.data.clusterId;
      ctx.actions({
        pick: async (el) => {
          if (el.classList.contains('on')) return Admin.toast('That image is already in use', '--muted');
          await api.post(`/breaking/image/${id}`, { imageUrl: el.dataset.url });
          Admin.toast('Lead image updated in the app');
          ctx.reload();
        },
        clear: async () => {
          await api.del(`/breaking/image/${id}`);
          Admin.toast('Override cleared, auto-selecting again');
          ctx.reload();
        },
      });
    },
  });
})();
